FROM python:3.12-slim AS platform
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api:/app PLATFORM_ENV=docker
WORKDIR /app
ARG API_REQUIREMENTS=apps/api/requirements.lock
COPY ${API_REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd -u 1000 -m sonder
COPY research_*.py /app/
COPY apps/api /app/apps/api
COPY alembic.ini /app/
COPY migrations /app/migrations
RUN mkdir -p /data/uploads /data/runs && chown -R sonder:sonder /data
USER sonder
CMD ["uvicorn", "research_platform.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

FROM platform AS worker
CMD ["python", "-m", "research_platform.worker"]

FROM python:3.12-slim AS executor
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/runner
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
ARG EXECUTOR_REQUIREMENTS=apps/executor/requirements.lock
COPY ${EXECUTOR_REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY research_worker.py /opt/runner/
COPY apps/executor/service.py /opt/runner/service.py
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
