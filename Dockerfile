# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    DB_PATH=/app/state/serp_monitor.sqlite3

WORKDIR /app

RUN groupadd -g "${GID}" app && \
    useradd -u "${UID}" -g "${GID}" -m -d /home/app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY tests/ ./tests/
COPY data/fixtures/ ./data/fixtures/
COPY docs/ ./docs/

# /app/state — SQLite database (named volume), /app/output — JSON/CSV export
RUN mkdir -p /app/state /app/output && chown -R app:app /app

USER app

# The `serve` dashboard listens here; publish it with -p 8000:8000.
EXPOSE 8000

ENTRYPOINT ["python", "-m", "brand_serp.cli"]
CMD ["run", "--source", "fixture", "--out", "/app/output"]
