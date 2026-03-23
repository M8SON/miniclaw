FROM python:3.11-slim
RUN pip install --no-cache-dir requests
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
