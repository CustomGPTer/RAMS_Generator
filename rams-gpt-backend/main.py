from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from docx import Document
import os
import logging
from starlette.middleware.gzip import GZipMiddleware
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rams-generator")

# Paths from environment or defaults
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "output/completed_rams.docx")

# Ensure output directory exists
OUTPUT_DIR = os.path.dirname(OUTPUT_PATH)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Optional write test
try:
    with open(os.path.join(OUTPUT_DIR, "test_write.txt"), "w") as f:
        f.write("RAMS generator write check")
    logger.info("Write check passed.")
except Exception as e:
    logger.error(f"WRITE ERROR: {e}")

# FastAPI app
app = FastAPI(
    title="C2V+ RAMS Generator",
    description="Replaces placeholders in a Word RAMS template with site-specific content.",
    version="1.0.0"
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Health check
@app.get("/")
async def root():
    logger.info("Health check ping received")
    return {"message": "RAMS Generator is running"}

@app.head("/")
async def root_head():
    return {"message": "RAMS Generator is running"}

# Pydantic model
class SectionInput(BaseModel):
    content: str

# Function to insert Risk Assessment table content (replaces row in template)
def insert_risk_assessment_table(content: str):
    doc_path = OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH
    doc = Document(doc_path)

    found = False
    row_index = None

    for table in doc.tables:
        for i, row in enumerate(table.rows):
            if "Insert hazards here" in row.cells[1].text:
                row_index = i
                found = True
                break
        if found:
            target_table = table
            break

    if not found:
        raise ValueError("Could not find the risk assessment placeholder row.")

    # Remove placeholder row
    tbl = target_table._tbl
    tbl.remove(target_table.rows[row_index]._tr)

    # Add content rows
    lines = content.strip().splitlines()
    for index, line in enumerate(lines, start=1):
        cols = line.split("\t")
        new_row = target_table.add_row().cells
        new_row[0].text = str(index)  # Auto-number
        for j in range(min(len(cols), 6)):
            new_row[j + 1].text = cols[j].strip()

    doc.save(OUTPUT_PATH)
    logger.info("Inserted formatted Risk Assessment rows.")

# Function to replace body text placeholders (Sequence and Method)
def insert_section_by_placeholder(placeholder: str, content: str):
    doc_path = OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH
    doc = Document(doc_path)

    found = False
    for para in doc.paragraphs:
        if placeholder in para.text:
            para.text = content.strip()
            found = True
            break

    if not found:
        raise ValueError(f"Placeholder '{placeholder}' not found in the template.")

    doc.save(OUTPUT_PATH)
    logger.info(f"Replaced placeholder: {placeholder}")

# API endpoints
@app.post("/generate_risk_assessment")
async def generate_risk_assessment(input: SectionInput):
    try:
        insert_risk_assessment_table(input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Risk assessment error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_sequence")
async def generate_sequence(input: SectionInput):
    try:
        insert_section_by_placeholder("[Enter Sequence of Activities Here]", input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Sequence section error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_method_statement")
async def generate_method_statement(input: SectionInput):
    try:
        insert_section_by_placeholder("[Enter Method Statement Here]", input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Method Statement error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})



