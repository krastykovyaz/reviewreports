import os

import pytest

# TestClient runs the app's lifespan; the retention loop must never touch the real stores from a test run.
os.environ["DISABLE_RETENTION_LOOP"] = "1"
# The service loads the host's real .env at import (load_dotenv, which never overrides a variable that is already set).
# That file restricts PUBLIC_KINDS to website_audit in production, which silently changed what the tests exercised:
# they passed on a clean checkout and failed here. Pin the settings the tests depend on so the result is the same everywhere.
os.environ["PUBLIC_KINDS"] = ""
os.environ.pop("REVIEW_ALLOWED_ROOTS", None)
os.environ.pop("PUBLIC_MODELS", None)


@pytest.fixture(autouse=True)
def _isolated_previews(tmp_path, monkeypatch):
    """Link-preview images and the retention marker go to a temp dir, never the real workdir, and the
    background prewarm (which would outlive a test's event loop) is off unless a test asks for it."""
    import src.service.app as app_module
    monkeypatch.setattr(app_module, "_PREVIEW_DIR", tmp_path / "previews")
    monkeypatch.setattr(app_module, "_retention_marker", lambda: tmp_path / ".retention_started")
    monkeypatch.setattr(app_module, "_prewarm_preview", lambda cv_id: None)


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    """The app's stores, upload directory and model registry point at the host's REAL files by default (workdir/service/*.db,
    workdir/uploads, and the model keys loaded from the host's .env). `with TestClient(app)` runs the lifespan, which
    initialises them - so a test that patched only one store still opened the other real database, and loaded real model
    credentials. Every test now starts on throwaway copies; a test that wants its own still monkeypatches over these."""
    import src.service.app as app_module
    from src.service.cv_store import CVStore
    from src.service.db import JobStore

    monkeypatch.setattr(app_module, "_store", JobStore(db_path=str(tmp_path / "default-jobs.db")))
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "default-cv.db")))
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "default-uploads"))

    async def _no_model_init():
        return None

    monkeypatch.setattr(app_module.model_manager, "initialize", _no_model_init)


@pytest.fixture(autouse=True)
def _no_browser_temp_dirs_left_behind():
    """BrowserProfile() makes /tmp/browser-use-downloads-* the moment it is built (and a profile dir when one is launched), and
    nothing deletes them: tests that build profiles must not fill the disk of the machine that runs them."""
    import glob
    import shutil
    import tempfile

    patterns = [os.path.join(tempfile.gettempdir(), p) for p in ("browser-use-downloads-*", "browser-use-user-data-dir-*")]
    before = {path for pattern in patterns for path in glob.glob(pattern)}
    yield
    for pattern in patterns:
        for path in set(glob.glob(pattern)) - before:
            shutil.rmtree(path, ignore_errors=True)
