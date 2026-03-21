# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=120

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 10 -r requirements.txt

COPY bot/ ./bot/
COPY main.py .
COPY ops/ ./ops/

RUN mkdir -p /app/data
RUN python -m compileall bot main.py

ENV LOG_FILE=/app/data/trades.json
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
