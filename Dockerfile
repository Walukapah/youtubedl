FROM python:3.11-slim

# ffmpeg install කරනවා (video/audio merge && MP3 convert)
# curl unzip Deno install කරන්න ඕන නිසා එකතු කරනවා
RUN apt-get update && apt-get install -y --no-install-recommends     ffmpeg     curl     unzip     ca-certificates     && rm -rf /var/lib/apt/lists/*

# ===== DENO INSTALL =====
# apt install deno කියලා direct install කරන්න බැහැ Debian repos තුළ නැති නිසා
# Official Deno installer එක use කරනවා
RUN curl -fsSL https://deno.land/install.sh | sh &&     mv /root/.deno/bin/deno /usr/local/bin/deno &&     deno --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV DENO_INSTALL=/root/.deno
ENV PATH="/root/.deno/bin:${PATH}"

# Render PORT env variable එක use කරනවා
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 300 --workers 2 app:app"]
