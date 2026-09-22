FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY service.py world.json ./

# Hugging Face Spaces expects port 7860. One worker (single instance by requirement),
# four threads so a slow model call on one order does not hold the others past the arena's 10 s.
ENV PORT=7860
CMD gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT service:app
