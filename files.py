"""
Sharelock v2 — Storage backend abstraction.

Configurable per-agency via extension settings.
Nextcloud WebDAV is the default (and currently only active) backend.
S3 and Azure stubs ready for future implementation.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree

import httpx

from app import NC_URL, NC_USER, NC_PASS, NC_BASE_PATH

log = logging.getLogger("sharelock-v2.files")


@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    modified: datetime | None = None
    content_type: str = ""


@dataclass
class FileChange:
    path: str
    change_type: str  # "added" | "modified" | "deleted"
    modified: datetime | None = None


class StorageBackend:
    """Abstract storage backend. Subclass and implement all methods."""

    async def list_files(self, path: str) -> list[FileInfo]:
        raise NotImplementedError

    async def upload(self, path: str, data: bytes, filename: str) -> FileInfo:
        raise NotImplementedError

    async def download(self, path: str) -> bytes:
        raise NotImplementedError

    async def delete(self, path: str) -> bool:
        raise NotImplementedError

    async def mkdir(self, path: str) -> bool:
        raise NotImplementedError

    async def watch(self, path: str, since: datetime) -> list[FileChange]:
        raise NotImplementedError


class NextcloudWebDAV(StorageBackend):
    """Nextcloud storage via WebDAV (PROPFIND/GET/PUT/DELETE/MKCOL)."""

    def __init__(self, url: str = "", user: str = "", password: str = "",
                 base_path: str = ""):
        self.url = (url or NC_URL).rstrip("/")
        self.user = user or NC_USER
        self.password = password or NC_PASS
        self.base_path = base_path or NC_BASE_PATH
        self._dav = f"{self.url}/remote.php/dav/files/{self.user}"

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.user, self.password)

    def _full_path(self, path: str) -> str:
        return f"{self._dav}{self.base_path}{path}".rstrip("/")

    async def list_files(self, path: str) -> list[FileInfo]:
        """List files in a directory via PROPFIND."""
        url = self._full_path(path) + "/"
        body = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getlastmodified/><d:getcontentlength/><d:getcontenttype/><d:resourcetype/></d:prop></d:propfind>'
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request("PROPFIND", url, auth=self._auth(),
                                headers={"Depth": "1", "Content-Type": "application/xml"},
                                content=body)
            if r.status_code == 404:
                return []
            r.raise_for_status()

        ns = {"d": "DAV:"}
        root = ElementTree.fromstring(r.text)
        files = []
        for resp in root.findall("d:response", ns):
            href = resp.findtext("d:href", "", ns)
            # Skip the directory itself
            if href.rstrip("/") == url.split(self.url)[-1].rstrip("/"):
                continue
            props = resp.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            # Skip subdirectories
            rt = props.find("d:resourcetype/d:collection", ns)
            if rt is not None:
                continue
            name = href.rstrip("/").split("/")[-1]
            size = int(props.findtext("d:getcontentlength", "0", ns) or 0)
            ct = props.findtext("d:getcontenttype", "", ns) or ""
            mod_str = props.findtext("d:getlastmodified", "", ns)
            mod = None
            if mod_str:
                try:
                    from email.utils import parsedate_to_datetime
                    mod = parsedate_to_datetime(mod_str)
                except Exception:
                    pass
            files.append(FileInfo(name=name, path=f"{path}/{name}", size=size,
                                  modified=mod, content_type=ct))
        return files

    async def upload(self, path: str, data: bytes, filename: str) -> FileInfo:
        """Upload a file via PUT."""
        url = self._full_path(f"{path}/{filename}")
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.put(url, auth=self._auth(), content=data)
            r.raise_for_status()
        return FileInfo(name=filename, path=f"{path}/{filename}", size=len(data))

    async def download(self, path: str) -> bytes:
        """Download a file via GET."""
        url = self._full_path(path)
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.get(url, auth=self._auth())
            r.raise_for_status()
            return r.content

    async def delete(self, path: str) -> bool:
        """Delete a file via DELETE."""
        url = self._full_path(path)
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request("DELETE", url, auth=self._auth())
            return r.status_code in (200, 204, 404)

    async def mkdir(self, path: str) -> bool:
        """Create directory via MKCOL."""
        url = self._full_path(path) + "/"
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.request("MKCOL", url, auth=self._auth())
            return r.status_code in (201, 405)  # 405 = already exists

    async def watch(self, path: str, since: datetime) -> list[FileChange]:
        """Detect files added/modified since a given time."""
        files = await self.list_files(path)
        changes = []
        for f in files:
            if f.modified and f.modified > since:
                changes.append(FileChange(path=f.path, change_type="modified",
                                          modified=f.modified))
        return changes


class S3Backend(StorageBackend):
    """AWS S3 storage. Stub — implement when needed."""

    def __init__(self, **kwargs):
        raise NotImplementedError("S3 backend not yet implemented. Configure Nextcloud instead.")


class AzureBlobBackend(StorageBackend):
    """Azure Blob storage. Stub — implement when needed."""

    def __init__(self, **kwargs):
        raise NotImplementedError("Azure Blob backend not yet implemented. Configure Nextcloud instead.")


def create_backend(settings: dict | None = None) -> StorageBackend:
    """Factory: create storage backend from extension settings."""
    if not settings:
        return NextcloudWebDAV()

    storage = settings.get("storage", {})
    backend_type = storage.get("backend", "nextcloud")

    if backend_type == "nextcloud":
        nc = storage.get("nextcloud", {})
        return NextcloudWebDAV(
            url=nc.get("url", ""),
            user=nc.get("username", ""),
            password=nc.get("password", ""),
            base_path=nc.get("base_path", "") or NC_BASE_PATH,
        )
    elif backend_type == "s3":
        return S3Backend(**storage.get("s3", {}))
    elif backend_type == "azure_blob":
        return AzureBlobBackend(**storage.get("azure_blob", {}))
    else:
        raise ValueError(f"Unknown storage backend: {backend_type}")


_BACKEND_TTL = 300.0  # seconds; settings change rarely (admin operation)
_backends: dict[str, tuple[StorageBackend, float]] = {}


def reset_backend_cache() -> None:
    _backends.clear()


async def get_agency_backend(agency_id: str) -> StorageBackend:
    """Per-agency storage backend (in-process TTL cache; creds never leave
    this module — I-SECRETS-HANDLER-SCOPE-MEMORY discipline)."""
    import time

    import queries

    key = agency_id or "default"
    now = time.monotonic()
    cached = _backends.get(key)
    if cached is not None and now - cached[1] < _BACKEND_TTL:
        return cached[0]
    settings = None
    try:
        resp = await queries.get_agency_storage(key)
        if resp.get("configured"):
            settings = resp
    except Exception:
        settings = None  # Cases API degraded -> env fallback keeps working
    backend = create_backend(settings)
    _backends[key] = (backend, now)
    return backend
