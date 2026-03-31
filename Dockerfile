FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN set -eux; \
    apt-get update -o Acquire::Retries=3; \
    apt-get install -y --no-install-recommends ffmpeg; \
    rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    pip install --upgrade pip >/tmp/pip-upgrade.log 2>&1 || (cat /tmp/pip-upgrade.log && exit 1); \
    pip install --retries 5 --timeout 120 -r requirements.txt >/tmp/pip-install.log 2>&1 || (tail -n 200 /tmp/pip-install.log && exit 1)

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
