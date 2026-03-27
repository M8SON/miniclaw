FROM python:3.11-slim
RUN apt-get update && apt-get install -y nodejs bash curl && rm -rf /var/lib/apt/lists/*
COPY app.py /app/app.py
WORKDIR /app
CMD ["python3", "app.py"]
