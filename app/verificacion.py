"""Verificación final de rama en un PR (enfoque A).

Al abrir/actualizar un PR: se traen los cambios contra la rama canónica, se
calcula **su impacto** con el grafo real (`detect_changes`), se cruzan con los
**lineamientos oficiales** de las capas tocadas y se comenta el veredicto en el
PR. Es "revisión arquitectónica automática": grafo real + norma, no opiniones.

Enfoque A: NO se genera un grafo temporal de la rama. `detect_changes` es una
consulta de solo lectura sobre el grafo canónico (verificado: mismos nodos y
aristas antes y después), así que main queda intacto.

Cuidado con el working tree: para que `detect_changes` compare la rama contra
main, HEAD tiene que estar en la cabeza del PR. Se hace checkout y **se
restaura** siempre (try/finally). Como todo corre en el worker único, ningún
reindexado puede colarse en el medio y terminar indexando el código del PR
dentro del proyecto de main.

Degradación elegante: sin ANTHROPIC_API_KEY no hay veredicto automático, pero el
comentario igual lleva los cambios, el impacto y las reglas aplicables.
"""

import json
import re
from pathlib import Path

from psycopg.types.json import Json

from . import credenciales
from . import db as repos_db
from . import git_repo, github_api
from . import graph_engine as cbm
from . import pg
from . import reglas as wiki
from .config import settings


class VerificacionError(RuntimeError):
    pass


def crear(
    repo_name: str,
    pr_numero: int,
    rama: str,
    autor: str | None,
    base_commit: str | None,
    head_commit: str | None,
) -> str:
    """Registra la revisión en estado 'generando' y devuelve su id."""
    with pg.conn() as c:
        row = c.execute(
            """
            INSERT INTO revisiones (repo_name, pr_numero, rama, autor, base_commit, head_commit, estado)
            VALUES (%s, %s, %s, %s, %s, %s, 'generando')
            RETURNING id
            """,
            (repo_name, pr_numero, rama, autor, base_commit, head_commit),
        ).fetchone()
    return str(row["id"])


def listar(repo_name: str, limite: int = 20) -> list[dict]:
    with pg.conn() as c:
        rows = c.execute(
            """
            SELECT id, repo_name, pr_numero, rama, autor, base_commit, head_commit,
                   estado, diff_resumen, violaciones, comentario_url, creado_en
            FROM revisiones
            WHERE repo_name = %s
            ORDER BY creado_en DESC
            LIMIT %s
            """,
            (repo_name, limite),
        ).fetchall()
    salida = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        if d.get("creado_en") is not None:
            d["creado_en"] = d["creado_en"].isoformat()
        salida.append(d)
    return salida


def capas_tocadas(archivos: list[str], slugs: list[str]) -> list[str]:
    """Infiere qué capas del arquetipo toca un conjunto de archivos, mirando los
    segmentos de la ruta (ej. 'src/Handlers/CrearCita.cs' -> 'handlers')."""
    tocadas: set[str] = set()
    for archivo in archivos:
        segmentos = re.split(r"[/\\.]", archivo.lower())
        normalizados = {s.replace("-", "").replace("_", "") for s in segmentos if s}
        for slug in slugs:
            clave = slug.replace("-", "").replace("_", "")
            if clave in normalizados or any(clave in n for n in normalizados):
                tocadas.add(slug)
    return sorted(tocadas)


def _slugs_de_capas() -> list[str]:
    with pg.conn() as c:
        rows = c.execute("SELECT slug FROM capas ORDER BY orden").fetchall()
    return [r["slug"] for r in rows]


def _veredicto_con_llm(modulo: str, cambios: dict, reglas: dict[str, str]) -> list[dict]:
    """Pide el juicio a Claude con los HECHOS (cambios + impacto del grafo) y la
    NORMA (lineamientos verbatim). Devuelve la lista de incumplimientos."""
    import anthropic

    cambios_fmt = json.dumps(cambios, ensure_ascii=False, indent=2)[:6000]
    reglas_fmt = "\n\n".join(
        f"=== Lineamiento oficial: {capa} ===\n{contenido[:6000]}" for capa, contenido in reglas.items()
    )

    prompt = f"""Eres un revisor de arquitectura de Real Plaza. Revisa los cambios \
de esta rama contra los lineamientos oficiales adjuntos. Reporta SOLO \
incumplimientos reales de la norma (no inventes, no opines de estilo).

# Cambios detectados en el grafo real del código (archivos e impacto)
{cambios_fmt}

# Lineamientos oficiales (verbatim de la wiki de arquitectura)
{reglas_fmt}

Responde EXCLUSIVAMENTE con JSON: {{"violaciones": [{{"archivo": "...", \
"regla_violada": "...", "descripcion": "...", "fix_sugerido": "...", \
"severidad": "alta|media|baja"}}]}}. Si no hay incumplimientos, devuelve una lista vacía."""

    cliente = anthropic.Anthropic()
    respuesta = cliente.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = respuesta.content[0].text
    try:
        data = json.loads(texto)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto, re.DOTALL)
        data = json.loads(match.group(0)) if match else {"violaciones": []}
    return data.get("violaciones", [])


