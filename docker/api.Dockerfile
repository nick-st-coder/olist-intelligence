# Prediction API image.
#
# The image carries only what serving needs: the model artefact, its scoring contract and the
# feature-building code. It installs the ``serving`` dependency group, which declares its own
# runtime packages, rather than the whole project — the training stack (LightGBM, XGBoost,
# Optuna, MLflow) and the cloud clients are not needed to score an order.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# libgomp is required by the BLAS-backed numerics in scikit-learn; the model artefact was fitted
# with it present, so the runtime must have it too.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# An explicit virtual environment rather than ``--system``: it pins the interpreter uvicorn runs
# under, so a base image that already ships its own venv cannot shadow the installed packages.
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --upgrade pip uv

# Dependencies first, so a code change does not invalidate the dependency layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv pip install --no-cache --group serving

# The analytical dataset is deliberately not copied: the seller history and SHAP background are
# optional, and a mounted volume supplies them when they are wanted. Without them the service
# still scores orders. The directory must exist because the project locates its root by the
# presence of data/ beside pyproject.toml.
RUN mkdir -p data/processed/modeling

COPY src ./src
COPY api ./api

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
