"""Tests del cliente de GitHub SIN tocar GitHub.

Se inyecta un httpx.MockTransport, así se verifica lo que de verdad importa (que
el payload del webhook sea el correcto y que los errores se traduzcan) sin
necesitar credenciales ni red.
"""

import json

import httpx
import pytest

from app import github_api


def _cliente(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_owner_repo_acepta_las_formas_usuales():
    assert github_api.parse_owner_repo("https://github.com/octocat/Hello-World.git") == (
        "octocat",
        "Hello-World",
    )
    assert github_api.parse_owner_repo("https://github.com/octocat/Hello-World") == (
        "octocat",
        "Hello-World",
    )
    assert github_api.parse_owner_repo("https://github.com/octocat/Hello-World/") == (
        "octocat",
        "Hello-World",
    )
    assert github_api.parse_owner_repo("git@github.com:octocat/Hello-World.git") == (
        "octocat",
        "Hello-World",
    )


def test_parse_owner_repo_rechaza_url_invalida():
    with pytest.raises(github_api.GitHubError):
        github_api.parse_owner_repo("https://gitlab.com/algo")


def test_crear_webhook_manda_el_payload_correcto():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["metodo"] = request.method
        capturado["url"] = str(request.url)
        capturado["auth"] = request.headers.get("authorization")
        capturado["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 12345})

    with _cliente(handler) as c:
        hook_id = github_api.crear_webhook(
            "realplaza", "ms-citas", "tok_secreto",
            url="https://hub.example/webhooks/github",
            secret="s3cr3t",
            cliente=c,
        )

    assert hook_id == 12345
    assert capturado["metodo"] == "POST"
    assert capturado["url"].endswith("/repos/realplaza/ms-citas/hooks")
    assert capturado["auth"] == "Bearer tok_secreto"
    # Lo que hace que el webhook sirva: los dos eventos, JSON y el secreto HMAC.
    assert capturado["body"]["events"] == ["push", "pull_request"]
    assert capturado["body"]["config"]["content_type"] == "json"
    assert capturado["body"]["config"]["secret"] == "s3cr3t"
    assert capturado["body"]["active"] is True


def test_borrar_webhook_usa_delete():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["metodo"] = request.method
        capturado["url"] = str(request.url)
        return httpx.Response(204)

    with _cliente(handler) as c:
        github_api.borrar_webhook("o", "r", "tok", 999, cliente=c)

    assert capturado["metodo"] == "DELETE"
    assert capturado["url"].endswith("/repos/o/r/hooks/999")


def test_comentar_pr_devuelve_la_url_del_comentario():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/o/r/issues/42/comments"
        assert json.loads(request.content)["body"] == "veredicto"
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/42#issuecomment-1"})

    with _cliente(handler) as c:
        url = github_api.comentar_pr("o", "r", "tok", 42, "veredicto", cliente=c)

    assert url.endswith("#issuecomment-1")


def test_error_de_github_se_traduce_con_el_motivo():
    """El caso real: un PAT sin permiso Webhooks:write."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "Resource not accessible by personal access token"}
        )

    with _cliente(handler) as c:
        with pytest.raises(github_api.GitHubError) as exc:
            github_api.crear_webhook("o", "r", "tok", "u", "s", cliente=c)

    assert "403" in str(exc.value)
    assert "not accessible" in str(exc.value)
