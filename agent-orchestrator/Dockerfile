FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_THREAD_LIMIT=1
# Optional corporate-network CA is supplied as a BuildKit secret, never baked into the image.
RUN --mount=type=secret,id=build_ca_bundle,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && build_ca_option="" \
    && if [ -s /run/secrets/build_ca_bundle ]; then build_ca_option="-o Acquire::https::CaInfo=/run/secrets/build_ca_bundle"; fi \
    && apt-get ${build_ca_option} update \
    && apt-get ${build_ca_option} install -y --no-install-recommends tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ ./src/
ARG QWEN_TOKENIZER_MODEL=Qwen/Qwen3-8B
ENV QWEN_TOKENIZER=/opt/qwen-tokenizer \
    HF_HUB_DISABLE_XET=1
RUN python -m pip install . \
    && python -m pip check \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /data /workspace \
    && chown agent:agent /data /workspace
RUN --mount=type=secret,id=build_ca_bundle,mode=0444 \
    if [ -s /run/secrets/build_ca_bundle ]; then export REQUESTS_CA_BUNDLE=/run/secrets/build_ca_bundle; fi \
    && python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('${QWEN_TOKENIZER_MODEL}', trust_remote_code=False).save_pretrained('/opt/qwen-tokenizer')"

FROM base AS test
COPY tests/ ./tests/
RUN python -m pip install ".[test]" \
    && python -m pytest -q \
    && touch /tmp/tests-passed

# Optional: docker build --target notebook -t regulatory-agent:notebook .
FROM base AS notebook
RUN python -m pip install ".[notebook]"
COPY --chown=agent:agent regulatory_agentic_layer_qwen3.ipynb validate_notebook.py /workspace/
WORKDIR /workspace
USER agent
RUN python validate_notebook.py
EXPOSE 8888
CMD ["python", "-m", "jupyterlab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--ServerApp.root_dir=/workspace"]

# Default: API image. Copying the marker requires the offline test stage to pass.
FROM base AS api
COPY --from=test /tmp/tests-passed /tmp/tests-passed
ENV AGENT_DATABASE_PATH=/data/conversations.sqlite
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "regulatory_agent.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
