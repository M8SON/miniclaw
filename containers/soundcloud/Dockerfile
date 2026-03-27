FROM python:3.11-slim
RUN apt-get update && apt-get install -y mpv ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
