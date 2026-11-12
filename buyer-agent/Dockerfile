FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# A2A servers are ASGI apps (Starlette), so we run them with uvicorn instead of gunicorn/Flask.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
