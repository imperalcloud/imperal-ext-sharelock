import asyncio

import files
import queries


def test_get_agency_backend_env_fallback(monkeypatch):
    async def not_configured(agency_id):
        return {"configured": False}
    monkeypatch.setattr(queries, "get_agency_storage", not_configured)
    files.reset_backend_cache()
    b = asyncio.run(files.get_agency_backend("default"))
    assert type(b).__name__ == "NextcloudWebDAV"


def test_get_agency_backend_uses_settings_and_caches(monkeypatch):
    calls = []

    async def configured(agency_id):
        calls.append(agency_id)
        return {"configured": True, "storage": {"backend": "nextcloud", "nextcloud": {
            "url": "https://acme.nc", "username": "svc", "password": "p",
            "base_path": "/Sharelock-acme/"}}}
    monkeypatch.setattr(queries, "get_agency_storage", configured)
    files.reset_backend_cache()
    b1 = asyncio.run(files.get_agency_backend("acme"))
    b2 = asyncio.run(files.get_agency_backend("acme"))
    assert b1 is b2 and calls == ["acme"]
    assert b1.url == "https://acme.nc" and b1.base_path == "/Sharelock-acme/"


def test_backend_cache_failure_degrades_to_env(monkeypatch):
    async def boom(agency_id):
        raise RuntimeError("cases api down")
    monkeypatch.setattr(queries, "get_agency_storage", boom)
    files.reset_backend_cache()
    b = asyncio.run(files.get_agency_backend("acme"))
    assert type(b).__name__ == "NextcloudWebDAV"  # env fallback, never raises
