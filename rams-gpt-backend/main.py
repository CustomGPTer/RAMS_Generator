from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from docx import Document
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai._httpx_client import AsyncHttpxClientWrapper
from httpx import AsyncClient
import os
import asyncio
from io import BytesIO

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")

# ✅ Disable proxy injection from host environments like Render
custom_http_client = AsyncHttpxClientWrapper(AsyncClient(proxies=None))
client = AsyncOpenAI(api_key=OPENAI_API_KEY, http_client=custom_http_client)

# FastAPI app setup
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Session state (in memory)
chat_state = {}

@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/rams", response_class=HTMLResponse)
async def rams_page(request: Request):
    return templates.TemplateResponse("rams_chat.html", {"request": request})

@app.post("/rams_chat/start")
async def start_chat(task: str = Form(...)):
    try:
        messages = [
            {
                "role": "system",
                "content": "You are a construction safety AI. Generate exactly 20 very specific RAMS questions based only on the task provided. Do not add intro or explanation. Return only a numbered list of the questions."
            },
            {"role": "user", "content": f"The task is: {task}"}
        ]
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.4
        )
        questions_raw = response.choices[0].message.content.strip()
        questions = [q.split('. ', 1)[-1].strip() for q in questions_raw.split('\n') if q.strip()]
        if len(questions) != 20:
            return JSONResponse(status_code=500, content={"error": "GPT did not return exactly 20 questions."})
        session_id = os.urandom(6).hex()
        chat_state[session_id] = {
            "task": task,
            "questions": questions,
            "answers": []
        }
        return {"session_id": session_id, "questions": questions}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/rams_chat/answer")
async def submit_answer(session_id: str = Form(...), answer: str = Form(...)):
    try:
        if session_id not in chat_state:
            return JSONResponse(status_code=400, content={"error": "Session expired. Please refresh and start again."})
        state = chat_state[session_id]
        state["answers"].append(answer)
        next_index = len(state["answers"])
        if next_index >= 20:
            return {"complete": True}
        return {"complete": False, "next_question": state["questions"][next_index]}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/rams_chat/generate")
async def generate_rams(session_id: str = Form(...)):
    try:
        if session_id not in chat_state or len(chat_state[session_id]["answers"]) != 20:
            return JSONResponse(status_code=400, content={"error": "Incomplete session or session expired."})

        task = chat_state[session_id]["task"]
        answers = chat_state[session_id]["answers"]
        answers_list = "\n".join([f"{i+1}. {a}" for i, a in enumerate(answers)])

        prompts = {
            "risk": (
                f"Based on this task: {task}\n\nAnd these answers:\n{answers_list}\n\n"
                "Generate a Risk Assessment Table with at least 20 hazards. For each hazard, return tab-separated values in this order:\n"
                "Hazard\tPersons at Risk\tUndesired Event\tControl Measures\tActioned By\nReturn only one hazard per line."
            ),
            "sequence": (
                f"Task: {task}\n\nAnswers:\n{answers_list}\n\nGenerate the Sequence of Activities section. Minimum 600 words. Use multiple paragraphs."
            ),
            "method": (
                f"Task: {task}\n\nAnswers:\n{answers_list}\n\nGenerate the Method Statement section. Minimum 750 words. Include:\n"
                "Scope, Roles and Responsibilities, PPE, Rescue Plan, CESWI Clauses, Hold Points, Tools and Equipment, Materials, Quality, Environmental Controls."
            )
        }

        async def get_section(prompt):
            result = await client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return result.choices[0].message.content.strip()

        risk_task = get_section(prompts["risk"])
        seq_task = get_section(prompts["sequence"])
        method_task = get_section(prompts["method"])
        risk, sequence, method = await asyncio.gather(risk_task, seq_task, method_task)

        doc = Document(TEMPLATE_PATH)

        # Insert Risk Assessment Table
        for table in doc.tables:
            for i, row in enumerate(table.rows):
                if "Insert hazards here" in row.cells[1].text:
                    table._tbl.remove(row._tr)
                    for idx, line in enumerate(risk.strip().splitlines(), 1):
                        cols = line.split('\t')
                        row_cells = table.add_row().cells
                        row_cells[0].text = str(idx)
                        for j in range(min(5, len(cols))):
                            row_cells[j+1].text = cols[j].strip()
                    break

        # Insert Sequence
        for para in doc.paragraphs:
            if "[Enter Sequence of Activities Here]" in para.text:
                para.text = ""
                for line in sequence.strip().split("\n"):
                    if line.strip():
                        new_para = para.insert_paragraph_before(line.strip())
                        new_para.style = para.style
                para._element.getparent().remove(para._element)
                break

        # Insert Method Statement
        for para in doc.paragraphs:
            if "[Enter Method Statement Here]" in para.text:
                para.text = ""
                for line in method.strip().split("\n"):
                    if line.strip():
                        new_para = para.insert_paragraph_before(line.strip())
                        new_para.style = para.style
                para._element.getparent().remove(para._element)
                break

        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        filename = f"rams_{session_id}.docx"

        # Clean up session
        del chat_state[session_id]

        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})




