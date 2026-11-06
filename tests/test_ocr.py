import io
import pathlib
import shutil

import pytest
from dramatiq import Worker

import ocr_engine
import store
from tasks import broker

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "hello.png"


def run_queued_jobs():
    """Act as an OCR worker pod: process everything on the in-memory queue, then stop."""
    worker = Worker(broker, worker_timeout=100)
    worker.start()
    broker.join("ocr")
    worker.join()
    worker.stop()


def upload(client, data=b"fake-image", name="scan.png"):
    return client.post("/api/ocr", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_submit_queues_job_and_returns_immediately(client):
    r = upload(client)
    assert r.status_code == 202
    body = r.get_json()
    assert body["status"] == "queued"
    # The job is waiting on the queue; no OCR has happened yet.
    assert client.get(f"/api/jobs/{body['job_id']}").get_json()["status"] == "queued"
    assert broker.queues["ocr"].qsize() == 1


def test_worker_processes_job(client, monkeypatch):
    monkeypatch.setattr(ocr_engine, "extract_text", lambda b: "HELLO FROM OCR")
    job_id = upload(client).get_json()["job_id"]

    run_queued_jobs()

    job = client.get(f"/api/jobs/{job_id}").get_json()
    assert job["status"] == "done"
    assert job["text"] == "HELLO FROM OCR"
    assert "worker_pod" in job and "api_pod" in job
    # The uploaded file is cleaned up after processing.
    assert store.get_redis().get(store.file_key(job_id)) is None


def test_rejects_missing_and_wrong_files(client):
    assert client.post("/api/ocr").status_code == 400
    assert upload(client, name="notes.txt").status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_real_tesseract_reads_fixture():
    text = ocr_engine.extract_text(FIXTURE.read_bytes())
    assert "KUBERNETES" in text.upper()
