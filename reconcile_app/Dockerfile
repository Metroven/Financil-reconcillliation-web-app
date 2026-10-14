FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    RECON_DATA_DIR=/data COOKIE_SECURE=1 TRUST_PROXY=1 PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/healthz')"
CMD ["sh", "-c", "exec gunicorn -w 2 --threads 4 --timeout 120 -b 0.0.0.0:${PORT} app:app"]
