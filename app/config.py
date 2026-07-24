"""Configuración centralizada — única fuente de verdad para las variables de
entorno que usa el hub. Nada más en el proyecto debe leer os.environ directo;
todo pasa por `settings`.
"""

import platform
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_cbm_bin() -> str:
    if platform.system() == "Windows":
        import os

        return os.path.expandvars(r"%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe")
    return "codebase-memory-mcp"  # resuelto por PATH (ver Dockerfile / install.sh oficial)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Motor de grafo (cbm)
    cbm_bin: str = _default_cbm_bin()
    # Puerto donde cbm sirve su UI (loopback). En el contenedor se mueve a 9750
    # porque el relay socat toma el externo. En local sin Docker, cbm escucha
    # directo acá y no hay relay.
    cbm_ui_port: int = 9749
    # Puerto público/externo por el que se llega a la UI del grafo (el que
    # publica Docker/Dokploy). En local == cbm_ui_port; en el contenedor es 9749
    # mientras cbm queda en 9750 detrás del relay.
    cbm_ui_external_port: int = 9749
    graph_ui_public_url: str = ""  # si queda vacío, se arma con el puerto externo

    # Timeouts de cbm (segundos). El indexado de un repo grande tarda mucho más
    # que una consulta puntual (search/arch/snippet), por eso van separados.
    cbm_cli_timeout: int = 120
    cbm_index_timeout: int = 900

    # Lineamientos (wiki bundleada)
    wiki_microservicio_dir: str = str(PROJECT_ROOT / "wiki_data" / "Microservicio")

    # Base de datos (Postgres/ParadeDB). En producción apunta al host interno de
    # Dokploy; en desarrollo, a un Postgres local. Todo pasa por acá, nadie lee
    # os.environ directo. Sin esto, la capa de datos falla al primer uso.
    database_url: str = ""

    # Clave Fernet para cifrar secretos en la BD (PAT, webhook secrets). Vive en
    # el entorno, nunca en Postgres: si se filtra la BD, no hay secretos en claro.
    # Generar: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credentials_key: str = ""

    # URL pública de la app — con esto se arma la URL del webhook que se registra
    # en GitHub (ej. https://governdevs.midominio.io). Sin esto no hay auto-registro.
    public_base_url: str = ""

    # Auditoría vía Claude (REST /audit, apagado si no hay key)
    anthropic_api_key: str | None = None

    # Tamaño máximo del ZIP de lineamientos que se acepta subir, en MB. La
    # subida se escribe a disco por partes (nunca entera en memoria) y del ZIP
    # solo se extraen los .md, así que un archivo grande no se traduce en un
    # consumo grande. Pasado el límite se responde 413 con un mensaje claro.
    max_upload_mb: int = 500

    # Rutas en disco.
    #
    # No hay `data_dir`: existía para las bases SQLite (repos.db, wiki_index.db)
    # y quedó sin uso al migrar a Postgres. En el contenedor /app/data sigue
    # siendo el volumen — pero por HOME (ahí guarda cbm el grafo) y porque
    # WORKSPACE_DIR cuelga de él, no por esta configuración.
    workspace_dir: str = str(PROJECT_ROOT / "workspace")
    static_dir: str = str(PROJECT_ROOT / "static")

    @property
    def graph_ui_url(self) -> str:
        return self.graph_ui_public_url or f"http://localhost:{self.cbm_ui_external_port}"


settings = Settings()
