# syntax=docker/dockerfile:1
# AI Context Hub — imagen para Dokploy.
#
# Empaqueta: backend FastAPI+MCP, el binario Linux oficial de codebase-memory-mcp
# (variante UI, para el grafo 3D) y git (para clonar repos por URL en runtime).
# Los lineamientos (wiki_data/) NO se hornean acá: son confidenciales y se
# proveen por volumen de solo-lectura (ver compose.yaml / README).

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    TZ=America/Lima

WORKDIR /app

# Dependencias de sistema:
#   git                 -> clonar repos registrados por URL (se borran tras indexar)
#   curl/ca-certificates -> requeridos por el install.sh de cbm
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Binario oficial de codebase-memory-mcp (release Linux, variante UI, build portable).
#
# PINEADO A UN TAG, no a main. El install.sh de main cambió y pasó a delegar la
# instalación en el propio binario (`cbm install --dir=...`); con el release 0.9.0
# eso rompe el build por dos motivos: el auto-instalador ignora --dir (deja el
# binario en ~/.local/bin y la verificación de /usr/local/bin falla) y además
# invoca `pgrep`, que no existe en python:slim. El script del tag hace un `cp`
# directo y no depende de nada de eso.
#
# Se fijan LAS DOS cosas al mismo release: el script y los binarios que descarga
# (por defecto apuntaría a releases/latest/download). El script valida el
# checksum contra el checksums.txt del mismo tag.
ARG CBM_VERSION=v0.9.0
RUN curl -fsSL "https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/${CBM_VERSION}/install.sh" \
    | CBM_DOWNLOAD_URL="https://github.com/DeusData/codebase-memory-mcp/releases/download/${CBM_VERSION}" \
      bash -s -- --ui --dir=/usr/local/bin --skip-config
ENV CBM_BIN=/usr/local/bin/codebase-memory-mcp
# cbm sirve su UI en 127.0.0.1:${CBM_UI_PORT}; el proxy Caddy la expone en
# 0.0.0.0:${CBM_UI_EXTERNAL_PORT} y le reescribe el Host a localhost (cbm solo
# acepta ese host). Van en puertos distintos para no chocar.
ENV CBM_UI_PORT=9750 \
    CBM_UI_EXTERNAL_PORT=9749

# uv pineado (no :latest) para builds reproducibles
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Caddy pineado como binario portable: proxy HTTP que reescribe Host -> localhost
# para que cbm acepte los pedidos que llegan por el dominio publico (ver Caddyfile).
# XDG_* al volumen para que Caddy no intente escribir en / siendo no-root.
COPY --from=caddy:2.10 /usr/bin/caddy /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
ENV XDG_CONFIG_HOME=/app/data \
    XDG_DATA_HOME=/app/data

# Dependencias primero (capa cacheada mientras pyproject.toml/uv.lock no cambien)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Código de la app
COPY app ./app
COPY static ./static

# Usuario no privilegiado. HOME=/app/data para que TODO el estado (nuestras .db
# + el store del grafo que cbm guarda bajo $HOME) viva en un solo volumen y
# persista entre redeploys. data/ y workspace/ los crea y los posee appuser.
ARG UID=10001
RUN adduser --disabled-password --gecos "" --uid "${UID}" appuser \
    && mkdir -p /app/data /app/workspace \
    && chown -R appuser:appuser /app/data /app/workspace
ENV HOME=/app/data

USER appuser

EXPOSE 8000 9749

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Proxy Caddy (publica la UI del grafo y reescribe Host -> localhost) en segundo
# plano, y luego uvicorn toma el proceso principal. Forma shell para lanzar ambos
# sin un script aparte.
CMD caddy run --config /etc/caddy/Caddyfile --adapter caddyfile & \
    exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
