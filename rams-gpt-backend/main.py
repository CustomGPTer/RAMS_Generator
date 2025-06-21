import os
import time
import uuid
import asyncio
import logging
from io import BytesIO
from fastapi import FastAPI, Request, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseSettings, ValidationError
import openai
from docx import Document

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration from environment variables using Pydantic BaseSettings
class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str
    template_path: str
    class Config:
        env_file = ".env"

try:
    settings = Settings()
except ValidationError as e:
    logger.error(f"Configuration error: {e}")
    # If running via Uvicorn, we rethrow to stop application start
    raise

# Securely set OpenAI API key and model
openai.api_key = settings.openai_api_key
OPENAI_MODEL = settings.openai_model

# Initialize FastAPI app, Jinja2 templates, and static files
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory session store and lock for thread-safety
sessions = {}
sessions_lock = asyncio.Lock()
SESSION_TTL = 60 * 60  # 1 hour (in seconds) for session expiration

def cleanup_sessions():
    """Remove expired sessions from the in-memory store."""
    now = time.time()
    to_delete = [sid for sid, data in sessions.items() if now - data.get("last_active", 0) > SESSION_TTL]
    for sid in to_delete:
        sessions.pop(sid, None)
    if to_delete:
        logger.info(f"Cleaned up {len(to_delete)} expired sessions.")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the homepage."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/rams", response_class=HTMLResponse)
async def rams_page(request: Request):
    """Serve the RAMS chat interface page."""
    return templates.TemplateResponse("rams_chat.html", {"request": request})

@app.post("/rams_chat/start")
async def start_rams(request: Request, task: str = Body(..., embed=True)):
    """Start a new RAMS session: generate 20 questions based on the task description."""
    # Clean up any expired sessions
    async with sessions_lock:
        cleanup_sessions()
    # Validate task input
    if not task or task.strip() == "":
        raise HTTPException(status_code=400, detail="Task description cannot be empty.")
    task = task.strip()
    # If user already had a session, discard it to start fresh
    old_session_id = request.cookies.get("session_id")
    if old_session_id:
        async with sessions_lock:
            if old_session_id in sessions:
                sessions.pop(old_session_id, None)
                logger.info(f"Discarded existing session {old_session_id} for new start.")
    # Create a new session ID
    session_id = str(uuid.uuid4())
    # Prepare the prompt for OpenAI to generate questions
    system_prompt = (
        "You are an expert in creating Risk Assessment and Method Statement (RAMS) documents. "
        "The user will provide a description of a task. Based on this task, generate a list of 20 specific questions "
        "that need to be answered in order to create a comprehensive Risk Assessment and Method Statement for the task. "
        "Cover all relevant aspects such as scope, personnel, location, hazards, control measures, tools, PPE, training requirements, and emergency procedures. "
        "Provide the questions as a numbered list (1 to 20)."
    )
    user_prompt = f"Task description: {task}\nGenerate 20 questions."
    # Call OpenAI API to generate questions
    try:
        openai_response = await openai.ChatCompletion.acreate(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0
        )
    except Exception as e:
        logger.exception("OpenAI question generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate questions. Please try again.")
    # Extract and parse the questions from the OpenAI response
    content = ""
    if openai_response and openai_response.choices:
        content = openai_response.choices[0].message.content.strip()
    questions = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Remove any leading numbering or bullet from the line
        if line[0].isdigit():
            if "." in line:
                parts = line.split(".", 1)
                question_text = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            elif ")" in line:
                parts = line.split(")", 1)
                question_text = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            else:
                question_text = line.lstrip("0123456789").strip()
        elif line[0] in ("-", "*"):
            question_text = line[1:].strip()
        else:
            question_text = line
        if question_text:
            questions.append(question_text)
    # Validate that we have 20 questions
    if len(questions) < 20:
        logger.error(f"Expected 20 questions, but got {len(questions)}: {questions}")
        raise HTTPException(status_code=500, detail="Failed to generate the required number of questions.")
    if len(questions) > 20:
        questions = questions[:20]
    # Store session data
    session_data = {
        "questions": questions,
        "answers": [],
        "task": task,
        "last_active": time.time()
    }
    async with sessions_lock:
        sessions[session_id] = session_data
    # Return the first question and set session cookie
    response = JSONResponse(content={"question": questions[0]})
    response.set_cookie(key="session_id", value=session_id, httponly=True)
    return response

@app.post("/rams_chat/answer")
async def answer_rams(request: Request, answer: str = Body(..., embed=True)):
    """Store an answer for the current question and return the next question."""
    # Retrieve session
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No active session. Please start a new session.")
    async with sessions_lock:
        session_data = sessions.get(session_id)
        if not session_data:
            # Session expired or not found
            raise HTTPException(status_code=400, detail="Session not found or expired. Please start again.")
        # Validate answer
        if not answer or answer.strip() == "":
            raise HTTPException(status_code=400, detail="Answer cannot be empty.")
        answer_text = answer.strip()
        answers_list = session_data["answers"]
        questions_list = session_data["questions"]
        if len(answers_list) >= len(questions_list):
            raise HTTPException(status_code=400, detail="All questions have already been answered for this session.")
        # Store the answer and update session
        answers_list.append(answer_text)
        session_data["last_active"] = time.time()
        sessions[session_id] = session_data
        # Determine next step
        if len(answers_list) < len(questions_list):
            next_question = questions_list[len(answers_list)]
            # Return the next question
            return {"question": next_question}
        else:
            # All questions answered
            return {"message": "All questions answered. Ready to generate document."}

