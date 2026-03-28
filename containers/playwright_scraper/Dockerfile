FROM miniclaw/base:latest
RUN pip install --no-cache-dir playwright
RUN playwright install --with-deps chromium
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
