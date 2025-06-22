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

# Load environment settings
class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str
    template_path: str

    class Config:
        env_file = ".env"

try:
    settings = Settings()
except ValidationError as e:
    logger.error(f"Environment config error: {e}")
    raise

# OpenAI client
client = AsyncOpenAI(api_key=settings.openai_api_key)
OPENAI_MODEL = settings.openai_model

# FastAPI setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Session management
sessions = {}
sessions_lock = asyncio.Lock()
SESSION_TTL = 3600  # 1 hour

def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in sessions.items() if now - s.get("last_active", 0) > SESSION_TTL]
    for sid in expired:
        sessions.pop(sid, None)
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired sessions.")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    cleanup_sessions()
    return templates.TemplateResponse("rams_chat.html", {"request": request})

@app.post("/rams_chat/start")
async def start_rams(request: Request, task: str = Body(..., embed=True)):
    if not task.strip():
        raise HTTPException(status_code=400, detail="Task description cannot be empty.")

    session_id = str(uuid.uuid4())
    system_prompt = (
        "You are an expert in writing Risk Assessment and Method Statement (RAMS) documents. "
        "Given a construction task, generate 20 specific and numbered questions needed to write a bespoke RAMS. "
        "Cover scope, hazards, PPE, controls, rescue plans, COSHH, training, plant, people and permits."
    )

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task.strip()}
            ],
            temperature=0.0
        )
        content = response.choices[0].message.content.strip()
        questions = [line.strip() for line in content.splitlines() if line.strip()]
    except Exception:
        logger.exception("OpenAI failed to generate questions.")
        raise HTTPException(status_code=500, detail="Failed to generate questions.")

    if len(questions) < 20:
        raise HTTPException(status_code=500, detail="Expected 20 questions from OpenAI.")

    async with sessions_lock:
        sessions[session_id] = {
            "task": task.strip(),
            "questions": questions[:20],
            "answers": [],
            "last_active": time.time()
        }

    return JSONResponse(content={"session_id": session_id, "questions": questions[:20]})

@app.post("/rams_chat/answer")
async def answer_rams(session_id: str = Body(...), answer: str = Body(...)):
    async with sessions_lock:
        session = sessions.get(session_id)

    if not session:
        raise HTTPException(status_code=400, detail="Session not found.")
    if not answer.strip():
        raise HTTPException(status_code=400, detail="Answer cannot be empty.")

    session["answers"].append(answer.strip())
    session["last_active"] = time.time()

    if len(session["answers"]) >= len(session["questions"]):
        return {"complete": True}

    next_question = session["questions"][len(session["answers"])]
    return {"complete": False, "next_question": next_question}

@app.post("/rams_chat/generate")
async def generate_rams(session_id: str = Body(...)):
    async with sessions_lock:
        session = sessions.get(session_id)

    if not session:
        raise HTTPException(status_code=400, detail="Session not found.")
    if len(session["answers"]) < len(session["questions"]):
        raise HTTPException(status_code=400, detail="All questions must be answered.")

    qa_block = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(zip(session["questions"], session["answers"]))])

    prompts = {
        "RISK_SECTION": f"Write the Risk Assessment section. Use the Q&A below:\n{qa_block}",
        "SEQUENCE_SECTION": f"Write a step-by-step Sequence of Work for the task. Use Q&A below:\n{qa_block}",
        "METHOD_SECTION": f"Write a detailed Method Statement, including safety procedures, people, PPE and rescue plan:\n{qa_block}"
    }

    try:
        completions = await asyncio.gather(*[
            client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a health and safety expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            ) for prompt in prompts.values()
        ])
        sections = {
            key: completions[i].choices[0].message.content.strip()
            for i, key in enumerate(prompts)
        }
    except Exception:
        logger.exception("Failed to generate final RAMS sections.")
        raise HTTPException(status_code=500, detail="RAMS generation failed.")

    try:
        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(None, Document, settings.template_path)
    except Exception:
        logger.exception("Word template could not be opened.")
        raise HTTPException(status_code=500, detail="Failed to open Word template.")

    # Replace placeholders
    for para in doc.paragraphs:
        for key, val in sections.items():
            if key in para.text:
                para.text = para.text.replace(key, val)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, val in sections.items():
                    if key in cell.text:
                        cell.text = cell.text.replace(key, val)

    buffer = BytesIO()
    await loop.run_in_executor(None, doc.save, buffer)
    buffer.seek(0)

    async with sessions_lock:
        sessions.pop(session_id, None)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=RAMS_Document.docx"}
    )




