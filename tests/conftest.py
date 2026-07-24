"""Aísla la suite del .env del desarrollador.

El .env del proyecto es el borrador de PRODUCCIÓN (WORKSPACE_DIR=/app/data/...,
DATABASE_URL apuntando al host interno de Dokploy). pydantic-settings lo lee al
importar app.config, así que sin esto los tests dependen de qué tenga cada uno
en su máquina — e importar app.main reventaba al intentar crear /app/data.

Las variables de entorno REALES tienen prioridad sobre el .env, y setdefault
respeta las que ya estén exportadas (por ejemplo DATABASE_URL para los tests
que necesitan Postgres). Tiene que correr antes de cualquier import de app/.
"""

import os
import tempfile
from pathlib import Path

_BASE = Path(tempfile.gettempdir()) / "context-hub-tests"
(_BASE / "workspace").mkdir(parents=True, exist_ok=True)
(_BASE / "data").mkdir(parents=True, exist_ok=True)

os.environ.setdefault("WORKSPACE_DIR", str(_BASE / "workspace"))
os.environ.setdefault("DATA_DIR", str(_BASE / "data"))
