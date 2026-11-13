# Prediction interface image.
#
# The dashboard talks to the API over HTTP, so it needs no model. It does need the reference data
# the form is built from, which is read from the analytical dataset when a volume supplies one.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

WORKDIR /app

# The numerics stack needs libgomp, and the shared feature code imports it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# An explicit virtual environment rather than ``--system``: it pins the interpreter that
# ``streamlit`` runs under, so a base image with its own venv cannot shadow the packages.
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --upgrade pip uv

COPY pyproject.toml uv.lock README.md ./
RUN uv pip install --no-cache --group serving

RUN mkdir -p data/processed

COPY src ./src
COPY app ./app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
