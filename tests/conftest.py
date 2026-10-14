import os

import pytest

# TestClient runs the app's lifespan; the retention loop must never touch the real stores from a test run.
os.environ["DISABLE_RETENTION_LOOP"] = "1"


@pytest.fixture(autouse=True)
def _isolated_previews(tmp_path, monkeypatch):
    """Link-preview images and the retention marker go to a temp dir, never the real workdir, and the
    background prewarm (which would outlive a test's event loop) is off unless a test asks for it."""
    import src.service.app as app_module
    monkeypatch.setattr(app_module, "_PREVIEW_DIR", tmp_path / "previews")
    monkeypatch.setattr(app_module, "_retention_marker", lambda: tmp_path / ".retention_started")
    monkeypatch.setattr(app_module, "_prewarm_preview", lambda cv_id: None)
