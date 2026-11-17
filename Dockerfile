FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# Run as an unprivileged user, not root.
RUN useradd --system --uid 10001 appuser
USER appuser

WORKDIR /srv/app
EXPOSE 8000

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Docker marks the container unhealthy if /health stops returning 200.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# --preload runs init_db() once in the master before workers fork.
CMD ["gunicorn", "--preload", "--workers", "2", "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", "--timeout", "30", "wsgi:app"]
