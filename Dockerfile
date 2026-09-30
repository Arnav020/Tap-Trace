# TapTrace - Smart Guided Troubleshooting Engine (Samsung PRISM GenAI Hackathon 3.0, Theme 02)
# CPU-only image. The INT8 ONNX embedder and the pre-warmed semantic cache are baked in, so the
# container answers cached scenarios immediately (no model download at start-up, no API key needed).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TAPTRACE_EMBED_THREADS=2

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY taptrace/ taptrace/
COPY data/ data/
COPY demo/ demo/
COPY scripts/ scripts/
COPY eval/ eval/
COPY artifacts/model/ artifacts/model/
COPY artifacts/cache.sqlite artifacts/cache.sqlite
COPY results.jsonl metrics.md ./

RUN useradd --create-home --uid 1000 taptrace && chown -R taptrace /app
USER taptrace

# Cloud hosts (Render, Railway, Cloud Run, Hugging Face Spaces) inject PORT; locally it defaults to 8000.
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000')).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn taptrace.api:app --host 0.0.0.0 --port ${PORT} --workers 1"]
