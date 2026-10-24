"""What an audit's browser may cost the host: the directories it writes are removed (also after failures, timeouts and
cancellation), downloads are refused, Chromium and the PDF rasteriser are capped in memory, a crashed browser fails its job at once,
and nothing outside the system temp dir is ever deleted.

audit_page runs here against a stand-in browser class; the keyword arguments it hands over are checked against the real
Browser/BrowserProfile (which reject fields they do not have), and the live-Chromium behaviour is in test_browser_guard_live.py."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosedError

import src.service.app as app_module
from src.audit import browser_session
from src.audit.browser_session import AUDIT_CHROMIUM_ARGS, BrowserCrashed, _remove_own_dirs, audit_page
from src.audit.http_client import SSRFBlocked
from src.environment.browser import Browser
from src.service.db import JobStatus, JobStore


@pytest.fixture()
def tmp_root(tmp_path, monkeypatch):
    """The system temp dir, for this test: audit_page's mkdtemp lands here and nowhere else."""
    root = tmp_path / "systmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _fake(events=None, *, start_error=None, stop_error=None, crashed=False, body_files=True):
    """Stand-in for Browser: writes into the profile and downloads directories it was given, as Chromium does."""
    events = events if events is not None else []

    class FakeBrowser:
        process = None

        def __init__(self, **kw):
            events.append("init")
            self.kw = kw
            self.crashed = crashed
            FakeBrowser.last = self

        def browser_process(self):
            return FakeBrowser.process

        async def start(self):
            events.append("start")
            if body_files:
                for key in ("user_data_dir", "downloads_path"):
                    d = Path(self.kw[key])
                    (d / "sub").mkdir()
                    (d / "sub" / "blob.bin").write_bytes(b"x" * 1000)
            if start_error:
                raise start_error

        async def stop(self):
            events.append("stop")
            if stop_error:
                raise stop_error

        async def get_current_page(self):
            class P:
                async def goto(self, url): events.append(("goto", url))
            return P()

    return FakeBrowser


@pytest.fixture(autouse=True)
def _public_start_url(monkeypatch):
    monkeypatch.setattr(browser_session, "assert_public_url", lambda url: asyncio.sleep(0))


def _left(root):
    return sorted(p.name for p in root.iterdir())


# ── the directories audit_page makes are removed ──

@pytest.mark.asyncio
async def test_the_profile_and_downloads_directories_are_made_in_tmp_and_removed_after_a_normal_audit(tmp_root, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(browser_session, "Browser", fake)
    async with audit_page("https://example.com/"):
        names = _left(tmp_root)
        assert len(names) == 2 and {n.rsplit("-", 1)[0] for n in names} == {"reviewreports-audit-profile", "reviewreports-audit-downloads"}
        assert Path(fake.last.kw["user_data_dir"]).parent == tmp_root and Path(fake.last.kw["downloads_path"]).parent == tmp_root
        assert (Path(fake.last.kw["user_data_dir"]) / "sub" / "blob.bin").exists()      # the browser wrote there
    assert _left(tmp_root) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [SSRFBlocked("internal"), RuntimeError("collector failed"), asyncio.TimeoutError()])
async def test_they_are_removed_when_the_audit_body_fails(tmp_root, monkeypatch, failure):
    events = []
    monkeypatch.setattr(browser_session, "Browser", _fake(events))
    with pytest.raises(type(failure)):
        async with audit_page("https://example.com/"):
            raise failure
    assert _left(tmp_root) == [] and "stop" in events


@pytest.mark.asyncio
async def test_they_are_removed_when_the_browser_fails_to_start(tmp_root, monkeypatch):
    events = []
    monkeypatch.setattr(browser_session, "Browser", _fake(events, start_error=RuntimeError("Chrome would not start")))
    with pytest.raises(RuntimeError, match="would not start"):
        async with audit_page("https://example.com/"):
            pass
    assert _left(tmp_root) == [] and "stop" in events      # a browser that half started is stopped too, not left running


