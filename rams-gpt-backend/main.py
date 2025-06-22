import os, time, uuid, asyncio, logging
from io import BytesIO
from fastapi import FastAPI, Request, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseSettings
from openai import AsyncOpenAI
from docx import Document

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load settings from .env
class Settings(BaseSettings):
    openai_api_key: str
    openai_model: str
    template_path: str

    class Config:
        env_file = ".env"

settings = Settings()
client = AsyncOpenAI(api_key=settings.openai_api_key)
OPENAI_MODEL = settings.openai_model

# App setup
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Session store
sessions = {}
sessions_lock = asyncio.Lock()
SESSION_TTL = 3600

def cleanup_sessions():
    now = time.time()
    to_delete = [k for k,v in sessions.items() if now - v.get("last_active", 0) > SESSION_TTL]
    for k in to_delete:
        sessions.pop(k, None)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cleanup_sessions()
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/rams", response_class=HTMLResponse)
async def rams_chat(request: Request):
    return templates.TemplateResponse("rams_chat.html", {"request": request})

@app.post("/rams_chat/start")
async def start(task: str = Body(...)):
    if not task.strip():
        raise HTTPException(status_code=400, detail="Task is empty.")

    session_id = str(uuid.uuid4())
    system_prompt = (
        "You are an expert in writing Risk Assessment and Method Statement (RAMS) documents. "
        "Given a construction task, generate 20 specific and numbered questions needed to write a bespoke RAMS. "
        "Cover scope, hazards, PPE, controls, rescue plans, COSHH, training, plant, people and permits."
    )

    try:
        res = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task.strip()}
            ],
            temperature=0.0
        )
        questions = [line.strip() for line in res.choices[0].message.content.strip().splitlines() if line.strip()]
    except Exception as e:
        logger.exception("OpenAI error")
        raise HTTPException(500, "Failed to generate questions.")

    if len(questions) < 20:
        raise HTTPException(500, "OpenAI returned too few questions.")

    async with sessions_lock:
        sessions[session_id] = {
            "questions": questions[:20],
            "answers": [],
            "last_active": time.time()
        }

    return {"session_id": session_id, "questions": questions[:20]}

@app.post("/rams_chat/answer")
async def answer(session_id: str = Body(...), answer: str = Body(...)):
    async with sessions_lock:
        session = sessions.get(session_id)

    if not session:
        raise HTTPException(400, "Session not found.")
    if not answer.strip():
        raise HTTPException(400, "Answer is empty.")

    session["answers"].append(answer.strip())
    session["last_active"] = time.time()

    if len(session["answers"]) >= len(session["questions"]):
        return {"complete": True}
    else:
        next_q = session["questions"][len(session["answers"])]
        return {"complete": False, "next_question": next_q}

@app.post("/rams_chat/generate")
async def generate(session_id: str = Body(...)):
    async with sessions_lock:
        session = sessions.get(session_id)
    if not session:
        raise HTTPException(400, "Session not found.")
    if len(session["answers"]) < 20:
        raise HTTPException(400, "Answer all questions before generating RAMS.")

    qa_text = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i,(q,a) in enumerate(zip(session["questions"], session["answers"]))])

    prompts = {
        "RISK_SECTION": "Write the Risk Assessment section:\n" + qa_text,
        "SEQUENCE_SECTION": "Write the Sequence of Activities section:\n" + qa_text,
        "METHOD_SECTION": "Write the Method Statement section including roles, PPE, and rescue:\n" + qa_text
    }

    try:
        results = await asyncio.gather(*[
            client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": "You are a RAMS expert"}, {"role": "user", "content": p}],
                temperature=0.0
            ) for p in prompts.values()
        ])
        content = {k: results[i].choices[0].message.content.strip() for i, k in enumerate(prompts)}
    except Exception:
        logger.exception("Final RAMS generation failed")
        raise HTTPException(500, "RAMS generation failed.")

    # Load and insert into Word doc
    loop = asyncio.get_running_loop()
    try:
        doc = await loop.run_in_executor(None, Document, settings.template_path)
    except Exception:
        raise HTTPException(500, "Could not load Word template.")

    for para in doc.paragraphs:
        for k, v in content.items():
            if k in para.text:
                para.text = para.text.replace(k, v)

    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for k, v in content.items():
                    if k in cell.text:
                        cell.text = cell.text.replace(k, v)

    buf = BytesIO()
    await loop.run_in_executor(None, doc.save, buf)
    buf.seek(0)

    async with sessions_lock:
        sessions.pop(session_id, None)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=RAMS_Document.docx"}
    )





