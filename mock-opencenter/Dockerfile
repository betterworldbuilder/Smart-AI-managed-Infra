FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY mock-opencenter/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY mock-opencenter/app.py /srv/app.py

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=10 \
    CMD curl -fsS http://localhost:8080/api/v1/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
