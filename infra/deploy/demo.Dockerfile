# Simulated drivers for presentations, for hosts that can't mount the tool the way docker-compose.prod.yml does.
# Its own image, so a test tool never ships inside the backend image. Build from the repo root.
FROM python:3.15-slim-bookworm@sha256:96599df822d0e1c6bb700014a05c7f3c8cabc6e0f7ff3c4f9f69cee033b4b5d9

WORKDIR /sim
COPY backend/requirements/constraints.txt ./constraints.txt
RUN pip install --no-cache-dir -c constraints.txt httpx shapely
COPY backend/tests/__init__.py ./tests/__init__.py
COPY backend/tests/tools ./tests/tools

USER nobody
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "tests.tools.demo_fleet"]
