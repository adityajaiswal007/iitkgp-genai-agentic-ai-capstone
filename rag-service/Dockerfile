# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract is only used for PDF pages without a text layer (OCR fallback).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install ".[ocr]"

RUN useradd --create-home --uid 10001 rag && mkdir -p /index && chown rag /index
USER rag

ENV RAG_DATA_DIR=/data RAG_INDEX_DIR=/index RAG_AUTO_BUILD=1
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "regassist_rag.api:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
