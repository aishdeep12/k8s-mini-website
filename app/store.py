"""Shared Redis connection, used by both the web pods and the OCR worker pods.

Redis plays two roles here:
  1. the Dramatiq job queue (see tasks.py)
  2. a small store for uploaded files and job status/results

Pods do not share disks, so the web pod cannot just save the upload to its local disk:
the OCR worker pod would never see it. For this demo, small uploads (max 5 MB) go into Redis
with an expiry. In production you would put files in S3 / MinIO / blob storage and pass the key.
"""
import os

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

FILE_TTL = 60 * 60          # uploaded files expire after 1 hour
JOB_TTL = 24 * 60 * 60      # job status/results expire after 1 day

_client = None


def get_redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL)
    return _client


def set_client(client):
    """Used by the tests to swap in a fake Redis."""
    global _client
    _client = client


def file_key(job_id):
    return f"file:{job_id}"


def job_key(job_id):
    return f"job:{job_id}"


def save_job(job_id, **fields):
    r = get_redis()
    r.hset(job_key(job_id), mapping={k: str(v) for k, v in fields.items()})
    r.expire(job_key(job_id), JOB_TTL)


def load_job(job_id):
    data = get_redis().hgetall(job_key(job_id))
    return {k.decode(): v.decode() for k, v in data.items()}