def _armar_comentario(cambios: dict, capas: list[str], reglas: dict, violaciones: list[dict], estado: str) -> str:
    icono = {"ok": "✅", "advertencias": "⚠️", "viola": "❌"}.get(estado, "ℹ️")
    archivos = cambios.get("changed_files") or []
    impactados = cambios.get("impacted_symbols") or []

    lineas = [
        f"## {icono} Verificación de arquitectura",
        "",
        f"**{len(archivos)}** archivo(s) cambiado(s) · **{len(impactados)}** símbolo(s) impactado(s) según el grafo real.",
        "",
    ]

    if archivos:
        lineas += ["<details><summary>Archivos cambiados</summary>", ""]
        lineas += [f"- `{a}`" for a in archivos[:40]]
        lineas += ["", "</details>", ""]

    if impactados:
        lineas += ["<details><summary>Impacto aguas abajo</summary>", ""]
        lineas += [f"- `{s if isinstance(s, str) else s.get('qualified_name', s)}`" for s in impactados[:40]]
        lineas += ["", "</details>", ""]

    if capas:
        lineas += [f"**Capas tocadas:** {', '.join(f'`{c}`' for c in capas)}", ""]

    if violaciones:
        lineas += ["### Incumplimientos detectados", ""]
        for v in violaciones:
            sev = (v.get("severidad") or "media").lower()
            marca = {"alta": "❌", "media": "⚠️", "baja": "ℹ️"}.get(sev, "⚠️")
            lineas.append(f"- {marca} **{v.get('regla_violada', 'Regla')}** — `{v.get('archivo', '?')}`")
            if v.get("descripcion"):
                lineas.append(f"  - {v['descripcion']}")
            if v.get("fix_sugerido"):
                lineas.append(f"  - _Sugerencia:_ {v['fix_sugerido']}")
        lineas.append("")
    elif reglas:
        lineas += [
            "No se detectaron incumplimientos automáticos. Lineamientos aplicables a las capas tocadas:",
            "",
        ]
        lineas += [f"- **{capa}**" for capa in reglas]
        lineas.append("")

    lineas.append("<sub>Generado por AI Context Hub · grafo real + lineamientos oficiales</sub>")
    return "\n".join(lineas)


def ejecutar(revision_id: str) -> None:
    """Corre la verificación completa. La llama el worker (nunca en el request)."""
    with pg.conn() as c:
        rev = c.execute(
            "SELECT repo_name, pr_numero, rama, head_commit FROM revisiones WHERE id = %s",
            (revision_id,),
        ).fetchone()
    if rev is None:
        return

    try:
        repo = repos_db.get(rev["repo_name"])
        if repo is None or not repo.get("cbm_project"):
            raise VerificacionError("el repo no está indexado todavía")

        ruta = Path(repo["local_path"])
        rama_base = repo.get("rama") or "main"
        token = (
            credenciales.token_para_clonar(repo["credential_id"])
            if repo.get("credential_id")
            else None
        )

        # Traer la cabeza del PR y ponerse ahí para que detect_changes compare
        # rama_base...PR. Se restaura SIEMPRE para no dejar el clon desalineado
        # con el grafo canónico.
        sha_pr = git_repo.fetch_pr(ruta, repo["source"], token, rev["pr_numero"])
        try:
            git_repo.checkout(ruta, sha_pr)
            cambios = cbm.detect_changes(
                repo["cbm_project"], since=f"origin/{rama_base}", base_branch=rama_base
            )
        finally:
            git_repo.checkout(ruta, rama_base)

        archivos = cambios.get("changed_files") or []
        capas = capas_tocadas(archivos, _slugs_de_capas())

        reglas: dict[str, str] = {}
        for capa in capas:
            regla = wiki.get_regla(capa)
            if regla:
                reglas[regla["capa"]] = regla["contenido"]

        violaciones: list[dict] = []
        if reglas and settings.anthropic_api_key:
            violaciones = _veredicto_con_llm(rev["repo_name"], cambios, reglas)

        if any((v.get("severidad") or "").lower() == "alta" for v in violaciones):
            estado = "viola"
        elif violaciones:
            estado = "advertencias"
        else:
            estado = "ok"

        cuerpo = _armar_comentario(cambios, capas, reglas, violaciones, estado)

        comentario_url = ""
        if token:
            try:
                owner, nombre = github_api.parse_owner_repo(repo["source"])
                comentario_url = github_api.comentar_pr(
                    owner, nombre, token, rev["pr_numero"], cuerpo
                )
            except github_api.GitHubError:
                pass  # el veredicto queda igual guardado en la base

        _guardar(revision_id, estado, cambios, violaciones, comentario_url, sha_pr)

    except Exception as e:
        _guardar(revision_id, "error", {"error": str(e)[:500]}, [], "", None)


def _guardar(
    revision_id: str,
    estado: str,
    cambios: dict,
    violaciones: list[dict],
    comentario_url: str,
    head_commit: str | None,
) -> None:
    with pg.conn() as c:
        c.execute(
            """
            UPDATE revisiones
               SET estado = %s,
                   diff_resumen = %s,
                   violaciones = %s,
                   comentario_url = %s,
                   head_commit = COALESCE(%s, head_commit)
             WHERE id = %s
            """,
            (estado, Json(cambios), Json(violaciones), comentario_url or None, head_commit, revision_id),
        )
