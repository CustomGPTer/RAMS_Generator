from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import openai
import shutil
from docx import Document
from dotenv import load_dotenv
import json
from io import BytesIO
import uuid

# Load environment variables from .env
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
model = os.getenv("OPENAI_MODEL", "gpt-4o")

TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", "templates/template_rams.docx")
OUTPUT_DIR = os.path.dirname(os.getenv("OUTPUT_PATH", "output/completed_rams.docx"))
PROMPT_PATH = os.getenv("PROMPT_PATH", "prompts/system_prompt.txt")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# FastAPI app
app = FastAPI(title="C2V+ RAMS Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load the system prompt
def load_prompt():
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()

# Generate section from GPT
def generate_section(prompt_text, temperature=0.2, max_tokens=6500):
    response = openai.ChatCompletion.create(
        model=model,
        messages=[
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": prompt_text}
        ],
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response["choices"][0]["message"]["content"]

# Replace placeholders
def fill_template(template_path, replacements: dict):
    doc = Document(template_path)
    for paragraph in doc.paragraphs:
        for key, value in replacements.items():
            if key in paragraph.text:
                paragraph.text = paragraph.text.replace(key, value)
    return doc

@app.post("/generate_rams")
async def generate_rams(
    answers: str = Form(...),
    template_rams: UploadFile = File(...)
):
    # Load answers
    answers_data = json.loads(answers)

    # Save uploaded template locally
    temp_template_path = f"temp_{uuid.uuid4()}.docx"
    with open(temp_template_path, "wb") as f:
        shutil.copyfileobj(template_rams.file, f)

    # Build prompts from answers
    scope = answers_data.get("scope", "No scope provided.")
    activity = answers_data.get("activity_description", "No activity provided.")

    prompt_risk = f"Generate the Risk Assessment for:\n\nScope: {scope}\nActivity: {activity}"
    prompt_sequence = f"Generate the Sequence of Activities for:\n\nScope: {scope}\nActivity: {activity}"
    prompt_method = f"Generate the Method Statement including all required sections for:\n\nScope: {scope}\nActivity: {activity}"

    # Generate content
    risk_text = generate_section(prompt_risk)
    sequence_text = generate_section(prompt_sequence)
    method_text = generate_section(prompt_method)

    # Replace placeholders
    replacements = {
        "[Enter Risk Assessment Table Here]": risk_text,
        "[Enter Sequence of Activities Here]": sequence_text,
        "[Enter Method Statement Here]": method_text
    }

    completed_doc = fill_template(temp_template_path, replacements)

    # Save final file
    output_path = f"{OUTPUT_DIR}/completed_rams_{uuid.uuid4()}.docx"
    completed_doc.save(output_path)

    # Clean up
    os.remove(temp_template_path)

    # Return completed document
    return FileResponse(
        path=output_path,
        filename="C2V_RAMSDocument.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

