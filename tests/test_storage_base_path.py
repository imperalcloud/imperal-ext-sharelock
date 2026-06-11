import importlib


def _reload(monkeypatch):
    monkeypatch.setenv("NC_BASE_PATH", "/Private Share/")
    monkeypatch.setenv("NC_URL", "https://nc.example")
    monkeypatch.setenv("NC_USER", "svc")
    monkeypatch.setenv("NC_PASSWORD", "x")
    import app
    importlib.reload(app)
    import files
    importlib.reload(files)
    return files


def test_nextcloud_backend_defaults_to_env_base_path(monkeypatch):
    files = _reload(monkeypatch)
    backend = files.NextcloudWebDAV()
    assert backend.base_path == "/Private Share/"


def test_create_backend_factory_defaults_to_env_base_path(monkeypatch):
    files = _reload(monkeypatch)
    backend = files.create_backend({"storage": {"backend": "nextcloud", "nextcloud": {}}})
    assert backend.base_path == "/Private Share/"


def test_explicit_base_path_still_overrides(monkeypatch):
    files = _reload(monkeypatch)
    backend = files.NextcloudWebDAV(base_path="/Custom/")
    assert backend.base_path == "/Custom/"
