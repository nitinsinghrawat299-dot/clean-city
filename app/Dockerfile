FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT; gunicorn binds to it.
# --timeout 120: report submissions upload photos/video to Cloudinary, which can exceed
# gunicorn's default 30s on a slow mobile connection.
CMD exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120 app:app
