"""Credenciales git: PAT hoy, GitHub App en v3.

`token_para_clonar()` es la ÚNICA puerta por la que el resto del sistema obtiene
un token para operar con git/GitHub. Hoy, para tipo='pat', descifra y devuelve el
PAT. Cuando exista la GitHub App se agrega la rama tipo='github_app' que firma un
JWT con la private key y pide un installation token efímero — y **nada más del
sistema cambia**: registro de repos, webhooks y verificación siguen pidiendo el
token por acá.

Los secretos se guardan cifrados (ver cripto.py). `listar()` nunca los expone.
"""

from datetime import datetime

from . import cripto, pg

# Hasta que exista RBAC, todo lo registra el admin del piloto.
OWNER_POR_DEFECTO = "admin"


class CredencialError(RuntimeError):
    pass


def crear_pat(
    alias: str,
    token: str,
    github_login: str | None = None,
    owner: str = OWNER_POR_DEFECTO,
) -> str:
    """Guarda un Personal Access Token cifrado. Devuelve el id de la credencial."""
    if not token.strip():
        raise CredencialError("El token no puede estar vacío.")
    with pg.conn() as c:
        row = c.execute(
            """
            INSERT INTO git_credentials (tipo, alias, owner, github_login, token_cifrado)
            VALUES ('pat', %s, %s, %s, %s)
            RETURNING id
            """,
            (alias, owner, github_login, cripto.cifrar(token)),
        ).fetchone()
    return str(row["id"])


def listar() -> list[dict]:
    """Metadata de las credenciales — nunca devuelve el token."""
    with pg.conn() as c:
        rows = c.execute(
            """
            SELECT id, tipo, alias, owner, github_login, creado_en
            FROM git_credentials
            ORDER BY creado_en DESC
            """
        ).fetchall()
    return [_norm(r) for r in rows]


def eliminar(credential_id: str) -> bool:
    with pg.conn() as c:
        cur = c.execute("DELETE FROM git_credentials WHERE id = %s", (credential_id,))
        return cur.rowcount > 0


def token_para_clonar(credential_id: str) -> str:
    """Punto de extensión PAT → GitHub App. Todo el sistema pide el token acá."""
    with pg.conn() as c:
        row = c.execute(
            "SELECT tipo, token_cifrado FROM git_credentials WHERE id = %s",
            (credential_id,),
        ).fetchone()

    if row is None:
        raise CredencialError(f"La credencial {credential_id} no existe.")

    if row["tipo"] == "pat":
        return cripto.descifrar(row["token_cifrado"])

    # v3 — github_app: firmar un JWT con la private key y pedir a GitHub un
    # installation access token (efímero, ~1h). El resto del sistema no se entera.
    raise CredencialError(
        f"Tipo de credencial todavía no soportado: {row['tipo']!r}."
    )


def _norm(row: dict) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    if isinstance(d.get("creado_en"), datetime):
        d["creado_en"] = d["creado_en"].isoformat()
    return d
