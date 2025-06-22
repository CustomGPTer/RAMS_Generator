import os
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError
from docx import Document

# Load environment variables from .env file if present
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Mount static files directory if exists (for CSS/JS if any)
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Set up Jinja2 templates directory
templates = Jinja2Templates(directory="templates")

# Get OpenAI API key from environment and initialize AsyncOpenAI client
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY environment variable not set.")
openai_client = AsyncOpenAI(api_key=openai_api_key)

# Session data structure: holds questions list, answers list, etc.
sessions = {}
# Session expiration duration
SESSION_TIMEOUT = timedelta(hours=1)

# Utility function to clean up expired sessions
def cleanup_sessions():
    now = datetime.utcnow()
    expired_keys = [sid for sid, data in sessions.items() if now - data["last_access"] > SESSION_TIMEOUT]
    for sid in expired_keys:
        sessions.pop(sid, None)
        # Remove any generated document for this session
        output_file = f"templates/RAMS_Output_{sid}.docx"
        try:
            if os.path.exists(output_file):
                os.remove(output_file)
        except OSError:
            pass

# Landing page route - serves index.html
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# RAMS Generator interface page - serves rams_chat.html and initiates a session
@app.get("/rams", response_class=HTMLResponse)
async def serve_rams(request: Request):
    # Create a new session ID
    session_id = str(uuid.uuid4())
    cleanup_sessions()  # clean old sessions
    # Initialize session data
    sessions[session_id] = {
        "created": datetime.utcnow(),
        "last_access": datetime.utcnow(),
        "questions": None,
        "answers": [],
        "current_index": -1  # no question asked yet
    }
    # Render the chat interface with the session_id
    return templates.TemplateResponse("rams_chat.html", {"request": request, "session_id": session_id})

# API endpoint to handle generating questions and receiving answers
@app.post("/api/submit")
async def handle_submit(request: Request):
    data = await request.json()
    session_id = data.get("session_id")
    user_input = data.get("message", "").strip()
    if not session_id or session_id not in sessions:
        # Invalid or missing session
        raise HTTPException(status_code=400, detail="Invalid session.")
    session = sessions[session_id]
    # Update last access time
    session["last_access"] = datetime.utcnow()
    cleanup_sessions()
    # Check for session expiration
    if datetime.utcnow() - session["created"] > SESSION_TIMEOUT:
        # Session expired
        sessions.pop(session_id, None)
        return JSONResponse({"error": "Session expired. Please start a new session."}, status_code=440)
    # If no questions generated yet, the user_input is the task description
    if session["questions"] is None:
        if not user_input:
            return JSONResponse({"error": "Task description cannot be empty."}, status_code=400)
        # Use OpenAI API to generate 20 tailored questions
        try:
            prompt = (
                "You are an expert in health and safety. You will be provided with a description of a task. "
                "Generate a numbered list of 20 specific questions to ask in order to gather all information needed to create a comprehensive Risk Assessment and Method Statement for that task. "
                f"Task description: \"{user_input}\""
            )
            response = await openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}]
            )
            questions_text = response.choices[0].message.content.strip()
        except OpenAIError as e:
            # Return an error message if the OpenAI API call fails
            return JSONResponse({"error": f"Failed to generate questions: {str(e)}"}, status_code=500)
        # Split the response into individual questions (expecting a numbered list)
        questions = []
        for line in questions_text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Remove leading numbering or bullets (e.g., "1.", "1)", "1-" etc.)
            line = line.lstrip("0123456789.):- ").strip()
            if line:
                questions.append(line)
        # Ensure exactly 20 questions in the list
        if len(questions) >= 20:
            questions = questions[:20]
        else:
            questions += ["(Question not generated)"] * (20 - len(questions))
        session["questions"] = questions
        session["current_index"] = 0  # index of current question awaiting answer
        # Return the first question
        return {"question": questions[0]}
    else:
        # Questions are already generated, so this input is an answer to the current question
        idx = session["current_index"]
        if idx is None or idx < 0 or session["questions"] is None:
            return JSONResponse({"error": "No active questions. Please start over."}, status_code=400)
        # Store the user's answer
        session["answers"].append(user_input)
        # Move to next question index
        session["current_index"] += 1
        idx = session["current_index"]
        if idx < len(session["questions"]):
            # There are more questions to ask
            next_question = session["questions"][idx]
            return {"question": next_question}
        else:
            # All questions have been answered, generate the RAMS document
            answers = session["answers"]
            total = len(answers)
            # Split answers into three sections (Risk, Sequence, Method) roughly equally
            part1 = answers[: total//3 if total//3 > 0 else total]
            part2 = answers[total//3 : 2*total//3 if 2*total//3 > 0 else total]
            part3 = answers[2*total//3 :]
            risk_section_text = "\n".join(part1).strip()
            sequence_section_text = "\n".join(part2).strip()
            method_section_text = "\n".join(part3).strip()
            # Load the Word template
            try:
                doc = Document("templates/template_rams.docx")
            except Exception as e:
                return JSONResponse({"error": f"Failed to load template: {e}"}, status_code=500)
            # Replace placeholders in the document
            def replace_in_element(element, placeholder, replacement):
                """Recursively replace placeholder text in all paragraphs within the given element."""
                for paragraph in element.paragraphs:
                    if placeholder in paragraph.text:
                        for run in paragraph.runs:
                            if placeholder in run.text:
                                run.text = run.text.replace(placeholder, replacement)
                if hasattr(element, "tables"):
                    for table in element.tables:
                        for row in table.rows:
                            for cell in row.cells:
                                replace_in_element(cell, placeholder, replacement)
            replace_in_element(doc, "RISK_SECTION", risk_section_text)
            replace_in_element(doc, "SEQUENCE_SECTION", sequence_section_text)
            replace_in_element(doc, "METHOD_SECTION", method_section_text)
            # Save the filled document to a file
            output_path = f"templates/RAMS_Output_{session_id}.docx"
            try:
                doc.save(output_path)
            except Exception as e:
                return JSONResponse({"error": f"Failed to save output document: {e}"}, status_code=500)
            # Signal completion (client will download the file via /download)
            return {"done": True}

# Endpoint to download the generated RAMS document
@app.get("/download")
async def download_rams(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=400, detail="Invalid or expired session.")
    file_path = f"templates/RAMS_Output_{session_id}.docx"
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Generated document not found.")
    return FileResponse(file_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename="RAMS_Output.docx")




