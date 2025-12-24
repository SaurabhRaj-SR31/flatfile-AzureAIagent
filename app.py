import os
import uuid
import io
import traceback
from datetime import datetime

from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename

# ---------------- Azure Auth ----------------
from azure.identity import ClientSecretCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole, ListSortOrder

# ---------------- Azure Blob ----------------
from azure.storage.blob import BlobServiceClient

# ---------------- PDF ----------------
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

# =====================================================
# 🔴 UPDATE THESE VALUES
# =====================================================
TENANT_ID = "d3e4c61b-2e8e-4b54-a89c-19706dab6b3c"
CLIENT_ID = "ff7486df-777e-440d-865f-6d74845a6f85"
CLIENT_SECRET = ".DL8Q~5eztBS_rp6i9xlsIFeM2lymslAf8A9caiB"

AI_PROJECT_ENDPOINT = "https://ai-rg-discoveriq.services.ai.azure.com/api/projects/DiscoverIQ"
AGENT_ID = "asst_EBPhWjwxGX4yEnh5k5KjUhWC"

# ---- 3️⃣ AZURE STORAGE ACCOUNT ----
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

app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

# =====================================================
# 🔐 Azure Clients
# =====================================================
credential = ClientSecretCredential(
    tenant_id=TENANT_ID,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
)

project_client = AIProjectClient(
    endpoint=AI_PROJECT_ENDPOINT,
    credential=credential,
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

# =====================================================
# 🧠 SESSION → THREAD MAP (IN-MEMORY)
# =====================================================
SESSION_THREADS = {}


def get_or_create_thread(session_id: str) -> str:
    if session_id in SESSION_THREADS:
        return SESSION_THREADS[session_id]

    thread = project_client.agents.threads.create()
    SESSION_THREADS[session_id] = thread.id
    return thread.id


def allowed_file(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)

# =====================================================
# 🌐 ROUTES
# =====================================================


@app.route("/")
def index():
    return app.send_static_file("index.html")

# ---------------- CHAT ----------------


@app.post("/chat")
def chat():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    user_message = (data.get("message") or "").strip()

    if not session_id or not user_message:
        return jsonify({"error": "session_id and message required"}), 400

    try:
        thread_id = get_or_create_thread(session_id)

        project_client.agents.messages.create(
            thread_id=thread_id,
            role=MessageRole.USER,
            content=user_message,
        )

        project_client.agents.runs.create_and_process(
            thread_id=thread_id,
            agent_id=AGENT_ID,
        )

        messages = list(
            project_client.agents.messages.list(
                thread_id=thread_id,
                order=ListSortOrder.ASCENDING
            )
        )

        reply = "No response from agent"
        for msg in reversed(messages):
            if msg.role == "assistant" and msg.text_messages:
                reply = msg.text_messages[-1].text.value
                break

        return jsonify({"reply": reply})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------------- FILE UPLOAD ----------------


@app.post("/upload")
def upload():
    session_id = request.form.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    f = request.files["file"]

    if not allowed_file(f.filename):
        return jsonify({"error": "Invalid file type"}), 400

    blob_path = (
        f"sessions/{session_id}/uploads/"
        f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    )

    try:
        blob_client = container_client.get_blob_client(blob_path)
        blob_client.upload_blob(f, overwrite=True)

        return jsonify({
            "filename": f.filename,
            "blob_url": blob_client.url
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------------- PDF ----------------


@app.post("/download_pdf")
def download_pdf():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    if not text:
        return jsonify({"error": "No text"}), 400

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    for line in text.split("\n"):
        story.append(Paragraph(line, styles["BodyText"]))
        story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="agent_response.pdf",
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------- RUN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
