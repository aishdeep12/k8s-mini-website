"""Background jobs, run by the OCR worker pods.

Web pods only call `run_ocr.send(...)`, which puts a message on the Redis queue and returns
immediately. The OCR worker pods run `dramatiq tasks`, which pulls messages off the queue
and calls `run_ocr(...)`.
"""
import datetime
import os
import socket
import time

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker

import ocr_engine
import store

if os.getenv("DRAMATIQ_BROKER") == "stub":      # unit tests: in-memory queue, no Redis needed
    broker = StubBroker()
    broker.emit_after("process_boot")
else:
    broker = RedisBroker(url=store.REDIS_URL)
dramatiq.set_broker(broker)

WORKER_POD = os.getenv("POD_NAME", socket.gethostname())


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dramatiq.actor(queue_name="ocr", max_retries=3, time_limit=2 * 60 * 1000)
def run_ocr(job_id: str):
    r = store.get_redis()
    store.save_job(job_id, status="running", worker_pod=WORKER_POD, started_at=_now())

    image_bytes = r.get(store.file_key(job_id))
    if image_bytes is None:
        store.save_job(job_id, status="failed", error="uploaded file expired or missing")
        return

    t0 = time.perf_counter()
    try:
        text = ocr_engine.extract_text(image_bytes)
    except Exception as exc:  # noqa: BLE001 - record any OCR failure on the job
        store.save_job(job_id, status="failed", error=str(exc))
        raise  # let Dramatiq retry

    store.save_job(
        job_id,
        status="done",
        text=text,
        seconds=round(time.perf_counter() - t0, 2),
        finished_at=_now(),
    )
    r.delete(store.file_key(job_id))
