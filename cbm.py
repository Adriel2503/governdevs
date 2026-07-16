"""Wrapper sobre el binario codebase-memory-mcp (motor de grafo de código).

Cada función invoca `codebase-memory-mcp cli <tool> '<json>'` como subproceso
puntual. El binario imprime el resultado como un único JSON en stdout y sus
logs en stderr (verificado), así que basta con json.loads(stdout).

No reimplementamos nada del grafo aquí: este módulo es pura plomería hacia
el motor real. Si el motor cambia, solo este archivo se toca.
"""

import json
import os
import shutil
import socket
import subprocess

CBM_BIN = os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\codebase-memory-mcp\codebase-memory-mcp.exe"
)

# Referencia global al proceso de la UI 3D — debe mantenerse viva o el pipe de
# stdin se cierra y el binario se apaga (interpreta EOF en stdin como "mi host
# MCP murió"). Por eso NO se lanza con stdin=DEVNULL (EOF inmediato) sino con
# stdin=PIPE, que queda abierto indefinidamente mientras no se cierre.
_ui_process: subprocess.Popen | None = None


class CbmError(RuntimeError):
    """El binario cbm devolvió un error o una salida no parseable."""


def _run(tool: str, payload: dict | None = None) -> dict:
    args = [CBM_BIN, "cli", tool]
    if payload is not None:
        args.append(json.dumps(payload))

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        raise CbmError(
            f"cbm cli {tool} salió con código {result.returncode}: {result.stderr.strip()}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise CbmError(f"cbm cli {tool} devolvió salida no-JSON: {result.stdout!r}") from e


def is_available() -> bool:
    return shutil.which(CBM_BIN) is not None or os.path.isfile(CBM_BIN)


def index_repository(repo_path: str) -> dict:
    return _run("index_repository", {"repo_path": repo_path})


def list_projects() -> list[dict]:
    return _run("list_projects").get("projects", [])


def index_status(project: str) -> dict:
    return _run("index_status", {"project": project})


def get_architecture(project: str) -> dict:
    return _run("get_architecture", {"project": project})


def search_graph(project: str, name_pattern: str, label: str | None = None) -> dict:
    payload = {"project": project, "name_pattern": name_pattern}
    if label:
        payload["label"] = label
    return _run("search_graph", payload)


def trace_path(project: str, function_name: str, direction: str = "both") -> dict:
    return _run(
        "trace_path",
        {"project": project, "function_name": function_name, "direction": direction},
    )


def delete_project(project: str) -> dict:
    return _run("delete_project", {"project": project})


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_ui_running(port: int = 9749) -> bool:
    """Lanza `codebase-memory-mcp --ui=true --port=<port>` como proceso
    persistente si nadie está escuchando ese puerto todavía. Idempotente:
    si ya está arriba (por nosotros o por una sesión de Claude Code con el
    MCP conectado), no hace nada. Devuelve True si quedó (o ya estaba) activo.
    """
    global _ui_process

    if _port_listening(port):
        return True

    _ui_process = subprocess.Popen(
        [CBM_BIN, "--ui=true", f"--port={port}"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def get_code_snippet(project: str, qualified_name: str, include_neighbors: bool = False) -> dict:
    return _run(
        "get_code_snippet",
        {"project": project, "qualified_name": qualified_name, "include_neighbors": include_neighbors},
    )