@pytest.mark.asyncio
async def test_they_are_removed_when_the_browser_cannot_even_be_built(tmp_root, monkeypatch):
    class Refuses:
        def __init__(self, **kw): raise ValueError("bad option")
    monkeypatch.setattr(browser_session, "Browser", Refuses)
    with pytest.raises(ValueError):
        async with audit_page("https://example.com/"):
            pass
    assert _left(tmp_root) == []


@pytest.mark.asyncio
async def test_they_are_removed_even_when_stopping_the_browser_fails(tmp_root, monkeypatch):
    monkeypatch.setattr(browser_session, "Browser", _fake(stop_error=RuntimeError("stop failed")))
    with pytest.raises(RuntimeError, match="stop failed"):
        async with audit_page("https://example.com/"):
            pass
    assert _left(tmp_root) == []


@pytest.mark.asyncio
async def test_they_are_removed_when_the_audit_is_cancelled_by_the_job_timeout(tmp_root, monkeypatch):
    events = []
    monkeypatch.setattr(browser_session, "Browser", _fake(events))

    async def audit():
        async with audit_page("https://example.com/"):
            await asyncio.sleep(30)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(audit(), timeout=0.3)
    assert _left(tmp_root) == [] and "stop" in events


@pytest.mark.asyncio
async def test_they_are_removed_when_cancelled_twice_while_cleaning_up(tmp_root, monkeypatch):
    slow_stop = asyncio.Event()

    class SlowStop(_fake()):
        async def stop(self):
            slow_stop.set()
            await asyncio.sleep(0.2)

    monkeypatch.setattr(browser_session, "Browser", SlowStop)

    async def audit():
        async with audit_page("https://example.com/"):
            await asyncio.sleep(30)

    task = asyncio.ensure_future(audit())
    await asyncio.sleep(0.1)
    task.cancel()
    await slow_stop.wait()
    task.cancel()                  # a second cancel lands while the browser is being stopped
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if not _left(tmp_root): break
        await asyncio.sleep(0.02)
    assert _left(tmp_root) == []


# ── the directories go only after Chromium has really exited ──

class _FakeProcess:
    """The part of psutil.Process that _reap_and_remove uses: children(recursive=True), wait(timeout) (raising
    psutil.TimeoutExpired while it runs) and kill()."""
    def __init__(self, exits_after_kill_only=False, children=(), on_wait=None):
        self.alive, self.kills, self._children, self.only_kill, self.on_wait = True, 0, list(children), exits_after_kill_only, on_wait

    def children(self, recursive=False): return list(self._children)

    def wait(self, timeout=None):
        import psutil
        if self.on_wait: self.on_wait()
        if self.alive and self.only_kill:
            raise psutil.TimeoutExpired(timeout)
        self.alive = False

    def kill(self):
        self.kills += 1; self.alive = False


def test_the_directories_are_removed_only_after_the_browser_process_has_exited(tmp_root, monkeypatch):
    profile = tmp_root / "reviewreports-audit-profile-x"; profile.mkdir()
    seen = {}
    proc = _FakeProcess(on_wait=lambda: seen.update(profile_still_there_while_waiting=profile.exists()))
    browser_session._reap_and_remove(proc, [str(profile)])
    assert seen["profile_still_there_while_waiting"] is True and not profile.exists() and proc.kills == 0


def test_a_browser_that_will_not_exit_is_killed_with_its_children_before_the_directories_go(tmp_root, monkeypatch):
    monkeypatch.setattr(browser_session, "_BROWSER_EXIT_GRACE_SEC", 0.01)
    profile = tmp_root / "reviewreports-audit-profile-x"; profile.mkdir()
    renderer = _FakeProcess()
    proc = _FakeProcess(exits_after_kill_only=True, children=[renderer])
    browser_session._reap_and_remove(proc, [str(profile)])
    assert proc.kills == 1 and renderer.kills == 1 and not profile.exists()


@pytest.mark.asyncio
async def test_audit_page_waits_for_the_process_stop_leaves_running(tmp_root, monkeypatch):
    proc = _FakeProcess()
    fake = _fake()
    fake.process = proc
    monkeypatch.setattr(browser_session, "Browser", fake)
    async with audit_page("https://example.com/"):
        pass
    assert proc.alive is False and proc.kills == 0 and _left(tmp_root) == []      # it was waited for, not just abandoned


