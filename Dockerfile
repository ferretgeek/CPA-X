FROM python:3.11-slim

WORKDIR /app

# System deps (minimal)
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Runtime image contains only executable assets. In particular, a developer's
# local .env (often holding management keys) must never be baked into a layer.
COPY app.py X.txt /app/
COPY static /app/static

ENV CLIPROXY_PANEL_BIND_HOST=0.0.0.0
ENV CLIPROXY_PANEL_PANEL_PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; p=os.getenv('CLIPROXY_PANEL_PANEL_PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/healthz',timeout=3).read()"]

CMD ["python", "app.py"]
