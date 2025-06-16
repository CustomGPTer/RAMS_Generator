from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from docx import Document
import os
import logging
from starlette.middleware.gzip import GZipMiddleware

# Logging setup (MUST be before logger is used)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rams-generator")

# Environment paths
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
OUTPUT_PATH = os.getenv("OUTPUT_PATH", "output/completed_rams.docx")

# Ensure output directory exists
OUTPUT_DIR = os.path.dirname(OUTPUT_PATH)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Optional debug write check
try:
    test_path = os.path.join(OUTPUT_DIR, "test_write.txt")
    with open(test_path, "w") as f:
        f.write("RAMS generator write check")
    logger.info(f"Write check passed: {test_path}")
except Exception as write_err:
    logger.error(f"WRITE ERROR: Cannot write to {OUTPUT_DIR}: {write_err}")

# Init FastAPI app
app = FastAPI(
    title="C2V+ RAMS Generator",
    description="Appends sections to a master Word RAMS template cumulatively.",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enable GZip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Health check route
@app.get("/")
async def root():
    logger.info("Health check ping received")
    return {"message": "RAMS Generator is running"}

# Input model
class SectionInput(BaseModel):
    content: str

# Insert section function
def insert_section(title: str, content: str):
    """Append a section to the existing RAMS output document."""
    doc = Document(OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH)
    doc.add_page_break()
    doc.add_heading(title, level=1)
    for line in content.strip().splitlines():
        doc.add_paragraph(line.strip())
    doc.save(OUTPUT_PATH)
    logger.info(f"Inserted section: {title} → saved to {OUTPUT_PATH}")

@app.post("/generate_risk_assessment", response_class=FileResponse)
async def generate_risk_assessment(input: SectionInput):
    try:
        logger.info(f"Risk Assessment content length: {len(input.content)} characters")
        insert_section("Risk Assessment", input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="risk_assessment.docx")
    except Exception as e:
        logger.error(f"Error inserting Risk Assessment: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_sequence", response_class=FileResponse)
async def generate_sequence(input: SectionInput):
    try:
        logger.info(f"Sequence content length: {len(input.content)} characters")
        insert_section("Sequence of Activities", input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="sequence_of_activities.docx")
    except Exception as e:
        logger.error(f"Error inserting Sequence: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_method_statement", response_class=FileResponse)
async def generate_method_statement(input: SectionInput):
    try:
        logger.info(f"Method Statement content length: {len(input.content)} characters")
        insert_section("Method Statement", input.content)
        return FileResponse(OUTPUT_PATH,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="method_statement.docx")
    except Exception as e:
        logger.error(f"Error inserting Method Statement: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