# ── and only what audit_page created ──

def test_nothing_outside_the_temp_dir_or_with_another_name_is_ever_removed(tmp_root, tmp_path):
    outside = tmp_path / "reviewreports-audit-profile-elsewhere"            # right name, wrong place
    outside.mkdir(); (outside / "keep").write_text("1")
    nested = tmp_root / "sub"; nested.mkdir()
    in_sub = nested / "reviewreports-audit-profile-nested"                  # right name, not directly in the temp dir
    in_sub.mkdir(); (in_sub / "keep").write_text("1")
    other = tmp_root / "somebody-elses-dir"                                 # in the temp dir, not ours
    other.mkdir(); (other / "keep").write_text("1")
    precious = tmp_path / "precious"; precious.mkdir(); (precious / "keep").write_text("1")
    link = tmp_root / "reviewreports-audit-downloads-link"                  # ours by name, but a link to elsewhere
    link.symlink_to(precious, target_is_directory=True)
    file_ = tmp_root / "reviewreports-audit-profile-file"; file_.write_text("1")
    ours = tmp_root / "reviewreports-audit-profile-real"; ours.mkdir(); (ours / "x").write_text("1")
    (ours / "inner-link").symlink_to(precious, target_is_directory=True)    # a link INSIDE a profile is not followed

    _remove_own_dirs([str(outside), str(in_sub), str(other), str(link), str(file_), str(tmp_root / "missing"), str(ours),
                      str(tmp_root), "/", "", str(Path(tmp_root) / ".." / "systmp" / "somebody-elses-dir")])

    assert (outside / "keep").exists() and (in_sub / "keep").exists() and (other / "keep").exists()
    assert (precious / "keep").exists() and link.is_symlink() and file_.exists()
    assert not ours.exists() and tmp_root.exists()


# ── the browser's memory, downloads and extensions ──

@pytest.mark.asyncio
async def test_the_browser_is_started_with_memory_caps_and_the_audit_settings(tmp_root, monkeypatch):
    fake = _fake()
    monkeypatch.setattr(browser_session, "Browser", fake)
    async with audit_page("https://example.com/"):
        kw = fake.last.kw
    assert kw["accept_downloads"] is False and kw["auto_download_pdfs"] is False
    assert kw["enable_default_extensions"] is False and kw["abort_on_crash"] is True
    assert kw["args"] == AUDIT_CHROMIUM_ARGS


@pytest.mark.asyncio
async def test_the_real_browser_gets_those_flags_denies_downloads_and_never_fetches_extensions(tmp_root, monkeypatch):
    from src.environment.browser.browser.profile import BrowserProfile

    seen = {}

    def no_fetching(self):
        raise AssertionError("the default extensions must not be downloaded for an audit")

    monkeypatch.setattr(BrowserProfile, "_ensure_default_extensions_downloaded", no_fetching)
    real_browser = browser_session.Browser

    class Capturing:
        def __init__(self, **kw):
            seen["browser"] = real_browser(**kw)         # the REAL class: it rejects options it does not know
        crashed = False
        def browser_process(self): return None
        async def start(self): pass
        async def stop(self): pass
        async def get_current_page(self):
            class P:
                async def goto(self, url): pass
            return P()

    monkeypatch.setattr(browser_session, "Browser", Capturing)
    async with audit_page("https://example.com/"):
        pass
    profile = seen["browser"].browser_profile
    args = profile.get_args()
    assert "--js-flags=--max-old-space-size=256" in args
    assert any(a.startswith("--renderer-process-limit=") for a in args)
    assert not [a for a in args if a.startswith(("--load-extension", "--enable-extensions", "--disable-extensions-except"))]
    assert profile.accept_downloads is False and profile.auto_download_pdfs is False and profile.abort_on_crash is True
    assert profile.enable_default_extensions is False
    assert Path(profile.user_data_dir).name.startswith("reviewreports-audit-profile-")
    assert Path(profile.downloads_path).name.startswith("reviewreports-audit-downloads-")


