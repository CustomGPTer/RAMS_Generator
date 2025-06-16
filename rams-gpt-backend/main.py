from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from docx import Document
import os
import logging

# Environment paths
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "output/completed_rams.docx")

# Init FastAPI app
app = FastAPI(
    title="C2V+ RAMS Generator",
    description="Appends sections to a master Word RAMS template cumulatively.",
    version="1.0.0"
)

# Optional: Enable CORS if testing from external UIs
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rams-generator")

# Load master document on startup
try:
    doc = Document(TEMPLATE_PATH)
    logger.info(f"Loaded template from {TEMPLATE_PATH}")
except Exception as e:
    logger.error(f"Error loading template: {e}")
    raise RuntimeError("Failed to load Word template.")

def insert_section(title: str, content: str):
    """Insert a new section into the Word document line by line."""
    doc.add_page_break()
    doc.add_heading(title, level=1)

    for line in content.strip().splitlines():
        doc.add_paragraph(line.strip())

    doc.save(OUTPUT_PATH)
    logger.info(f"Inserted section: {title} → saved to {OUTPUT_PATH}")

@app.post("/generate_risk_assessment")
async def generate_risk_assessment(content: str = Body(...)):
    try:
        insert_section("Risk Assessment", content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        logger.error(f"Error inserting Risk Assessment: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_sequence")
async def generate_sequence(content: str = Body(...)):
    try:
        insert_section("Sequence of Activities", content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        logger.error(f"Error inserting Sequence: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_method_statement")
async def generate_method_statement(content: str = Body(...)):
    try:
        insert_section("Method Statement", content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        logger.error(f"Error inserting Method Statement: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


