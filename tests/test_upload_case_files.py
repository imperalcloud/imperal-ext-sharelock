"""Track C.2 T3 — evidence upload handler (handlers_files.py).

Contract:
- limit violations (count / per-file / total / bad base64 / unusable name)
  return typed ERROR-FREE facts ({uploaded: 0, reason}) — SUCCESS results;
- filenames are basename-sanitised (no traversal out of the case folder);
- happy path uploads into the case-name folder of the agency backend and
  notes that analysis picks files up on the next census run.
"""
import asyncio
import base64

import auth_gate
import handlers_files as hf
from handlers_files import UploadCaseFilesParams


class _User:
    imperal_id = "imp_u_owner"
    agency_id = "acme"


class _Ctx:
    user = _User()
    cache = None


class _Backend:
    def __init__(self):
        self.uploads = []

    async def upload(self, path, data, filename):
        self.uploads.append((path, data, filename))
        return None


def _setup(monkeypatch, backend=None):
    async def fake(ctx, force_fresh=False):
        return auth_gate.UnlockState(unlocked=True, agency_id="acme", role="user")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)

    async def get_case(case_id, agency_id=None):
        assert agency_id == "acme"
        return {"id": case_id, "name": "CaseU"}
    monkeypatch.setattr(hf.queries, "get_case", get_case)

    backend = backend or _Backend()

    async def get_agency_backend(agency_id):
        assert agency_id == "acme"
        return backend
    monkeypatch.setattr(hf.files, "get_agency_backend", get_agency_backend)
    return backend


def _file(name: str, payload: bytes) -> dict:
    return {"name": name, "size": len(payload), "mime_type": "text/plain",
            "data_base64": base64.b64encode(payload).decode()}


def test_upload_happy_path(monkeypatch):
    backend = _setup(monkeypatch)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file("a.txt", b"alpha"), _file("b.txt", b"bravo")])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert res.data["uploaded"] == 2
    assert res.data["case_name"] == "CaseU"
    assert res.data["files"] == ["a.txt", "b.txt"]
    assert "census" in res.data["note"]
    assert backend.uploads == [("CaseU", b"alpha", "a.txt"),
                               ("CaseU", b"bravo", "b.txt")]


def test_upload_traversal_name_sanitised(monkeypatch):
    backend = _setup(monkeypatch)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file("../../etc/evil.txt", b"x")])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert backend.uploads == [("CaseU", b"x", "evil.txt")]


def test_upload_too_many_files_is_limit_fact(monkeypatch):
    _setup(monkeypatch)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file(f"f{i}.txt", b"x") for i in range(9)])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success", "limit violations are facts, not errors"
    assert res.data["uploaded"] == 0
    assert "limit 8 files" in res.data["reason"]


def test_upload_per_file_limit_fact(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(hf, "_MAX_FILE_BYTES", 4)
    monkeypatch.setattr(hf, "_B64_FILE_CAP", 4 * 4 // 3 + 4)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file("big.bin", b"123456789")])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert res.data["uploaded"] == 0
    assert "per file" in res.data["reason"]


def test_upload_total_limit_fact(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(hf, "_MAX_TOTAL_BYTES", 8)
    monkeypatch.setattr(hf, "_B64_TOTAL_CAP", 8 * 4 // 3 + 4 * 8)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file("a.bin", b"12345"), _file("b.bin", b"123456")])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert res.data["uploaded"] == 0
    assert "total" in res.data["reason"]


def test_upload_invalid_base64_fact(monkeypatch):
    _setup(monkeypatch)
    params = UploadCaseFilesParams(case_id=7, files=[
        {"name": "x.txt", "size": 3, "mime_type": "", "data_base64": "@@not-b64@@"}])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert res.data["uploaded"] == 0
    assert "not valid base64" in res.data["reason"]


def test_upload_empty_payload_fact(monkeypatch):
    _setup(monkeypatch)
    res = asyncio.run(hf.fn_upload_case_files(
        _Ctx(), UploadCaseFilesParams(case_id=7, files=[])))
    assert res.status == "success"
    assert res.data["uploaded"] == 0


def test_upload_unknown_case_is_error(monkeypatch):
    _setup(monkeypatch)

    async def get_case(case_id, agency_id=None):
        return {}
    monkeypatch.setattr(hf.queries, "get_case", get_case)
    res = asyncio.run(hf.fn_upload_case_files(
        _Ctx(), UploadCaseFilesParams(case_id=404, files=[_file("a.txt", b"x")])))
    assert res.status == "error"


def test_upload_partial_backend_failure_reports_both(monkeypatch):
    class _FlakyBackend(_Backend):
        async def upload(self, path, data, filename):
            if filename == "bad.txt":
                raise RuntimeError("dav 507")
            return await super().upload(path, data, filename)

    backend = _FlakyBackend()
    _setup(monkeypatch, backend=backend)
    params = UploadCaseFilesParams(case_id=7, files=[
        _file("ok.txt", b"x"), _file("bad.txt", b"y")])
    res = asyncio.run(hf.fn_upload_case_files(_Ctx(), params))
    assert res.status == "success"
    assert res.data["uploaded"] == 1
    assert res.data["files"] == ["ok.txt"]
    assert res.data["failed"] == ["bad.txt"]
