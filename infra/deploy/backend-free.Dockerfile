# API-only image for render-free.yaml. No ingestion worker means map extracts are not needed.
FROM python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

WORKDIR /app
COPY contracts ./contracts
COPY backend/requirements ./backend/requirements
RUN pip install --no-cache-dir -c backend/requirements/constraints.txt -e ./contracts/python \
    -r backend/requirements/brain.txt -r backend/requirements/senses.txt
COPY backend/app ./backend/app
COPY backend/migrations ./backend/migrations
RUN useradd --create-home relief && mkdir -p /data/documents && chown -R relief /data
USER relief
WORKDIR /app/backend
ENV HOME=/home/relief PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
# Migrations first, because a free Render service has no pre-deploy step or shell: a service made by hand in the
# dashboard, with no Docker Command, still gets its tables. They are recorded, so a restart applies only new ones.
CMD ["sh", "-c", "python -m app.core.migrate && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header --timeout-keep-alive 10"]
