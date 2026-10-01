FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets $PORT; gunicorn binds to it.
# --timeout 120: the default is 30s, which the PDF data export (fetches and embeds
# report photos) can exceed on a slow connection even with concurrent fetching —
# a killed worker shows up to the user as a 502 from the platform's proxy.
CMD exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120 app:app
