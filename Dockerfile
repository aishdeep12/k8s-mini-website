FROM python:3.12-slim

# Links the image in GitHub Container Registry back to this repo.
LABEL org.opencontainers.image.source="https://github.com/aishdeep12/k8s-mini-website"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Tesseract is the OCR engine used by the worker pods.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

# The commit SHA, passed in by the pipeline, shows up on the web page.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Run as a non-root user.
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8080

# One image, two roles:
#   web pods (default):  gunicorn serving the Flask app
#   OCR worker pods:     k8s/ocr-worker.yaml overrides this with `dramatiq tasks ...`
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "main:app"]
