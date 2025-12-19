import os
import uuid
import io
import traceback
import re

from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename

# ===== Azure AI Foundry (API KEY AUTH) =====
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole, ListSortOrder
from azure.core.credentials import AzureKeyCredential

# ===== Azure Blob Storage =====
from azure.storage.blob import BlobServiceClient

# ===== PDF Generation =====
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer


# =====================================================
# 🔐 HARD-CODED CONFIG (OK FOR NOW – DEMO)
# =====================================================
AI_PROJECT_ENDPOINT = "https://ai-rg-discoveriq.services.ai.azure.com/api/projects/DiscoverIQ"
AI_PROJECT_API_KEY = "G19Ax6sSu5T6nhhrqfAmjUQnc78Ox6eOsMzT2X4UK6TkhD2imKnWJQQJ99BJACYeBjFXJ3w3AAAAACOG3PVC"

AGENT_ID = "asst_EBPhWjwxGX4yEnh5k5KjUhWC"

AZURE_STORAGE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=discoveriqstorage;"
    "AccountKey=fEVn0vPxGKaztcvTSBnFcDyb2F/5kWOud5urotgklyY+ce5LFrTpbvkwmBBRLTI2Q7uoRTmIpXqK+ASth1FD4A==;"
    "EndpointSuffix=core.windows.net"
)

AZURE_BLOB_CONTAINER = "flatfileinputs"
# =====================================================


ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# =====================================================
# Flask App
# =====================================================
app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def allowed_file(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)


# =====================================================
# Azure AI Foundry Client (API KEY)
# =====================================================
project_client = AIProjectClient(
    endpoint=AI_PROJECT_ENDPOINT,
    credential=AzureKeyCredential(AI_PROJECT_API_KEY),
)

print("✅ Azure AI Foundry client initialized using API Key")


# =====================================================
# Azure Blob Storage Client
# =====================================================
blob_service_client = BlobServiceClient.from_connection_string(
    AZURE_STORAGE_CONNECTION_STRING
)
container_client = blob_service_client.get_container_client(
    AZURE_BLOB_CONTAINER
)

try:
    container_client.create_container()
except Exception:
    pass

print("✅ Azure Blob Storage initialized")


# =====================================================
# Routes
# =====================================================
@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------------- CHAT ----------------
@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "message required"}), 400

    try:
        # Create thread
        thread = project_client.agents.threads.create()

        # Add user message
        project_client.agents.messages.create(
            thread_id=thread.id,
            role=MessageRole.USER,
            content=user_message,
        )

        # Run agent
        project_client.agents.runs.create_and_process(
            thread_id=thread.id,
            agent_id=AGENT_ID,
        )

        # Fetch messages
        messages = project_client.agents.messages.list(
            thread_id=thread.id,
            order=ListSortOrder.ASCENDING
        ).data

        for msg in reversed(messages):
            if msg.role == "assistant":
                return jsonify({
                    "reply": msg.text_messages[-1].text.value
                })

        return jsonify({"reply": "No response from agent"})

    except Exception as e:
        print("CHAT ERROR:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------- FILE UPLOAD ----------------
@app.post("/upload")
def upload():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file"}), 400

        f = request.files["file"]

        if not allowed_file(f.filename):
            return jsonify({"error": "Invalid file type"}), 400

        blob_name = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
        blob_client = container_client.get_blob_client(blob_name)

        blob_client.upload_blob(f, overwrite=True)

        return jsonify({
            "filename": f.filename,
            "blob_url": blob_client.url
        })

    except Exception as e:
        print("UPLOAD ERROR:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------- PDF DOWNLOAD ----------------
@app.post("/download_pdf")
def download_pdf():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    if not text:
        return jsonify({"error": "No text"}), 400

    pdf_bytes = create_pdf(text)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="agent_response.pdf"
    )


def create_pdf(text: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    for line in text.split("\n"):
        story.append(Paragraph(line, styles["BodyText"]))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


# ---------------- HEALTH ----------------
@app.get("/health")
def health():
    return {"status": "ok"}


# =====================================================
# ENTRY POINT (RENDER / LOCAL)
# =====================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
