FROM python:3.11-slim

WORKDIR /app

# Dependency layer first for build caching; only pyproject.toml is needed to resolve deps.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# The dataset/docs directory is not required at runtime (the app is dataset-agnostic; the
# judge pushes all context over HTTP) and is deliberately not copied into the image.

EXPOSE 8000

# $PORT is read at runtime (Render/Railway/Fly-style convention), defaulting to 8000 so the
# image also runs correctly with a plain `docker run -p 8000:8000`. 0.0.0.0 is required to be
# reachable from outside the container. No --reload (dev-only).
CMD ["sh", "-c", "uvicorn vera.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
