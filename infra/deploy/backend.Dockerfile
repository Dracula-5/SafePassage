# Backend image: API, ingestion worker and brain worker share it; docker-compose.prod.yml or fly.toml
# picks the process. Build from the repo root: docker build -f infra/deploy/backend.Dockerfile .
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

WORKDIR /app
COPY contracts ./contracts
COPY backend/requirements ./backend/requirements
# constraints.txt pins every package, so a rebuild can't quietly pull in something new.
RUN pip install --no-cache-dir -c backend/requirements/constraints.txt -e ./contracts/python \
    -r backend/requirements/brain.txt -r backend/requirements/senses.txt
COPY backend/app ./backend/app
COPY backend/migrations ./backend/migrations
COPY driver-pwa ./driver-pwa

# documents must exist here: a new named volume takes its owner from the image, or PO PDFs can't be written.
RUN useradd --create-home relief && mkdir -p /data/documents && chown -R relief /data
USER relief
WORKDIR /app/backend
ENV HOME=/home/relief PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--timeout-keep-alive", "10"]
