"""Cliente mínimo de la API REST de GitHub.

Solo lo que necesita el hub: registrar/borrar el webhook de un repo y comentar el
veredicto en un PR. El token llega siempre por parámetro (lo resuelve
credenciales.token_para_clonar), así que este módulo **no sabe** si detrás hay un
PAT o una GitHub App — cuando migremos a App, acá no se toca nada.

Todas las funciones aceptan `cliente` (un httpx.Client) para poder inyectar un
transporte simulado en los tests y no depender de credenciales reales.
"""

import re

import httpx

API = "https://api.github.com"
TIMEOUT = 20.0
VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    pass


def parse_owner_repo(url: str) -> tuple[str, str]:
    """'https://github.com/owner/repo.git' -> ('owner', 'repo'). Acepta también
    la forma SSH (git@github.com:owner/repo.git) y con o sin barra final."""
    m = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise GitHubError(f"No se pudo extraer owner/repo de la URL: {url!r}")
    return m.group(1), m.group(2)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": VERSION,
    }


def _pedir(
    metodo: str,
    ruta: str,
    token: str,
    json: dict | None = None,
    *,
    cliente: httpx.Client | None = None,
) -> dict:
    propio = cliente is None
    c = cliente or httpx.Client(timeout=TIMEOUT)
    try:
        r = c.request(metodo, f"{API}{ruta}", headers=_headers(token), json=json)
    except httpx.HTTPError as e:
        raise GitHubError(f"Error de red hablando con GitHub: {e}") from e
    finally:
        if propio:
            c.close()

    if r.status_code >= 400:
        try:
            detalle = r.json().get("message", "")
        except Exception:
            detalle = r.text[:200]
        raise GitHubError(f"GitHub {metodo} {ruta} → {r.status_code}: {detalle}")

    return r.json() if r.content else {}


def crear_webhook(
    owner: str, repo: str, token: str, url: str, secret: str, *, cliente=None
) -> int:
    """Registra el webhook (push + pull_request) y devuelve su id en GitHub.
    Requiere permiso Webhooks:write en el token."""
    data = _pedir(
        "POST",
        f"/repos/{owner}/{repo}/hooks",
        token,
        json={
            "name": "web",
            "active": True,
            "events": ["push", "pull_request"],
            "config": {
                "url": url,
                "content_type": "json",
                "secret": secret,
                "insecure_ssl": "0",
            },
        },
        cliente=cliente,
    )
    return int(data["id"])


def borrar_webhook(owner: str, repo: str, token: str, hook_id: int, *, cliente=None) -> None:
    _pedir("DELETE", f"/repos/{owner}/{repo}/hooks/{hook_id}", token, cliente=cliente)


def comentar_pr(
    owner: str, repo: str, token: str, pr_numero: int, cuerpo: str, *, cliente=None
) -> str:
    """Comenta en un PR (los PR son issues para la API de comentarios) y devuelve
    la URL del comentario."""
    data = _pedir(
        "POST",
        f"/repos/{owner}/{repo}/issues/{pr_numero}/comments",
        token,
        json={"body": cuerpo},
        cliente=cliente,
    )
    return data.get("html_url", "")