def test_the_v8_heap_cap_leaves_room_for_two_chromiums_in_the_unit_memory_limit():
    cap = int(next(a for a in AUDIT_CHROMIUM_ARGS if a.startswith("--js-flags=--max-old-space-size=")).rsplit("=", 1)[1])
    assert 0 < cap <= 256            # MemoryMax=1200M is shared by uvicorn, two of these and the raster worker


def test_the_raster_worker_sets_a_small_address_space_cap_before_it_touches_the_pdf(monkeypatch):
    import resource
    from src.cv import raster_worker

    calls = []

    class Stop(Exception):
        pass

    def record(which, limits):
        calls.append((which, limits))
        if which == resource.RLIMIT_CPU:
            raise Stop()                   # the real limits are not applied to the test process

    monkeypatch.setattr(raster_worker.resource, "setrlimit", record)
    with pytest.raises(Stop):
        raster_worker.main()
    cap = raster_worker.MAX_ADDRESS_SPACE
    assert cap <= 256 * 1024 * 1024        # the unit's MemoryMax=1200M is shared with uvicorn and two Chromiums
    assert calls[0] == (resource.RLIMIT_AS, (cap, cap))


def test_the_raster_child_still_renders_a_cv_under_the_cap():
    from src.cv import preview_card
    from tests.helpers import _cv_pdf

    assert preview_card.rasterize_first_page(_cv_pdf()).startswith(b"\x89PNG")


# ── a browser that dies fails its audit at once ──

@pytest.mark.asyncio
@pytest.mark.parametrize("failure, crashed", [
    (ConnectionClosedError(None, None), False),                  # Chromium killed: the CDP connection drops
    (ConnectionError("WebSocket connection closed"), True),      # a renderer crashed: abort_on_crash killed the browser
    (RuntimeError("anything at all"), True),
])
async def test_a_crashed_browser_raises_browser_crashed_not_a_network_error(tmp_root, monkeypatch, failure, crashed):
    monkeypatch.setattr(browser_session, "Browser", _fake(crashed=crashed))
    with pytest.raises(BrowserCrashed) as info:
        async with audit_page("https://example.com/"):
            raise failure
    assert info.value.__cause__ is failure and _left(tmp_root) == []


@pytest.mark.asyncio
async def test_other_failures_pass_through_unchanged(tmp_root, monkeypatch):
    monkeypatch.setattr(browser_session, "Browser", _fake())
    with pytest.raises(SSRFBlocked):
        async with audit_page("https://example.com/"):
            raise SSRFBlocked("went somewhere internal")


@pytest.mark.asyncio
async def test_a_crashed_audit_fails_its_job_with_the_generic_message_and_frees_the_slot(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db")); await store.init()
    monkeypatch.setattr(app_module, "_store", store)
    monkeypatch.setattr(app_module, "_audit_gate", asyncio.Semaphore(2))

    async def crashes(**kw):
        raise BrowserCrashed("the audit browser crashed")

    monkeypatch.setattr(app_module, "generate_report", crashes)
    job = await store.create_job("website_audit", "https://example.com")
    await app_module._run_job(job, "website_audit", "https://example.com", None, "en")
    j = await store.get_job(job)
    assert j["status"] == JobStatus.FAILED.value
    assert j["error"] == "The report could not be generated. Please try again in a few minutes."   # not "could not reach that address"
    assert app_module._audit_gate._value == 2


@pytest.mark.asyncio
async def test_a_database_error_marking_the_job_running_fails_the_job_instead_of_leaving_it_pending(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db")); await store.init()
    monkeypatch.setattr(app_module, "_store", store)
    monkeypatch.setattr(app_module, "_audit_gate", asyncio.Semaphore(2))
    real = store.mark_running
    calls = []

    async def locked(job_id):
        calls.append(job_id)
        raise RuntimeError("database is locked")

    store.mark_running = locked
    ran = []

    async def never(**kw):
        ran.append(1)

    monkeypatch.setattr(app_module, "generate_report", never)
    job = await store.create_job("website_audit", "https://example.com")
    await app_module._run_job(job, "website_audit", "https://example.com", None, "en")
    j = await store.get_job(job)
    assert calls and not ran
    assert j["status"] == JobStatus.FAILED.value and "database" not in (j["error"] or "")
    assert app_module._audit_gate._value == 2
