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

# Configure OpenAI
openai.api_key = settings.openai_api_key
OPENAI_MODEL = settings.openai_model

# FastAPI setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Session storage
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

    system_msg = (
        "You are an expert in Risk Assessment and Method Statement (RAMS). "
        "Given a construction task, generate exactly 20 questions covering scope, PPE, methods, rescue plan, tools, training, etc."
    )
    user_msg = f"Task: {task.strip()}. Generate 20 numbered questions."

    try:
        gpt = await openai.ChatCompletion.acreate(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0
        )
        lines = gpt.choices[0].message.content.strip().splitlines()
        questions = []
        for i, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            if not line.strip().startswith(str(i)):
                line = f"{i}. {line.strip()}"
            questions.append(line.strip())
    except Exception as e:
        logger.exception("Failed to generate questions")
        raise HTTPException(status_code=500, detail="Failed to generate questions.")

    if len(questions) < 20:
        raise HTTPException(status_code=500, detail="Expected 20 questions from OpenAI.")

    sessions[session_id] = {
        "task": task,
        "questions": questions[:20],
        "answers": [],
        "last_active": time.time()
    }

    return JSONResponse(content={"session_id": session_id, "questions": questions[:20]})

@app.post("/rams_chat/answer")
async def answer_rams(request: Request, answer: str = Body(..., embed=True)):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No session.")

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

        next_question = session["questions"][len(session["answers"])]
        return {"complete": False, "next_question": next_question}

@app.get("/rams_chat/generate")
async def generate_doc(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="No session to generate document.")

    async with sessions_lock:
        session = sessions.get(session_id)
        if not session or len(session["answers"]) < len(session["questions"]):
            raise HTTPException(status_code=400, detail="Incomplete session.")

    qa_pairs = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(zip(session["questions"], session["answers"]))])

    prompts = {
        "RISK_SECTION": (
            "You are a RAMS expert. Write the Risk Assessment section focusing on hazards, risks, and controls.\n" + qa_pairs
        ),
        "SEQUENCE_SECTION": (
            "Write a detailed Sequence of Activities for the task based on these Q&As:\n" + qa_pairs
        ),
        "METHOD_SECTION": (
            "Write the Method Statement section, including roles, tools, PPE, hold points, CESWI refs, rescue plan:\n" + qa_pairs
        )
    }

    try:
        results = await asyncio.gather(*[
            openai.ChatCompletion.acreate(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a health and safety RAMS expert."},
                    {"role": "user", "content": text}
                ],
                temperature=0.0
            ) for text in prompts.values()
        ])
        sections = {key: results[i].choices[0].message.content.strip() for i, key in enumerate(prompts)}
    except Exception as e:
        logger.exception("Failed to generate sections")
        raise HTTPException(status_code=500, detail="Failed to generate RAMS content.")

    try:
        doc = await asyncio.get_running_loop().run_in_executor(None, Document, settings.template_path)
    except Exception:
        logger.exception("Template load failed")
        raise HTTPException(status_code=500, detail="Template error.")

    for p in doc.paragraphs:
        for key, val in sections.items():
            if key in p.text:
                p.text = p.text.replace(key, val)

    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for key, val in sections.items():
                    if key in cell.text:
                        cell.text = cell.text.replace(key, val)

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
