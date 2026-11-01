"""An endpoint that refuses with its own headers must have them reach the client. A locked account's Retry-After is how
the sign-in form knows how long to wait."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.main import install_problem_handlers


def _app() -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/locked")
    async def locked():
        raise HTTPException(429, "too many wrong passwords", headers={"Retry-After": "900"})

    @app.get("/plain")
    async def plain():
        raise HTTPException(404, "not here")

    return TestClient(app)


def test_retry_after_survives_the_problem_handler():
    response = _app().get("/locked")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "too many wrong passwords"


def test_a_refusal_without_headers_is_unchanged():
    response = _app().get("/plain")
    assert response.status_code == 404 and "retry-after" not in response.headers
