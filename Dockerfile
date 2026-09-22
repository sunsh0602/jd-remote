FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
RUN useradd -r -u 10001 jdr && chown -R jdr:jdr /srv
USER jdr
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status in (200,503) else 1)"
# --proxy-headers: DSM 리버스 프록시(127.0.0.1)에서 오는 X-Forwarded-* 를 신뢰
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
