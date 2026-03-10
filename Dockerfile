FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY main.py .

RUN mkdir -p /app/data

ENV LOG_FILE=/app/data/trades.json
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