@app.get("/rams_chat/generate")
async def generate_rams(request: Request):
    """Generate the final RAMS Word document based on all answers."""
    # Clean up expired sessions (if any lingering)
    async with sessions_lock:
        cleanup_sessions()
    # Retrieve session
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No active session to generate document.")
    async with sessions_lock:
        session_data = sessions.get(session_id)
    if not session_data:
        raise HTTPException(status_code=400, detail="Session not found or expired.")
    questions = session_data["questions"]
    answers = session_data["answers"]
    if len(answers) < len(questions):
        raise HTTPException(status_code=400, detail="Not all questions have been answered.")
    # Prepare Q&A content for prompts
    qa_text = ""
    for i, (q, a) in enumerate(zip(questions, answers), start=1):
        qa_text += f"Q{i}: {q}\nA{i}: {a}\n"
    # Prepare prompts for each section
    system_prompt_final = "You are a health and safety expert writing a Risk Assessment and Method Statement."
    user_prompt_risk = (
        "Using the provided task information (questions and answers), write the Risk Assessment section of the RAMS document. "
        "Focus on the hazards, risks, and control measures specific to the task.\n" + qa_text
    )
    user_prompt_sequence = (
        "Using the provided information, write a step-by-step Sequence of Work for the task as part of the RAMS document. "
        "List the steps in a logical order to safely complete the task.\n" + qa_text
    )
    user_prompt_method = (
        "Using the provided information, write the Method Statement section for the task in the RAMS document. "
        "Include details of how the work will be carried out safely, including roles/responsibilities and any other relevant details.\n" + qa_text
    )
    # Call OpenAI API for each section
    try:
        risk_resp = await openai.ChatCompletion.acreate(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt_final},
                {"role": "user", "content": user_prompt_risk}
            ],
            temperature=0.0
        )
        seq_resp = await openai.ChatCompletion.acreate(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt_final},
                {"role": "user", "content": user_prompt_sequence}
            ],
            temperature=0.0
        )
        method_resp = await openai.ChatCompletion.acreate(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt_final},
                {"role": "user", "content": user_prompt_method}
            ],
            temperature=0.0
        )
    except Exception as e:
        logger.exception("OpenAI final content generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate document content. Please try again.")
    # Extract generated text for each section
    risk_text = risk_resp.choices[0].message.content.strip() if risk_resp and risk_resp.choices else ""
    seq_text = seq_resp.choices[0].message.content.strip() if seq_resp and seq_resp.choices else ""
    method_text = method_resp.choices[0].message.content.strip() if method_resp and method_resp.choices else ""
    # Verify content was generated
    if not risk_text or not seq_text or not method_text:
        logger.error("One or more sections returned empty content from OpenAI.")
        raise HTTPException(status_code=500, detail="Failed to generate some sections of the document.")
    # Load the Word template
    try:
        loop = asyncio.get_running_loop()
        doc: Document = await loop.run_in_executor(None, Document, settings.template_path)
    except Exception as e:
        logger.exception("Failed to open template document")
        raise HTTPException(status_code=500, detail="Failed to open template document.")
    # Replace placeholder text in the template with generated content
    placeholders = {
        "RISK_SECTION": risk_text,
        "SEQUENCE_SECTION": seq_text,
        "METHOD_SECTION": method_text
    }
    try:
        # Replace in all paragraphs
        for paragraph in doc.paragraphs:
            for placeholder, new_text in placeholders.items():
                if placeholder in paragraph.text:
                    paragraph.text = paragraph.text.replace(placeholder, new_text)
        # Replace in table cells (if the template has tables with placeholders)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for placeholder, new_text in placeholders.items():
                        if placeholder in cell.text:
                            cell.text = cell.text.replace(placeholder, new_text)
    except Exception as e:
        logger.exception("Failed to insert content into the document template")
        raise HTTPException(status_code=500, detail="Failed to insert content into template.")
    # Save the modified document to a bytes buffer
    try:
        output_buffer = BytesIO()
        await loop.run_in_executor(None, doc.save, output_buffer)
        output_buffer.seek(0)
    except Exception as e:
        logger.exception("Failed to save the generated document")
        raise HTTPException(status_code=500, detail="Failed to generate document file.")
    # Remove session data now that document is generated
    async with sessions_lock:
        sessions.pop(session_id, None)
    # Return the Word document as a downloadable file
    filename = "RAMS_Document.docx"
    return StreamingResponse(
        output_buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )