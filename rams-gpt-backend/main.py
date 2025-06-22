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
from openai import AsyncOpenAI
from docx import Document

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str
    template_path: str
    class Config:
        env_file = ".env"

try:
    settings = Settings()
except ValidationError as e:
    logger.error("Config validation failed: %s", e)
    raise

# Configure OpenAI client (correct v1.x+ usage)
client = AsyncOpenAI(api_key=settings.openai_api_key)
OPENAI_MODEL = settings.openai_model

# FastAPI setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Session store
sessions = {}
sessions_lock = asyncio.Lock()
SESSION_TTL = 3600  # 1 hour

def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in sessions.items() if now - s.get("last_active", 0) > SESSION_TTL]
    for sid in expired:
        sessions.pop(sid, None)
    if expired:
        logger.info("Cleaned up %d expired sessions.", len(expired))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/rams", response_class=HTMLResponse)
async def rams_page(request: Request):
    return templates.TemplateResponse("rams_chat.html", {"request": request})

@app.post("/rams_chat/start")
async def start_rams(request: Request, task: str = Body(..., embed=True)):
    async with sessions_lock:
        cleanup_sessions()

    if not task.strip():
        raise HTTPException(status_code=400, detail="Task description cannot be empty.")

    session_id = str(uuid.uuid4())

    system_prompt = (
        "You are an expert in writing construction RAMS. "
        "Generate exactly 20 numbered and detailed questions needed to complete a RAMS document "
        "for the following task. Include questions on scope, location, tools, training, PPE, rescue, environment, etc."
    )
    user_prompt = f"Task: {task.strip()}"

    try:
        gpt_response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0
        )
        raw = gpt_response.choices[0].message.content.strip()
        lines = raw.splitlines()
        questions = [line.strip() for line in lines if line.strip()]
        if len(questions) > 20:
            questions = questions[:20]
    except Exception:
        logger.exception("OpenAI question generation failed")
        raise HTTPException(status_code=500, detail="Failed to generate questions.")

    if len(questions) < 20:
        raise HTTPException(status_code=500, detail="OpenAI returned fewer than 20 questions.")

    sessions[session_id] = {
        "task": task,
        "questions": questions,
        "answers": [],
        "last_active": time.time()
    }

    return JSONResponse({"session_id": session_id, "questions": questions})

@app.post("/rams_chat/answer")
async def answer_rams(request: Request, answer: str = Body(..., embed=True)):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No active session.")

    async with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=400, detail="Session expired or not found.")
        if not answer.strip():
            raise HTTPException(status_code=400, detail="Answer cannot be empty.")

        session["answers"].append(answer.strip())
        session["last_active"] = time.time()

        if len(session["answers"]) >= len(session["questions"]):
            return {"complete": True}

        next_q = session["questions"][len(session["answers"])]
        return {"complete": False, "next_question": next_q}

@app.get("/rams_chat/generate")
async def generate_doc(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No active session.")

    async with sessions_lock:
        session = sessions.get(session_id)
        if not session or len(session["answers"]) < len(session["questions"]):
            raise HTTPException(status_code=400, detail="Incomplete session.")

    qa_block = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(zip(session["questions"], session["answers"]))])

    prompts = {
        "RISK_SECTION": "Write the Risk Assessment section based on this task and these answers:\n" + qa_block,
        "SEQUENCE_SECTION": "Write the full Sequence of Activities for the RAMS from this info:\n" + qa_block,
        "METHOD_SECTION": "Write a full Method Statement using the Q&As including scope, roles, PPE, rescue, CESWI refs:\n" + qa_block
    }

    try:
        results = await asyncio.gather(*[
            client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a RAMS writing expert."},
                    {"role": "user", "content": text}
                ],
                temperature=0.0
            ) for text in prompts.values()
        ])
        sections = {key: results[i].choices[0].message.content.strip() for i, key in enumerate(prompts)}
    except Exception:
        logger.exception("Failed to generate RAMS sections")
        raise HTTPException(status_code=500, detail="Error generating RAMS content.")

    try:
        doc = await asyncio.get_running_loop().run_in_executor(None, Document, settings.template_path)
    except Exception:
        logger.exception("Failed to load Word template")
        raise HTTPException(status_code=500, detail="Document template failed to load.")

    for p in doc.paragraphs:
        for tag, text in sections.items():
            if tag in p.text:
                p.text = p.text.replace(tag, text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for tag, text in sections.items():
                    if tag in cell.text:
                        cell.text = cell.text.replace(tag, text)

    buffer = BytesIO()
    await asyncio.get_running_loop().run_in_executor(None, doc.save, buffer)
    buffer.seek(0)

    async with sessions_lock:
        sessions.pop(session_id, None)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=RAMS_Document.docx"}
    )

