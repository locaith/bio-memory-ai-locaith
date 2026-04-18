FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8055

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY bio_agent_os /app/bio_agent_os
COPY index.html /app/index.html

RUN pip install --upgrade pip \
    && pip install -e ".[ollama,async-sqlite]"

EXPOSE 8055

CMD ["bio-agent-os", "serve-api", "--host", "0.0.0.0", "--port", "8055"]
