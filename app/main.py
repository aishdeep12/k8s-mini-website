import datetime
import os
import socket
import uuid

from flask import Flask, jsonify, render_template, request

import store
from tasks import run_ocr

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024      # 5 MB upload limit

# APP_VERSION is baked into the image at build time (the git commit SHA).
# POD_NAME / NODE_NAME are injected by Kubernetes (see k8s/deployment.yaml).
VERSION = os.getenv("APP_VERSION", "dev")
POD_NAME = os.getenv("POD_NAME", socket.gethostname())
NODE_NAME = os.getenv("NODE_NAME", "local")
STARTED_AT = datetime.datetime.now(datetime.timezone.utc)

ALLOWED = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}


@app.get("/")
def index():
    return render_template("index.html", version=VERSION, pod=POD_NAME, node=NODE_NAME)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/api/info")
def info():
    return jsonify(
        version=VERSION,
        pod=POD_NAME,
        node=NODE_NAME,
        started_at=STARTED_AT.isoformat(),
    )


@app.post("/api/ocr")
def submit_ocr():
    """Accept an image, queue an OCR job, and return straight away with a job id."""
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        return jsonify(error="send an image in the 'file' form field"), 400
    ext = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if ext not in ALLOWED:
        return jsonify(error=f"unsupported file type; use one of {sorted(ALLOWED)}"), 400

    job_id = uuid.uuid4().hex[:12]
    r = store.get_redis()
    r.set(store.file_key(job_id), upload.read(), ex=store.FILE_TTL)   # 1) store the file
    store.save_job(job_id, status="queued", filename=upload.filename,
                   api_pod=POD_NAME, queued_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    run_ocr.send(job_id)                                               # 2) put a job on the queue
    return jsonify(job_id=job_id, status="queued", api_pod=POD_NAME), 202   # 3) reply right away


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = store.load_job(job_id)
    if not job:
        return jsonify(error="job not found"), 404
    return jsonify(job_id=job_id, **job)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="file too large (max 5 MB)"), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
