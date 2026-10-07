FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY telok ./telok
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 telok && mkdir /app/data /app/.secrets && chmod 700 /app/.secrets && chown telok:telok /app/data /app/.secrets
USER telok
CMD ["python","-m","uvicorn","telok.api:app","--host","0.0.0.0","--port","8481","--no-access-log"]
