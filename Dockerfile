FROM python:3.11-slim

WORKDIR /app

# ffmpeg + curl install කරනවා
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp latest version එකට update කරනවා build time එකේදී
RUN pip install --upgrade yt-dlp

COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
