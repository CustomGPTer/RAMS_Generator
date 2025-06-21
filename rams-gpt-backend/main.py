```python
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from docx import Document
import os
import logging
from starlette.middleware.gzip import GZipMiddleware
from dotenv import load_dotenv
import openai
import asyncio

# Load .env file if present (for local development)
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rams-generator")

# Paths and config from environment or defaults
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
OUTPUT_PATH   = os.getenv("OUTPUT_PATH", "output/completed_rams.docx")
PROMPT_PATH   = os.getenv("PROMPT_PATH", "prompts/system_prompt.txt")

# Ensure output directory exists (for legacy endpoints usage)
OUTPUT_DIR = os.path.dirname(OUTPUT_PATH)
os.makedirs(OUTPUT_DIR, exist_ok=True)
try:
    # Quick write test to verify we have write permissions
    with open(os.path.join(OUTPUT_DIR, "test_write.txt"), "w") as f:
        f.write("RAMS generator write check")
    logger.info("Write check passed.")
except Exception as e:
    logger.error(f"WRITE ERROR: {e}")

# FastAPI app initialization
app = FastAPI(
    title="C2V+ RAMS Generator",
    description="Generates a site-specific RAMS Word document using a template and AI (ChatGPT) content.",
    version="1.2.0"
)

# CORS and GZip middleware (as in original code, allowing all origins for simplicity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Health check endpoint
@app.get("/")
async def root():
    logger.info("Health check ping received")
    return {"message": "RAMS Generator is running"}

@app.head("/")
async def root_head():
    return {"message": "RAMS Generator is running"}

# Pydantic models for request bodies
class SectionInput(BaseModel):
    content: str

class FullInput(BaseModel):
    answers: list[str]

# Helper function to insert Risk Assessment table content into a Document
def insert_risk_assessment_table(doc: Document, content: str):
    """
    Finds the placeholder row in the risk assessment table and replaces it with generated rows.
    """
    found = False
    for table in doc.tables:
        for i, row in enumerate(table.rows):
            # Check second cell text for the placeholder marker
            if "Insert hazards here" in row.cells[1].text:
                target_table = table
                placeholder_index = i
                found = True
                break
        if found:
            # Remove the placeholder row from the table
            target_table._tbl.remove(target_table.rows[placeholder_index]._tr)
            # Split content by lines and insert each as a new row
            lines = [ln for ln in content.strip().splitlines() if ln.strip()]
            for index, line in enumerate(lines, start=1):
                cols = line.split("\t")
                new_cells = target_table.add_row().cells
                new_cells[0].text = str(index)  # first column: auto-number
                # Fill up to 6 subsequent columns with provided data
                for j in range(min(len(cols), 6)):
                    new_cells[j + 1].text = cols[j].strip()
            logger.info(f"Inserted {len(lines)} hazard rows into Risk Assessment table.")
            break
    if not found:
        raise ValueError("Risk assessment placeholder row not found in template.")

# Helper function to replace a placeholder paragraph with content (possibly multi-paragraph)
def insert_section_by_placeholder(doc: Document, placeholder: str, content: str):
    """
    Replaces a paragraph containing the placeholder text with the provided content.
    If content has multiple paragraphs (separated by blank lines or newline), it inserts them as separate paragraphs.
    """
    for para in doc.paragraphs:
        if placeholder in para.text:
            base_para = para
            style = base_para.style  # preserve the style of the placeholder paragraph
            text_content = content.strip()
            if text_content == "":
                # If content is empty, just remove the placeholder paragraph
                base_para._element.getparent().remove(base_para._element)
                logger.info(f"Removed empty placeholder paragraph: {placeholder}")
            else:
                # Determine how to split content into paragraphs
                if "\n\n" in text_content:
                    parts = text_content.split("\n\n")
                else:
                    parts = text_content.split("\n")
                # Insert new paragraphs for each part above the placeholder paragraph
                for part in parts:
                    part_text = part.strip()
                    if part_text == "":
                        continue  # skip empty parts (e.g., extra newlines)
                    new_para = base_para.insert_paragraph_before(part_text)
                    new_para.style = style
                # Remove the original placeholder paragraph after inserting content
                base_para._element.getparent().remove(base_para._element)
                logger.info(f"Replaced placeholder '{placeholder}' with {len(parts)} paragraphs of content.")
            return  # once done, exit the function
    # If we reach here, placeholder was not found
    raise ValueError(f"Placeholder '{placeholder}' not found in the template.")

# Existing partial endpoints (for reference or alternative use)
@app.post("/generate_risk_assessment")
async def generate_risk_assessment(input: SectionInput):
    try:
        insert_risk_assessment_table(Document(OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH),
                                     input.content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Risk assessment error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_sequence")
async def generate_sequence(input: SectionInput):
    try:
        insert_section_by_placeholder(Document(OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH),
                                      "[Enter Sequence of Activities Here]", input.content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Sequence section error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate_method_statement")
async def generate_method_statement(input: SectionInput):
    try:
        insert_section_by_placeholder(Document(OUTPUT_PATH if os.path.exists(OUTPUT_PATH) else TEMPLATE_PATH),
                                      "[Enter Method Statement Here]", input.content)
        return FileResponse(OUTPUT_PATH, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            filename="completed_rams.docx")
    except Exception as e:
        logger.error(f"Method Statement error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# **New Endpoint**: Generate the full RAMS document in one go
@app.post("/generate_rams")
async def generate_full_rams(input: FullInput):
    """
    Accepts 20 answers and returns a completed RAMS Word document.
    """
    answers = input.answers
    if len(answers) != 20:
        return JSONResponse(status_code=400, content={"error": "Exactly 20 answers are required."})
    try:
        # Load system prompt instructions (if available)
        system_prompt_text = ""
        try:
            with open(PROMPT_PATH, 'r') as f:
                system_prompt_text = f.read().strip()
        except Exception as e:
            logger.warning(f"Could not load system prompt file: {e}")
        # Remove any plugin-specific instructions from system prompt (we want content only)
        if system_prompt_text:
            trunc_index = system_prompt_text.find("RAMS Section Submission Logic")
            if trunc_index != -1:
                system_prompt_text = system_prompt_text[:trunc_index].strip()
        # Base messages for OpenAI
        messages_base = []
        if system_prompt_text:
            messages_base.append({"role": "system", "content": system_prompt_text})
        # Format the 20 answers as a numbered list in the user prompt
        answers_list_text = "\n".join(f"{i+1}. {ans}" for i, ans in enumerate(answers))
        # Create specific user prompts for each section
        user_prompt_risk = (
            "Below are the 20 site-specific answers from the user:\n"
            f"{answers_list_text}\n\n"
            "Now generate the **Risk Assessment Table** section content ONLY. "
            "Include at least 20 unique task-specific hazards. For each hazard, provide entries for "
            "Hazard, Persons at Risk, Undesired Event, Control Measures, and Actioned By, **separated by a tab**. "
            "Do NOT include any numbering or bullets (the system will number the hazards)."
        )
        user_prompt_sequence = (
            f"Using the same 20 answers above, generate the **Sequence of Activities** section content (minimum 600 words). "
            f"Provide a detailed step-by-step narrative from site access, through the task, to reinstatement, "
            f"including isolations, controls, and hold points. Use multiple paragraphs for clarity."
        )
        user_prompt_method = (
            f"Using the 20 answers above, generate the **Method Statement** section content (minimum 750 words). "
            f"Make sure to cover all required subsections: Scope of Works, Roles and Responsibilities, Hold Points, "
            f"Operated Plant, Tools and Equipment, Materials, PPE, Rescue Plan (with five scenarios), "
            f"Applicable Site Standards, CESWI Clauses, Quality Control, and Environmental Considerations. "
            f"Provide a well-structured and detailed narrative covering all these aspects."
        )
        # Prepare OpenAI API parameters
        openai.api_key = os.getenv("OPENAI_API_KEY")  # ensure API key is set
        model_name = os.getenv("OPENAI_MODEL", "gpt-4")
        temperature = float(os.getenv("TEMPERATURE", "0.2"))
        max_tokens = int(os.getenv("MAX_TOKENS", "4300"))
        # Create concurrent tasks for each ChatGPT API call
        tasks = [
            openai.ChatCompletion.acreate(model=model_name, messages=messages_base + [{"role": "user", "content": user_prompt_risk}], 
                                          temperature=temperature, max_tokens=max_tokens),
            openai.ChatCompletion.acreate(model=model_name, messages=messages_base + [{"role": "user", "content": user_prompt_sequence}], 
                                          temperature=temperature, max_tokens=max_tokens),
            openai.ChatCompletion.acreate(model=model_name, messages=messages_base + [{"role": "user", "content": user_prompt_method}], 
                                          temperature=temperature, max_tokens=max_tokens)
        ]
        # Run the OpenAI calls in parallel
        results = await asyncio.gather(*tasks)
        # Extract the generated text from each result
        risk_content = results[0].choices[0].message.content if results[0] else ""
        seq_content  = results[1].choices[0].message.content if results[1] else ""
        method_content = results[2].choices[0].message.content if results[2] else ""
        logger.info("Received AI-generated content for all sections.")
        # Open a fresh copy of the template and insert all content
        doc = Document(TEMPLATE_PATH)
        if risk_content:
            insert_risk_assessment_table(doc, risk_content)
        if seq_content:
            insert_section_by_placeholder(doc, "[Enter Sequence of Activities Here]", seq_content)
        if method_content:
            insert_section_by_placeholder(doc, "[Enter Method Statement Here]", method_content)
        # Save document to a bytes buffer instead of a file
        from io import BytesIO
        buffer = BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        # Return the file as a response for download
        return Response(content=buffer.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": "attachment; filename=completed_rams.docx"})
    except Exception as e:
        logger.error(f"Full generation error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
```python



