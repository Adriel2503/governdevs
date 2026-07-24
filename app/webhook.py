"""Receptor de webhooks de GitHub.

Sigue el patrón validado en el análisis de Dokploy, con dos mejoras deliberadas:

  1. El HMAC se verifica sobre el **raw body**, no sobre un JSON re-serializado
     (que puede no coincidir byte a byte y hacer fallar firmas válidas).
  2. Los archivos tocados salen de added + modified + removed. Dokploy solo mira
     `modified`, pero un archivo **nuevo o borrado** también cambia el grafo.

Convención de status (igual que Dokploy): **200** = procesado o nada que hacer,
**400** = payload inválido o evento no soportado, **401** = problema de firma.
Nunca 500 hacia GitHub; el detalle del error se queda del lado del servidor.

El endpoint responde de inmediato y **encola**: el reindexado es lento y GitHub
espera una respuesta rápida.
"""

import fnmatch
import hashlib
import hmac
import json

from . import cola, cripto
from . import db as repos_db

EVENTOS_ACEPTADOS = {"push", "pull_request", "ping"}
PALABRAS_SKIP = ("[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]")


def firma_valida(secreto: str, cuerpo: bytes, firma: str) -> bool:
    """HMAC-SHA256 del cuerpo crudo, comparado en tiempo constante."""
    esperado = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, firma or "")


def archivos_tocados(payload: dict) -> list[str]:
    """added + modified + removed de todos los commits del push."""
    archivos: list[str] = []
    for commit in payload.get("commits") or []:
        for clave in ("added", "modified", "removed"):
            archivos.extend(commit.get(clave) or [])
    return archivos


def debe_reindexar(watch_paths: list[str], archivos: list[str]) -> bool:
    """Sin watch_paths configurados, cualquier cambio reindexa."""
    if not watch_paths:
        return True
    return any(fnmatch.fnmatch(a, patron) for a in archivos for patron in watch_paths)


def _repo_autenticado(full_name: str, cuerpo: bytes, firma: str) -> dict | None:
    """Devuelve el repo cuyo secreto valida la firma (búsqueda + autenticación
    en un solo paso: solo el repo correcto tiene ese secreto)."""
    for repo in repos_db.buscar_por_github(full_name):
        datos = repos_db.datos_webhook(repo["name"]) or {}
        cifrado = datos.get("webhook_secret_cifrado")
        if not cifrado:
            continue
        try:
            secreto = cripto.descifrar(cifrado)
        except RuntimeError:
            continue
        if firma_valida(secreto, cuerpo, firma):
            return repo
    return None


def procesar(cuerpo: bytes, firma: str, evento: str) -> tuple[int, dict]:
    if evento not in EVENTOS_ACEPTADOS:
        return 400, {"message": "Solo se aceptan eventos push y pull_request"}

    try:
        payload = json.loads(cuerpo)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, {"message": "El payload no es JSON válido"}

    if evento == "ping":
        return 200, {"message": "Ping recibido, el webhook está activo"}

    full_name = (payload.get("repository") or {}).get("full_name")
    if not full_name:
        return 400, {"message": "Falta repository.full_name en el payload"}

    if not firma:
        return 401, {"message": "Falta la cabecera x-hub-signature-256"}

    repo = _repo_autenticado(full_name, cuerpo, firma)
    if repo is None:
        return 401, {"message": "Firma inválida o repositorio no registrado"}

    if evento == "pull_request":
        return _procesar_pr(repo, payload)
    return _procesar_push(repo, payload)


def _procesar_push(repo: dict, payload: dict) -> tuple[int, dict]:
    ref = payload.get("ref") or ""
    if not ref.startswith("refs/heads/"):
        return 200, {"message": "No es un push a una rama (tag u otro): ignorado"}

    rama = ref[len("refs/heads/") :]
    vigilada = repo.get("rama") or "main"
    if rama != vigilada:
        return 200, {"message": f"Rama '{rama}' no vigilada (se vigila '{vigilada}'): ignorado"}

    head = payload.get("head_commit") or {}
    mensaje = head.get("message") or ""
    if any(palabra in mensaje for palabra in PALABRAS_SKIP):
        return 200, {"message": "Salteado por palabra clave en el mensaje del commit"}

    archivos = archivos_tocados(payload)
    if not debe_reindexar(repo.get("watch_paths") or [], archivos):
        return 200, {"message": "Ningún archivo vigilado cambió: ignorado"}

    job_id = cola.encolar(repo["name"], commit_sha=head.get("id"), evento="push")
    if job_id is None:
        return 200, {"message": "Ese commit ya estaba encolado (reintento de GitHub)"}

    return 200, {"message": "Reindexado encolado", "repo": repo["name"], "job_id": job_id}


def _procesar_pr(repo: dict, payload: dict) -> tuple[int, dict]:
    """Verificación final de rama: se registra la revisión y se encola. El
    trabajo pesado (fetch + detect_changes + lineamientos + comentario) lo hace
    el worker; acá se responde de inmediato como espera GitHub."""
    from . import verificacion  # import diferido: evita ciclo al cargar

    accion = payload.get("action")
    if accion not in ("opened", "reopened", "synchronize"):
        return 200, {"message": f"Acción de PR '{accion}' ignorada"}

    pr = payload.get("pull_request") or {}
    numero = payload.get("number") or pr.get("number")
    if not numero:
        return 400, {"message": "Falta el número del PR"}

    rama_origen = (pr.get("head") or {}).get("ref") or ""
    rama_destino = (pr.get("base") or {}).get("ref") or ""
    vigilada = repo.get("rama") or "main"
    if rama_destino and rama_destino != vigilada:
        return 200, {"message": f"PR hacia '{rama_destino}', no hacia '{vigilada}': ignorado"}

    revision_id = verificacion.crear(
        repo_name=repo["name"],
        pr_numero=int(numero),
        rama=rama_origen or f"pr/{numero}",
        autor=(pr.get("user") or {}).get("login"),
        base_commit=(pr.get("base") or {}).get("sha"),
        head_commit=(pr.get("head") or {}).get("sha"),
    )
    cola.encolar_revision(revision_id)

    return 200, {
        "message": "Verificación de rama encolada",
        "repo": repo["name"],
        "pr": numero,
        "revision_id": revision_id,
    }
