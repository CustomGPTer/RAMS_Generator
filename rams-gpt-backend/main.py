from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from docx import Document
from dotenv import load_dotenv
import openai
import os
import logging
import asyncio
from io import BytesIO

# Load .env variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rams-generator")

# Load environment variables
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
PROMPT_PATH = os.getenv("PROMPT_PATH", "prompts/system_prompt.txt")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4300"))

openai.api_key = OPENAI_API_KEY

# Init app
app = FastAPI(
    title="C2V+ RAMS Generator",
    description="Generates RAMS documents from 20 user answers using OpenAI",
    version="1.2.0"
)

# CORS and compression
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Models
class SectionInput(BaseModel):
    content: str

class FullInput(BaseModel):
    answers: list[str]

# Helpers
def insert_risk_assessment_table(doc: Document, content: str):
    for table in doc.tables:
        for i, row in enumerate(table.rows):
            if "Insert hazards here" in row.cells[1].text:
                table._tbl.remove(row._tr)
                lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
                for index, line in enumerate(lines, start=1):
                    cols = line.split("\t")
                    new_cells = table.add_row().cells
                    new_cells[0].text = str(index)
                    for j in range(min(len(cols), 6)):
                        new_cells[j + 1].text = cols[j].strip()
                return
    raise ValueError("Risk assessment placeholder not found")

def insert_section_by_placeholder(doc: Document, placeholder: str, content: str):
    for para in doc.paragraphs:
        if placeholder in para.text:
            style = para.style
            parts = content.strip().split("\n\n") if "\n\n" in content else content.strip().split("\n")
            for part in parts:
                if part.strip():
                    new_para = para.insert_paragraph_before(part.strip())
                    new_para.style = style
            para._element.getparent().remove(para._element)
            return
    raise ValueError(f"Placeholder '{placeholder}' not found")

# Endpoints
@app.get("/")
async def serve_form():
    return FileResponse("static/index.html")

@app.post("/generate_rams")
async def generate_full_rams(input: FullInput):
    if len(input.answers) != 20:
        return JSONResponse(status_code=400, content={"error": "Exactly 20 answers are required."})

    try:
        # Load and trim system prompt
        system_prompt = ""
        if os.path.exists(PROMPT_PATH):
            with open(PROMPT_PATH, "r") as f:
                system_prompt = f.read().strip().split("RAMS Section Submission Logic")[0].strip()

        answers_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(input.answers))

        prompts = {
            "risk": (
                f"Below are the 20 site-specific answers:\n{answers_text}\n\n"
                "Now generate the **Risk Assessment Table** only, using tab-separated values for each row: "
                "Hazard<TAB>Persons at Risk<TAB>Undesired Event<TAB>Control Measures<TAB>Actioned By. "
                "Provide at least 20 hazards. No numbers or formatting."
            ),
            "sequence": (
                f"Using the same 20 answers above, generate a **Sequence of Activities** (minimum 600 words). "
                f"Cover start-to-finish in multiple paragraphs."
            ),
            "method": (
                f"Using the same 20 answers, generate a **Method Statement** (minimum 750 words). "
                f"Include: Scope of Works, Roles and Responsibilities, Hold Points, Operated Plant, Tools and Equipment, "
                f"Materials, PPE, Rescue Plan (5 scenarios), Site Standards, CESWI Clauses, Quality Control, Environment."
            )
        }

        async def generate_section(prompt: str):
            result = await openai.ChatCompletion.acreate(
                model=OPENAI_MODEL,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
            )
            return result.choices[0].message.content.strip()

        risk_task = generate_section(prompts["risk"])
        sequence_task = generate_section(prompts["sequence"])
        method_task = generate_section(prompts["method"])

        risk_content, sequence_content, method_content = await asyncio.gather(risk_task, sequence_task, method_task)

        doc = Document(TEMPLATE_PATH)
        insert_risk_assessment_table(doc, risk_content)
        insert_section_by_placeholder(doc, "[Enter Sequence of Activities Here]", sequence_content)
        insert_section_by_placeholder(doc, "[Enter Method Statement Here]", method_content)

        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        return Response(content=buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": "attachment; filename=completed_rams.docx"})

    except Exception as e:
        logger.error(f"Error generating RAMS: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# Optional legacy endpoints (not needed for the HTML form, but safe to keep)
@app.post("/generate_risk_assessment")
async def generate_risk_assessment(input: SectionInput):
    try:
        doc = Document(TEMPLATE_PATH)
        insert_risk_assessment_table(doc, input.content)
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return Response(content=buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": "attachment; filename=completed_rams.docx"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_sequence")
async def generate_sequence(input: SectionInput):
    try:
        doc = Document(TEMPLATE_PATH)
        insert_section_by_placeholder(doc, "[Enter Sequence of Activities Here]", input.content)
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return Response(content=buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": "attachment; filename=completed_rams.docx"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_method_statement")
async def generate_method_statement(input: SectionInput):
    try:
        doc = Document(TEMPLATE_PATH)
        insert_section_by_placeholder(doc, "[Enter Method Statement Here]", input.content)
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        return Response(content=buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": "attachment; filename=completed_rams.docx"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Mount static folder for form hosting
app.mount("/static", StaticFiles(directory="static"), name="static")



