"""Track C.2 T3 — admin settings handler (handlers_admin.py).

Contract:
- non-admin gets a typed denial FACT (success, reason=admin_role_required);
- PUT replaces the whole blob, so the handler MERGES: empty submitted
  fields (passwords especially) keep the existing stored values, and an
  untouched database section survives a storage-only save;
- secrets NEVER appear in the result data or summary (masked to set/not-set);
- a successful save drops the in-process backend cache so new creds apply.
"""
import asyncio
import json

import auth_gate
import handlers_admin as ha
from handlers_admin import (
    SaveAgencySettingsParams, _merge_database, _merge_settings,
)

_SECRET = "sup3r-sekret"
_CURRENT = {
    "configured": True,
    "storage": {"backend": "nextcloud", "nextcloud": {
        "url": "https://old.example.org", "username": "olduser",
        "password": _SECRET, "base_path": "/Sharelock/",
    }},
    "database": {"host": "db1", "port": "3306", "name": "users",
                 "username": "dbu", "password": "db-sekret"},
}


class _User:
    imperal_id = "imp_u_admin"
    agency_id = "acme"


class _Ctx:
    user = _User()
    cache = None


def _role(monkeypatch, role):
    async def fake(ctx, force_fresh=False):
        return auth_gate.UnlockState(unlocked=True, agency_id="acme", role=role)
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def _wire(monkeypatch, current=None):
    puts = {}

    async def get_agency_storage(agency_id):
        return json.loads(json.dumps(current if current is not None else _CURRENT))
    monkeypatch.setattr(ha.queries, "get_agency_storage", get_agency_storage)

    async def put_agency_storage(agency_id, body):
        puts["agency_id"] = agency_id
        puts["body"] = body
        return {"ok": True, "agency_id": agency_id}
    monkeypatch.setattr(ha.queries, "put_agency_storage", put_agency_storage)

    resets = []
    monkeypatch.setattr(ha.files, "reset_backend_cache",
                        lambda: resets.append(True))
    return puts, resets


def test_non_admin_gets_typed_denial_fact(monkeypatch):
    _role(monkeypatch, "user")
    puts, _ = _wire(monkeypatch)
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(storage_url="https://new")))
    assert res.status == "success", "denial is a typed FACT, not an error"
    assert res.data["saved"] is False
    assert res.data["reason"] == "admin_role_required"
    assert puts == {}, "non-admin must never reach the PUT"


def test_empty_password_keeps_existing_secret(monkeypatch):
    _role(monkeypatch, "admin")
    puts, resets = _wire(monkeypatch)
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(storage_url="https://new.example.org")))
    assert res.status == "success" and res.data["saved"] is True
    nc = puts["body"]["storage"]["nextcloud"]
    assert nc["url"] == "https://new.example.org"
    assert nc["password"] == _SECRET, "empty password field must keep existing"
    assert nc["username"] == "olduser" and nc["base_path"] == "/Sharelock/"
    assert puts["body"]["database"] == _CURRENT["database"], (
        "untouched database section must survive a storage-only save "
        "(PUT replaces the whole blob)")
    assert puts["body"]["updated_by"] == "imp_u_admin"
    assert resets, "backend TTL cache must be dropped after a save"


def test_secrets_never_echoed_in_fact_or_summary(monkeypatch):
    _role(monkeypatch, "admin")
    _wire(monkeypatch)
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(storage_password="new-sekret")))
    blob = json.dumps(res.data) + (res.summary or "")
    assert "new-sekret" not in blob and _SECRET not in blob
    assert "db-sekret" not in blob
    assert res.data["storage_password_set"] is True


def test_dsn_mode_replaces_discrete_and_is_masked(monkeypatch):
    _role(monkeypatch, "admin")
    puts, _ = _wire(monkeypatch)
    dsn = "mysql://u:dsn-sekret@h/users"
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(db_dsn=dsn)))
    assert res.status == "success"
    assert puts["body"]["database"] == {"dsn": dsn}
    blob = json.dumps(res.data) + (res.summary or "")
    assert "dsn-sekret" not in blob, "DSN may embed a password — never echo it"
    assert res.data["database_configured"] is True


def test_discrete_db_overlay_keeps_unsubmitted_fields(monkeypatch):
    _role(monkeypatch, "admin")
    puts, _ = _wire(monkeypatch)
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(db_host="db2")))
    assert res.status == "success"
    db = puts["body"]["database"]
    assert db["host"] == "db2"
    assert db["password"] == "db-sekret", "unsubmitted db password kept"
    assert db["name"] == "users"


def test_unconfigured_agency_requires_url_and_base_path(monkeypatch):
    _role(monkeypatch, "admin")
    puts, _ = _wire(monkeypatch, current={"configured": False})
    res = asyncio.run(ha.fn_save_agency_settings(
        _Ctx(), SaveAgencySettingsParams(storage_username="u-only")))
    assert res.status == "error"
    assert puts == {}, "invalid merge must not PUT"


# ── Pure merge helpers ────────────────────────────────────────────────────────


def test_merge_database_nothing_submitted_keeps_existing():
    p = SaveAgencySettingsParams()
    assert _merge_database({"dsn": "x"}, p) == {"dsn": "x"}
    assert _merge_database(None, p) is None


def test_merge_database_discrete_submission_leaves_dsn_mode():
    p = SaveAgencySettingsParams(db_host="h", db_name="n")
    merged = _merge_database({"dsn": "mysql://old"}, p)
    assert merged == {"host": "h", "port": "", "name": "n",
                      "username": "", "password": ""}


def test_merge_settings_unconfigured_uses_submitted_only():
    p = SaveAgencySettingsParams(storage_url="https://nc", storage_base_path="/S/")
    body = _merge_settings({"configured": False}, p)
    assert body["storage"]["nextcloud"]["url"] == "https://nc"
    assert body["storage"]["nextcloud"]["password"] == ""
    assert "database" not in body
