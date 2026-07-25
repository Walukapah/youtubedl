FROM python:3.11-slim

# ffmpeg install කරනවා (video/audio merge කරන්න && MP3 convert කරන්න)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# Render PORT env variable එක use කරනවා
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 300 --workers 2 app:app"]
