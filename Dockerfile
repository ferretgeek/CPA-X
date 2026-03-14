FROM python:3.11-slim

WORKDIR /app

# System deps (minimal)
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV CLIPROXY_PANEL_BIND_HOST=0.0.0.0
ENV CLIPROXY_PANEL_PANEL_PORT=8080

EXPOSE 8080

CMD ["python", "app.py"]
