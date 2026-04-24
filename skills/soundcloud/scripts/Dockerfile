FROM miniclaw/base:latest
RUN apt-get update && apt-get install -y mpv ffmpeg libpulse0 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
