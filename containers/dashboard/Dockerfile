FROM miniclaw/base:latest

RUN pip install --no-cache-dir flask yfinance playwright requests

# Install Chromium and its system dependencies for news scraping
RUN playwright install chromium && playwright install-deps chromium

COPY app.py /app/app.py
COPY templates/ /app/templates/

WORKDIR /app
CMD ["python", "app.py"]
