"""Router auto-registration and the brain side of the REST contract."""

import sys
import types

import pytest
import yaml
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import create_app, load_router

from .conftest import REPO_ROOT


def test_absent_router_package_is_skipped():
    assert load_router("app.api_nonexistent.router") is None


def test_broken_router_import_is_raised(monkeypatch):
    # A router module that exists but imports a missing dependency must not be swallowed.
    def broken():
        import definitely_not_installed_pkg  # noqa: F401

    module = types.ModuleType("app.api_broken")
    module.__path__ = []  # mark as package
    monkeypatch.setitem(sys.modules, "app.api_broken", module)

    class Finder:
        def find_spec(self, name, path=None, target=None):
            if name == "app.api_broken.router":
                broken()
            return None

    monkeypatch.setattr(sys, "meta_path", [Finder(), *sys.meta_path])
    with pytest.raises(ModuleNotFoundError) as exc:
        load_router("app.api_broken.router")
    assert exc.value.name == "definitely_not_installed_pkg"


def test_non_router_attribute_is_rejected(monkeypatch):
    module = types.ModuleType("app.api_weird.router")
    module.router = object()
    monkeypatch.setitem(sys.modules, "app.api_weird.router", module)
    with pytest.raises(TypeError):
        load_router("app.api_weird.router")


def test_registered_router_is_mounted(monkeypatch):
    module = types.ModuleType("app.api_probe.router")
    module.router = APIRouter()

    @module.router.get("/api/probe")
    async def probe():
        return {"ok": True}

    monkeypatch.setitem(sys.modules, "app.api_probe.router", module)
    client = TestClient(create_app(("app.api_probe.router",)))
    assert client.get("/api/probe").json() == {"ok": True}


def _contract_paths(tag: str) -> set[tuple[str, str]]:
    spec = yaml.safe_load((REPO_ROOT / "contracts" / "openapi.yaml").read_text())
    return {
        (method.upper(), path)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if tag in op.get("tags", [])
    }


def test_brain_router_covers_contract():
    app = create_app(("app.api_brain.router",))
    served = {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}
    missing = _contract_paths("brain") - served
    assert not missing, f"brain paths in openapi.yaml not served: {missing}"


def test_healthz_reports_degraded_when_dependencies_are_down():
    # conftest points DATABASE_URL/REDIS_URL at closed ports.
    client = TestClient(create_app(()))
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"postgres": "error", "redis": "error"}}
