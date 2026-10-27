FROM python:3.12-slim AS platform
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PLATFORM_ENV=docker
WORKDIR /app
ARG API_REQUIREMENTS=backend/requirements.lock
COPY ${API_REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd -u 1000 -m sonder
COPY backend/src /app/src
COPY backend/alembic.ini /app/
COPY backend/migrations /app/migrations
RUN mkdir -p /data/uploads /data/runs && chown -R sonder:sonder /data
USER sonder
CMD ["uvicorn", "sonder.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM platform AS worker
CMD ["python", "-m", "sonder.worker"]

FROM python:3.12-slim AS executor
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/runner/src
WORKDIR /opt/runner
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
ARG EXECUTOR_REQUIREMENTS=executor/requirements.lock
COPY ${EXECUTOR_REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY executor/src /opt/runner/src
CMD ["uvicorn", "sonder_executor.service:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
