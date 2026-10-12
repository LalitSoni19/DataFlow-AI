# Production image for the FastAPI service.  The Streamlit dashboard is
# intentionally deployed as a separate process/service so API workers do not
# compete with interactive sessions for their event loop and memory budget.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATAFLOW_PROJECT_ROOT=/app

WORKDIR /app

RUN groupadd --system dataflow && useradd --system --gid dataflow --home-dir /app dataflow

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

COPY scripts ./scripts
COPY docs ./docs
COPY streamlit_app ./streamlit_app
COPY airflow ./airflow

RUN mkdir -p /app/data /app/logs && chown -R dataflow:dataflow /app

USER dataflow
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "dataflow_ai.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
