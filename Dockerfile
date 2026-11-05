# syntax=docker/dockerfile:1

# --- build stage: install wheels into a separate prefix --------------------
FROM python:3.12-alpine AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.txt

# --- runtime stage ---------------------------------------------------------
FROM python:3.12-alpine
LABEL org.opencontainers.image.title="Last Call" \
      org.opencontainers.image.description="E-mail digests of what is leaving (Maintainerr) and what is new on Jellyfin" \
      org.opencontainers.image.source="https://github.com/flowoy96/last-call" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LASTCALL_DATA=/data \
    TZ=Europe/Berlin

RUN addgroup -S -g 1000 lastcall \
 && adduser -S -D -H -u 1000 -G lastcall lastcall \
 && mkdir -p /data \
 && chown lastcall:lastcall /data

COPY --from=build /install /usr/local
COPY app /app/app

WORKDIR /app
USER lastcall
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/health >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-server-header"]
