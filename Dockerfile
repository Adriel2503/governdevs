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
#   socat               -> relay TCP que publica la UI de cbm (atada a loopback)
#                          en todas las interfaces (ver docker-entrypoint.sh)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates socat \
    && rm -rf /var/lib/apt/lists/*

# Binario oficial de codebase-memory-mcp (release Linux, variante UI, build portable)
RUN curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh \
    | bash -s -- --ui --dir=/usr/local/bin --skip-config
ENV CBM_BIN=/usr/local/bin/codebase-memory-mcp
# cbm sirve su UI en 127.0.0.1:${CBM_UI_PORT}; el relay del entrypoint la expone
# en 0.0.0.0:${CBM_UI_EXTERNAL_PORT}. Van en puertos distintos para no chocar.
ENV CBM_UI_PORT=9750 \
    CBM_UI_EXTERNAL_PORT=9749

# uv pineado (no :latest) para builds reproducibles
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Dependencias primero (capa cacheada mientras pyproject.toml/uv.lock no cambien)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Código de la app
COPY app ./app
COPY static ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

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

ENTRYPOINT ["docker-entrypoint.sh"]
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
