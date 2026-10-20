FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MUSE_DB=/data/muse.db
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY client/museboard.py ./museboard.py
RUN useradd -u 10001 -m muse && mkdir /data && chown muse /data
USER muse
VOLUME /data
EXPOSE 5050
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:5050/api/health',timeout=3)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5050", "--workers", "1", "--no-server-header"]
