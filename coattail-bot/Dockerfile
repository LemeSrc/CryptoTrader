FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[brokers,llm]"

COPY config ./config
COPY deploy ./deploy

# Datenbank und Kurscache liegen im Volume und ueberleben ein Update.
RUN mkdir -p /app/data /app/logs && useradd -m -u 1000 coattail \
 && chown -R coattail:coattail /app
USER coattail

HEALTHCHECK --interval=5m --timeout=30s --start-period=1m --retries=3 \
  CMD coattail doctor > /dev/null || exit 1

CMD ["coattail", "run"]
