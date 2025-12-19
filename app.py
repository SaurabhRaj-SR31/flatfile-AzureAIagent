import os
import uuid
import traceback
import io
import re

from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole, ListSortOrder

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
    ListFlowable,
    ListItem,
)

# ---------------- HARD-CODED CONFIG ----------------
AI_PROJECT_ENDPOINT = "https://ai-rg-discoveriq.services.ai.azure.com/api/projects/DiscoverIQ"
AGENT_ID = "asst_EBPhWjwxGX4yEnh5k5KjUhWC"

AZURE_STORAGE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=discoveriqstorage;"
    "AccountKey=PASTE_YOUR_KEY_HERE;"
    "EndpointSuffix=core.windows.net"
)

AZURE_BLOB_CONTAINER = "flatfileinputs"
# --------------------------------------------------

ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def allowed_file(filename: str) -> bool:
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)


# ---------- Azure Clients ----------
credential = None
try:
    credential = AzureCliCredential()
except Exception:
    credential = None

project_client = AIProjectClient(
    credential=credential,
    endpoint=AI_PROJECT_ENDPOINT
)

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


# ---------- ROUTES ----------
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "message required"}), 400

    try:
        thread = project_client.agents.threads.create()
        project_client.agents.messages.create(
            thread_id=thread.id,
            role=MessageRole.USER,
            content=user_message,
        )

        project_client.agents.runs.create_and_process(
            thread_id=thread.id,
            agent_id=AGENT_ID,
        )

        messages = project_client.agents.messages.list(
            thread_id=thread.id,
            order=ListSortOrder.ASCENDING
        ).data

        for msg in reversed(messages):
            if msg.role == "assistant":
                return jsonify({"reply": msg.text_messages[-1].text.value})

        return jsonify({"reply": "No response from agent"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/upload")
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    f = request.files["file"]
    if not allowed_file(f.filename):
        return jsonify({"error": "Invalid file type"}), 400

    blob_name = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    blob_client = container_client.get_blob_client(blob_name)
    blob_client.upload_blob(f)

    return jsonify({
        "filename": f.filename,
        "blob_url": blob_client.url
    })


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


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- ENTRY POINT ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
