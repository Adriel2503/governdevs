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
    cbm_ui_port: int = 9749
    graph_ui_public_url: str = ""  # se resuelve con cbm_ui_port si queda vacío

    # Timeouts de cbm (segundos). El indexado de un repo grande tarda mucho más
    # que una consulta puntual (search/arch/snippet), por eso van separados.
    cbm_cli_timeout: int = 120
    cbm_index_timeout: int = 900

    # Lineamientos (wiki bundleada)
    wiki_microservicio_dir: str = str(PROJECT_ROOT / "wiki_data" / "Microservicio")

    # Auditoría vía Claude (REST /audit, apagado si no hay key)
    anthropic_api_key: str | None = None

    # Persistencia y datos temporales
    data_dir: str = str(PROJECT_ROOT / "data")
    workspace_dir: str = str(PROJECT_ROOT / "workspace")
    static_dir: str = str(PROJECT_ROOT / "static")

    @property
    def graph_ui_url(self) -> str:
        return self.graph_ui_public_url or f"http://localhost:{self.cbm_ui_port}"


settings = Settings()
