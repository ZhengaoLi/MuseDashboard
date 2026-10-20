FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir fastapi==0.115.6 uvicorn==0.32.1 tzdata==2024.2 pytest==8.3.4 httpx==0.28.1 playwright==1.49.1 \
 && playwright install --with-deps chromium && chmod -R a+rX /ms-playwright && rm -rf /var/lib/apt/lists/*
COPY ref /opt/ref
COPY checks.py /opt/validator/checks.py
WORKDIR /opt/ref
