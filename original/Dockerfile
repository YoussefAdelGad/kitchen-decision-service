FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY service.py .

ENV PORT=7860
CMD gunicorn --workers 1 --timeout 120 --bind 0.0.0.0:$PORT service:app
