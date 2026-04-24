FROM miniclaw/base:latest

RUN pip install --no-cache-dir flask yfinance feedparser requests

COPY app.py /app/app.py
COPY eonet.py /app/eonet.py
COPY dashboard_defaults.py /app/dashboard_defaults.py
COPY templates/ /app/templates/

WORKDIR /app
CMD ["python", "app.py"]
