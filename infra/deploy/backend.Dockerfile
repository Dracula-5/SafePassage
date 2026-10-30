# Backend image: API, ingestion worker and brain worker share it; docker-compose.prod.yml or fly.toml
# picks the process. Build from the repo root: docker build -f infra/deploy/backend.Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY contracts ./contracts
COPY backend/requirements ./backend/requirements
RUN pip install --no-cache-dir -e ./contracts/python \
    -r backend/requirements/brain.txt -r backend/requirements/senses.txt
COPY backend/app ./backend/app
COPY backend/migrations ./backend/migrations
COPY driver-pwa ./driver-pwa

RUN useradd --create-home relief && mkdir -p /data && chown relief /data
USER relief
WORKDIR /app/backend
ENV HOME=/home/relief
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
