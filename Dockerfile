# AI Context Hub — imagen para Dokploy.
#
# Contiene: el backend FastAPI+MCP, la wiki de lineamientos bundleada
# (wiki_data/, congelada en build time) y el binario Linux oficial de
# codebase-memory-mcp (motor de grafo), variante UI (necesaria para el
# grafo 3D navegable).

FROM python:3.12-slim

# git: para clonar repos registrados por URL (se borran tras indexar, ver
#      app/main.py — solo nos interesa el grafo resultante)
# curl/ca-certificates: requeridos por install.sh de cbm
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Binario oficial de codebase-memory-mcp (release Linux, variante UI, build portable)
RUN curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh \
    | bash -s -- --ui --dir=/usr/local/bin --skip-config
ENV CBM_BIN=/usr/local/bin/codebase-memory-mcp

# uv: gestor de proyecto Python usado en este repo
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# WIKI_MICROSERVICIO_DIR y demás defaults ya resuelven bien solos (ver
# app/config.py, calculados relativos al paquete) — no hace falta
# setearlos acá salvo que quieras pisarlos. GRAPH_UI_PUBLIC_URL sí conviene
# pasarla en Dokploy (ver README) porque depende del dominio del deploy.

EXPOSE 8000
EXPOSE 9749

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
