"""Cifrado simétrico de secretos (PAT, webhook secrets, private keys a futuro).

Usa Fernet (cryptography): cifrado autenticado con clave simétrica. La clave vive
en `CREDENTIALS_KEY` (entorno), **nunca en la base**: si alguien lee Postgres, no
obtiene los secretos. Es una mejora deliberada sobre Dokploy, que guarda la
private key y los webhook secrets en texto plano.

Generar una clave nueva (una sola vez, guardar en el entorno):
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Perezoso como pg.py: si falta la clave, el error salta al primer uso y no al
importar, para que la app arranque igual y falle con un mensaje claro.
"""

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_fernet: Fernet | None = None


def _get() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.credentials_key:
            raise RuntimeError(
                "Falta CREDENTIALS_KEY: no se pueden cifrar/descifrar secretos."
            )
        try:
            _fernet = Fernet(settings.credentials_key.encode())
        except (ValueError, TypeError) as e:
            raise RuntimeError(
                "CREDENTIALS_KEY inválida: debe ser una clave Fernet (urlsafe-base64 de 32 bytes)."
            ) from e
    return _fernet


def cifrar(texto: str) -> str:
    return _get().encrypt(texto.encode()).decode()


def descifrar(token: str) -> str:
    try:
        return _get().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError(
            "No se pudo descifrar el secreto (¿cambió CREDENTIALS_KEY?)."
        ) from e
