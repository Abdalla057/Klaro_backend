FROM python:3.14-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install -r requirements.txt


FROM python:3.14-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 django \
    && useradd --system --uid 10001 --gid django --home-dir /app django

COPY --from=builder /install /usr/local

COPY . .

RUN SECRET_KEY=build-only \
    BD_NAME=build \
    BD_USER=build \
    BD_PASSWORD=build \
    BD_HOST=localhost \
    BD_PORT=5432 \
    python manage.py collectstatic --noinput \
    && chown -R django:django /app

USER django

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --worker-class gthread --workers ${WEB_CONCURRENCY:-2} --threads ${WEB_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-120} --graceful-timeout 60 --keep-alive 5 --max-requests 1000 --max-requests-jitter 100 --error-logfile - --log-level info"]
