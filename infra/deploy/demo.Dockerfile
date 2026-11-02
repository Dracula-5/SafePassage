# Simulated drivers for presentations, for hosts that can't mount the tool the way docker-compose.prod.yml does.
# Its own image, so a test tool never ships inside the backend image. Build from the repo root.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

WORKDIR /sim
COPY backend/requirements/constraints.txt ./constraints.txt
RUN pip install --no-cache-dir -c constraints.txt httpx shapely
COPY backend/tests/__init__.py ./tests/__init__.py
COPY backend/tests/tools ./tests/tools

USER nobody
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "tests.tools.demo_fleet"]
