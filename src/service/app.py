"""reviewreports service: submit a URL/repo/file, get back a structured report.

    uvicorn src.service.app:app --port 8010

Two ways in:
- JSON API: POST /reports {url, kind, model_name, lang} -> job id; GET /reports/{id}
  for status; GET /reports/{id}.md|.html|.tex|.pdf for the rendered report.
- B2C web flow: GET /?lang=ru (submit form) -> POST /submit (multipart, url or
  file upload) -> redirect to GET /view/{id} (progress page that auto-refreshes,
  then embeds the finished report with a PDF download link). The UI and the
  generated report are both rendered in the chosen language (en/ru/fr).

The job model is kind-agnostic: every registered kind in src.review.generate
plugs into both the API and the web flow the same way.

A separate CV builder lives alongside the review kinds: POST /cv {profile,
template} -> id; GET /cv/{id}.html|.pdf|.md for the rendered CV. No job
queue there (unlike the review kinds' minutes-long scans, CV rendering is
synchronous and fast - no LLM call, no browser scan). Its own B2C flow is
GET /cv-builder (fill-in form) -> POST /cv-builder (plain form submit,
mirroring /submit's redirect pattern) -> GET /cv-builder/{id} (all three
templates side by side, pick one, download).
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import unicodedata
import uuid
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.cv.extract import edit_cv_profile, extract_cv_profile
from src.cv.doc_text import DocError, extract_document_text
from src.cv.docx_preview import docx_to_html
from src.cv.pdf_text import MAX_CHARS as CV_TEXT_MAX_CHARS, MAX_UPLOAD_BYTES as CV_PDF_MAX_BYTES
from src.cv.preview_card import RasterError, compose_card, rasterize_first_page
from src.cv.photo import MAX_UPLOAD_BYTES as CV_PHOTO_MAX_BYTES, process_cv_photo, to_data_uri
from src.cv.render import TEMPLATES as CV_TEMPLATES, render_cv_html, render_cv_markdown, render_cv_pdf
from src.cv.schema import Contact, CVProfile, Education, Experience
from src.i18n import SUPPORTED_LANGUAGES, normalize_lang, t_chrome
from src.model import model_manager
from src.report.render import RENDERERS
from src.report.schema import Report
from src.review.generate import generate_report, supported_kinds
from src.service.cv_store import CVStore
from src.service.db import JobStatus, JobStore
from src.service.input_policy import InputRejected, validate_network_input
from src.utils import assemble_project_path

_MEDIA_TYPES = {"markdown": "text/markdown", "html": "text/html", "latex": "text/x-tex", "pdf": "application/pdf"}
_EXTENSION_TO_FORMAT = {"md": "markdown", "html": "html", "tex": "latex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_CV_EXTENSION_TO_FORMAT = {"html": "html", "pdf": "pdf", "md": "markdown"}
_DEFAULT_CV_EXTRACTION_MODEL = "deepseek/deepseek-chat"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

_KIND_LABEL_KEYS = {
    "website_audit": "ui.kind.website_audit",
    "code_review": "ui.kind.code_review",
    "app_review": "ui.kind.app_review",
    "resume_review": "ui.kind.resume_review",
    "presentation_review": "ui.kind.presentation_review",
    "book_review": "ui.kind.book_review",
}
_FILE_KINDS = {"resume_review", "presentation_review", "book_review"}

# code_review/app_review accept a server-side directory path (or, for
# code_review, a git URL) as input_value - fine for a trusted/internal
# caller, but this service is proxied straight through to a public domain
# (tsech.online/audit/), so left unrestricted a public visitor could ask for
# a review of any path on the box (e.g. the directory holding .env) and read
# the result back at a public /view/{id} link. PUBLIC_KINDS opts a
# deployment into restricting which kinds the public routes (home page,
# /submit, /reports) will accept; unset (the default, and every existing
# test) leaves all of supported_kinds() reachable, matching behavior before
# this existed. Sibling internal callers that don't go through these public
# routes are unaffected - there are none today; generate_report() itself is
# not gated.
def _parse_public_kinds(value: Optional[str]) -> Optional[set]:
    # A value that's present but parses to nothing (blank, or all commas) is
    # treated the same as unset - "unrestricted" - rather than the empty set,
    # which would silently reject every kind including website_audit itself.
    parsed = {k.strip() for k in value.split(",") if k.strip()} if value else set()
    return parsed or None


PUBLIC_KINDS = _parse_public_kinds(os.getenv("PUBLIC_KINDS"))


def _kind_allowed(kind: str) -> bool:
    return kind in supported_kinds() and (PUBLIC_KINDS is None or kind in PUBLIC_KINDS)

_UPLOAD_DIR = assemble_project_path("workdir/uploads")

_store = JobStore(db_path="workdir/service/jobs.db")
_cv_store = CVStore(db_path="workdir/service/cv.db")

# Concurrency gates: this service has no auth and (deliberately, see
# PUBLIC_KINDS above) very little else standing between a public request and
# an expensive operation - a website_audit launches a headless Chromium
# (~300MB RAM each), and CV create/edit is a paid LLM call. Unbounded, a
# burst of requests (accidental or not) either OOMs the box or runs up a
# model bill. These reject fast (503) once full rather than queuing
# silently, which would just delay the same resource exhaustion instead of
# preventing it. Separate gates because they bound different resources - an
# audit given a model_name may also call an LLM internally, but that's
# amortized inside the one Chromium-holding slot already, not a second
# thing to queue behind the CV pool for.
_AUDIT_CONCURRENCY = int(os.getenv("AUDIT_MAX_CONCURRENT", "2"))
_LLM_CONCURRENCY = int(os.getenv("CV_MAX_CONCURRENT", "3"))
_audit_gate = asyncio.Semaphore(_AUDIT_CONCURRENCY)
_llm_gate = asyncio.Semaphore(_LLM_CONCURRENCY)
# Reading an uploaded PDF is CPU work done in a thread; keep a handful from piling up.
_pdf_gate = asyncio.Semaphore(2)
# Rendering a link-preview image (CV -> PDF -> PNG -> card) is the heaviest request a crawler can trigger:
# one at a time, waiting its turn briefly rather than rejecting (a crawler that is refused may not retry).
_preview_gate = asyncio.Semaphore(1)
# PDF layout (WeasyPrint) is CPU-bound and takes seconds for a long document: it runs in a thread, at most two at a time,
# and never on the event loop (a synchronous render froze every request - /health included - while it ran).
_render_gate = asyncio.Semaphore(2)
_RENDER_TIMEOUT_SEC = 30
_preview_locks: dict = {}
_PREVIEW_DIR = Path(assemble_project_path("workdir/service/previews"))
_PREVIEW_SITE = "https://tsech.online/audit"

logger = logging.getLogger("reviewreports")


# ---- retention -------------------------------------------------------------------------
# Everything a person creates here (CVs with their photo, uploaded original and preview images; audit
# reports) is deleted this many days after it last CHANGED - opening a link doesn't extend it, otherwise a
# link that is only ever viewed would live forever. tsech's own projects already follow the same rule.
def _retention_days() -> float:
    try:
        return float(os.getenv("RETENTION_DAYS", "7"))
    except ValueError:
        return 7.0


def _expires_at(updated_iso: Optional[str]) -> Optional[str]:
    if not updated_iso:
        return None
    try:
        return (datetime.fromisoformat(updated_iso) + timedelta(days=_retention_days())).isoformat()
    except ValueError:
        return None


def _expires_from_now() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=_retention_days())).isoformat()


def _retention_marker() -> Path:
    return Path(assemble_project_path("workdir/service/.retention_started"))


def _drop_preview_files(cv_id: str) -> None:
    for f in _PREVIEW_DIR.glob(f"{cv_id}.*.jpg"):
        f.unlink(missing_ok=True)


async def _purge_expired(now: Optional[datetime] = None) -> dict:
    """Deletes expired CVs and reports and everything derived from them. The first time this ever runs it
    records its start and does nothing for one full retention period, so data that already existed isn't
    wiped the moment this ships - its clock starts then, not at some arbitrary past date."""
    now = now or datetime.now(timezone.utc)
    days = _retention_days()
    marker = _retention_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(now.isoformat())
    try:
        started = datetime.fromisoformat(marker.read_text().strip())
    except ValueError:
        started = now
    # Preview images of CVs that no longer exist (e.g. deleted while one was rendering) are always safe to drop.
    if _PREVIEW_DIR.exists():
        for f in _PREVIEW_DIR.glob("*.jpg"):
            if await _cv_store.get(f.name.split(".")[0]) is None:
                f.unlink(missing_ok=True)
    if now < started + timedelta(days=days):
        return {"cvs": 0, "reports": 0, "skipped": True}
    cutoff = (now - timedelta(days=days)).isoformat()
    cv_ids = await _cv_store.purge_expired(cutoff)
    for cv_id in cv_ids:
        _drop_preview_files(cv_id)
    reports = await _store.purge_expired(cutoff)
    upload_root = os.path.realpath(_UPLOAD_DIR)
    for _id, subject in reports:
        # A report built from an uploaded file points at it; only ever remove files that live in the uploads dir.
        real = os.path.realpath(subject) if isinstance(subject, str) else ""
        if real.startswith(upload_root + os.sep) and os.path.isfile(real):
            os.unlink(real)
    return {"cvs": len(cv_ids), "reports": len(reports), "skipped": False}


async def _retention_loop():
    while True:
        try:
            result = await _purge_expired()
            if result["cvs"] or result["reports"]:
                logger.info("retention: deleted %s CVs and %s reports", result["cvs"], result["reports"])
        except Exception:
            logger.exception("retention purge failed")
        await asyncio.sleep(3600)


class ServerBusy(Exception):
    pass


async def _render_in_pool(fn, *args, **kwargs):
    """Runs a CPU-heavy render in a thread under _render_gate with a timeout. A thread can't be cancelled, so on
    timeout the caller gets a 503 straight away but the gate is only released when the thread actually finishes -
    timed-out renders can't pile up beyond the gate's size."""
    try:
        await asyncio.wait_for(_render_gate.acquire(), timeout=10)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, lambda: fn(*args, **kwargs))
    fut.add_done_callback(lambda _f: _render_gate.release())
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=_RENDER_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Rendering took too long")


# Only the default model is selectable on public routes: a caller-chosen model_name lets anyone pick the most
# expensive one (deepseek-reasoner) on every request. PUBLIC_MODELS (comma-separated) widens the list deliberately.
def _allowed_models() -> set:
    extra = {m.strip() for m in os.getenv("PUBLIC_MODELS", "").split(",") if m.strip()}
    return {_DEFAULT_CV_EXTRACTION_MODEL} | extra


def _check_model(requested: Optional[str]) -> Optional[str]:
    name = (requested or "").strip()
    if name and name not in _allowed_models():
        raise HTTPException(status_code=400, detail="That model is not available here")
    return name or None


async def _acquire_or_reject(gate: asyncio.Semaphore) -> None:
    """Raises ServerBusy instead of waiting when the gate is already full.
    Checking .locked() and then awaiting acquire() with no other await in
    between is safe under asyncio's single-threaded cooperative scheduling -
    nothing else can steal the slot between the check and the guaranteed-
    immediate acquire, so this never actually blocks the caller."""
    if gate.locked():
        raise ServerBusy()
    await gate.acquire()
_templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _store.init()
    # A job still pending/running at startup belongs to a process that died: its
    # background task is gone, so without this it stays "running" forever and the
    # progress page refreshes indefinitely. (Assumes a single worker process.)
    await _store.fail_unfinished("The service restarted before this job finished. Please submit it again.")
    await _cv_store.init()
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    # Without this, every LLM-dependent pillar/feature across every kind (and
    # CV extraction) fails at call time with "Model X not found. Available
    # models: []" - model_manager's registry is only populated by this call,
    # and nothing else in the service was triggering it.
    await model_manager.initialize()
    loop_task = None if os.getenv("DISABLE_RETENTION_LOOP") == "1" else asyncio.create_task(_retention_loop())
    yield
    if loop_task:
        loop_task.cancel()


app = FastAPI(title="reviewreports", lifespan=lifespan)


class CreateReportRequest(BaseModel):
    url: str
    kind: str = "website_audit"
    model_name: Optional[str] = None
    lang: str = "en"


class JobResponse(BaseModel):
    id: str
    kind: str
    subject: str
    lang: str
    status: str
    report: Optional[dict] = None
    error: Optional[str] = None


def _job_response(job: dict) -> JobResponse:
    report = json.loads(job["report_json"]) if job["report_json"] else None
    return JobResponse(id=job["id"], kind=job["kind"], subject=job["subject"], lang=job["lang"], status=job["status"], report=report, error=job["error"])


async def _run_job(job_id: str, kind: str, url: str, model_name: Optional[str], lang: str) -> None:
    try:
        await _acquire_or_reject(_audit_gate)
    except ServerBusy:
        # The job record already exists (created before this background task
        # was scheduled) and the client is polling it, so "busy" has to be
        # reported through that record rather than an HTTP response - there
        # isn't one to give anymore by the time this runs.
        await _store.mark_failed(job_id, "Server is busy with other checks right now - please try again in a minute.")
        return
    try:
        await _store.mark_running(job_id)
        try:
            report = await generate_report(kind=kind, input=url, model_name=model_name, lang=lang)
            await _store.mark_done(job_id, report.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - persist any failure onto the job record
            await _store.mark_failed(job_id, str(exc))
    finally:
        _audit_gate.release()


# ---- JSON API ----------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "reviewreports"}


@app.post("/reports", response_model=JobResponse)
async def create_report(request: CreateReportRequest, background_tasks: BackgroundTasks):
    if not _kind_allowed(request.kind):
        raise HTTPException(status_code=400, detail=f"Unsupported report kind: {request.kind}. Supported: {supported_kinds()}")

    try:
        await validate_network_input(request.kind, request.url)
    except InputRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    lang = normalize_lang(request.lang)
    job_id = await _store.create_job(kind=request.kind, subject=request.url, lang=lang)
    background_tasks.add_task(_run_job, job_id, request.kind, request.url, _check_model(request.model_name), lang)
    job = await _store.get_job(job_id)
    return _job_response(job)


@app.get("/reports/{job_id}.{extension}")
async def get_report_rendered(job_id: str, extension: str, footer: str = "1"):
    # Registered before /reports/{job_id}: Starlette's {job_id} path converter
    # matches dots too, so a more general route registered first would swallow
    # "abc123.md" whole as job_id and never reach this handler.
    #
    # footer=0 is used when this is embedded in a page that already shows its
    # own page-level "Create with tsech.online" footer (reviewreports' own
    # /view/{id}) — everywhere else (direct downloads, tsech's own embedded
    # preview) defaults to footer=1 so the branding still shows up there.
    output_format = _EXTENSION_TO_FORMAT.get(extension)
    if output_format is None:
        raise HTTPException(status_code=400, detail=f"Unsupported extension '.{extension}'. Use .md, .html, .tex, or .pdf.")

    job = await _store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == JobStatus.FAILED.value:
        raise HTTPException(status_code=422, detail=f"Report generation failed: {job['error']}")
    if job["status"] != JobStatus.DONE.value:
        raise HTTPException(status_code=409, detail=f"Report not ready yet (status: {job['status']})")

    report = Report.model_validate(json.loads(job["report_json"]))
    try:
        rendered = await _render_in_pool(RENDERERS[output_format], report, include_footer=footer != "0")
    except (ImportError, OSError) as exc:  # OSError: WeasyPrint without system Pango/GObject
        raise HTTPException(status_code=503, detail=f"PDF rendering is unavailable on this server: {exc}")

    if output_format in _BINARY_FORMATS:
        return Response(content=rendered, media_type=_MEDIA_TYPES[output_format])
    return PlainTextResponse(content=rendered, media_type=_MEDIA_TYPES[output_format])


@app.get("/reports/{job_id}", response_model=JobResponse)
async def get_report(job_id: str):
    job = await _store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_response(job)


# ---- CV builder ---------------------------------------------------------------


class CreateCVRequest(CVProfile):
    template: str = "modern"


def _orig(row: dict, fresh: bool = False) -> dict:
    """The stored-file fields and expiry every CV response carries. fresh: the call just changed the CV."""
    return {"has_original": bool(row.get("has_original")), "original_name": row.get("original_name"), "original_kind": row.get("original_kind"),
            "expires_at": _expires_from_now() if fresh else _expires_at(row.get("updated_at"))}


def _safe_filename(name: Optional[str], kind: str) -> str:
    """A display/download name for an uploaded file: no path, no control or odd characters, right extension."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"\.(pdf|docx)$", "", base, flags=re.I)
    base = "".join(c for c in unicodedata.normalize("NFC", base) if c.isprintable() and c not in '<>:"|?*\\/')
    return (base.strip(" .")[:80] or "cv") + f".{kind}"


class CVResponse(BaseModel):
    id: str
    template: str
    profile: dict
    has_photo: bool = False  # the photo itself is never returned here - it's merged in at render time
    # The file this CV was built from (PDF/Word), when one was uploaded: only its existence/name/kind here,
    # the bytes are served by GET /cv/{id}/original.
    has_original: bool = False
    original_name: Optional[str] = None
    original_kind: Optional[str] = None
    # When this CV and everything stored for it will be deleted if it isn't changed again (see retention above).
    expires_at: Optional[str] = None


class CVCreateResponse(CVResponse):
    # Only ever returned here, at creation - never by GET /cv/{id} or any
    # rendered page - so a CV's share link (its bare id) grants read/render
    # access but not edit access. Whoever creates a CV is responsible for
    # keeping this if they want to revise it later via POST /cv/{id}/edit.
    edit_token: str


@app.post("/cv", response_model=CVCreateResponse)
async def create_cv(request: CreateCVRequest):
    if request.template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{request.template}'. Must be one of {CV_TEMPLATES}.")

    profile = CVProfile.model_validate(request.model_dump(exclude={"template"}))
    cv_id, edit_token = await _cv_store.create(profile, request.template)
    _prewarm_preview(cv_id)
    return CVCreateResponse(id=cv_id, template=request.template, profile=profile.model_dump(), edit_token=edit_token, expires_at=_expires_from_now())


class CreateCVFromTextRequest(BaseModel):
    # Bounds the size of what gets sent to the model on an unauthenticated,
    # unmetered endpoint - far beyond any real resume/bio, well under
    # nginx's request-body cap.
    text: str = Field(..., max_length=20_000)
    model_name: Optional[str] = None
    template: str = "modern"


@app.post("/cv/from-text", response_model=CVCreateResponse)
async def create_cv_from_text(request: CreateCVFromTextRequest):
    """JSON counterpart of the /cv-builder/from-text form flow, for clients
    that render the result themselves (tsech's "Create CV" mode): free text
    in, an extracted-and-stored CV out, same shape as POST /cv."""
    if request.template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{request.template}'. Must be one of {CV_TEMPLATES}.")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is empty")

    try:
        await _acquire_or_reject(_llm_gate)
    except ServerBusy:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    try:
        profile = await extract_cv_profile(text, _check_model(request.model_name) or _DEFAULT_CV_EXTRACTION_MODEL)
    finally:
        _llm_gate.release()
    if profile is None:
        raise HTTPException(status_code=422, detail="Could not extract a CV from that text")

    cv_id, edit_token = await _cv_store.create(profile, request.template)
    _prewarm_preview(cv_id)
    return CVCreateResponse(id=cv_id, template=request.template, profile=profile.model_dump(), edit_token=edit_token, expires_at=_expires_from_now())


class CVCreateFromFileResponse(CVCreateResponse):  # also carries has_original/original_name/original_kind
    source_text: str  # what was read from the file (plus any typed notes), so a client can show it as the CV's source


@app.post("/cv/from-file", response_model=CVCreateFromFileResponse)
@app.post("/cv/from-pdf", response_model=CVCreateFromFileResponse, include_in_schema=False)  # the name an earlier client used
async def create_cv_from_file(
    file: UploadFile = File(...), template: str = Form("modern"), model_name: Optional[str] = Form(None),
    extra_text: str = Form("", max_length=5_000),
):
    """An existing CV as a PDF or Word (.docx) file in, the same stored/editable/shareable CV out as
    POST /cv/from-text: the text is read from the file and goes through the same extraction. Anything the
    person typed alongside it (extra_text) is added as notes. The file itself isn't kept."""
    if template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{template}'. Must be one of {CV_TEMPLATES}.")
    raw = await file.read(CV_PDF_MAX_BYTES + 1)
    if len(raw) > CV_PDF_MAX_BYTES:
        raise HTTPException(status_code=413, detail={"code": "doc_too_large"})

    try:
        await _acquire_or_reject(_pdf_gate)
    except ServerBusy:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    try:
        text = await asyncio.wait_for(asyncio.to_thread(extract_document_text, raw), timeout=20)
    except DocError as e:
        # unsupported / legacy / encrypted / too_many_pages / unreadable are the file's fault (400); no_text is a valid file we can't use (422)
        raise HTTPException(status_code=422 if e.code == "no_text" else 400, detail={"code": f"doc_{e.code}"})
    except asyncio.TimeoutError:
        raise HTTPException(status_code=400, detail={"code": "doc_unreadable"})
    finally:
        _pdf_gate.release()

    notes = extra_text.strip()
    if notes:
        text = f"{text}\n\nAdditional notes from the person:\n{notes}"[:CV_TEXT_MAX_CHARS + 5_100]

    try:
        await _acquire_or_reject(_llm_gate)
    except ServerBusy:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    try:
        profile = await extract_cv_profile(text, _check_model(model_name) or _DEFAULT_CV_EXTRACTION_MODEL)
    finally:
        _llm_gate.release()
    if profile is None:
        raise HTTPException(status_code=422, detail={"code": "doc_no_cv"})

    cv_id, edit_token = await _cv_store.create(profile, template)
    # Kept with the CV so it can be shown next to our templates (and removed by whoever holds the edit token).
    kind = "pdf" if b"%PDF-" in raw[:1024] else "docx"
    orig_name = _safe_filename(file.filename, kind)
    await _cv_store.set_original(cv_id, raw, orig_name, kind)
    _prewarm_preview(cv_id)
    return CVCreateFromFileResponse(id=cv_id, template=template, profile=profile.model_dump(), edit_token=edit_token, source_text=text, expires_at=_expires_from_now(),
                                    has_original=True, original_name=orig_name, original_kind=kind)


class EditCVRequest(BaseModel):
    instructions: str = Field(..., max_length=2_000)
    model_name: Optional[str] = None
    edit_token: str


@app.post("/cv/{cv_id}/edit", response_model=CVResponse)
async def edit_cv(cv_id: str, request: EditCVRequest):
    """Revises a stored CV from a plain-language change request - the CV
    equivalent of "describe changes" on a generated app or document. Keeps
    the same id/template/share link; only the stored profile changes.

    Requires the token minted at creation - without this, a CV's id (which
    is also its public share link) would have been enough on its own to
    rewrite anyone's shared CV."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    if not _cv_store.verify_edit_token(row, request.edit_token):
        raise HTTPException(status_code=403, detail="Invalid or missing edit token")

    instructions = request.instructions.strip()
    if not instructions:
        raise HTTPException(status_code=422, detail="instructions is empty")

    try:
        await _acquire_or_reject(_llm_gate)
    except ServerBusy:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    try:
        current = await _cv_store.get_profile(cv_id)
        revised = await edit_cv_profile(current, instructions, _check_model(request.model_name) or _DEFAULT_CV_EXTRACTION_MODEL)
    finally:
        _llm_gate.release()
    if revised is None:
        raise HTTPException(status_code=422, detail="Could not apply that change")

    await _cv_store.update_profile(cv_id, revised)
    return CVResponse(id=cv_id, template=row["template"], profile=revised.model_dump(), has_photo=bool(row.get("has_photo")), **_orig(row, fresh=True))


@app.post("/cv/{cv_id}/photo", response_model=CVResponse)
async def set_cv_photo(cv_id: str, file: UploadFile = File(...), edit_token: str = Form(...)):
    """Sets (or replaces) the CV's profile photo. Same credential as editing the CV: a
    CV's id is also its public share link, so the id alone must not be enough to put
    someone's picture on it. The upload is decoded and re-encoded - see src/cv/photo.py."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    if not _cv_store.verify_edit_token(row, edit_token):
        raise HTTPException(status_code=403, detail="Invalid or missing edit token")
    raw = await file.read(CV_PHOTO_MAX_BYTES + 1)
    if len(raw) > CV_PHOTO_MAX_BYTES:
        raise HTTPException(status_code=413, detail={"code": "photo_too_large"})
    try:
        jpeg = await asyncio.to_thread(process_cv_photo, raw)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "bad_image"})
    await _cv_store.set_photo(cv_id, jpeg)
    _prewarm_preview(cv_id)
    return CVResponse(id=cv_id, template=row["template"], profile=json.loads(row["profile_json"]), has_photo=True, **_orig(row, fresh=True))


@app.delete("/cv/{cv_id}/photo", response_model=CVResponse)
async def remove_cv_photo(cv_id: str, x_edit_token: str = Header("")):
    """Removes the photo. The token travels in a header, not the URL: URLs end up in access logs."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    if not _cv_store.verify_edit_token(row, x_edit_token):
        raise HTTPException(status_code=403, detail="Invalid or missing edit token")
    await _cv_store.set_photo(cv_id, None)
    _prewarm_preview(cv_id)
    return CVResponse(id=cv_id, template=row["template"], profile=json.loads(row["profile_json"]), has_photo=False, **_orig(row, fresh=True))


_ORIGINAL_MEDIA = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


@app.get("/cv/{cv_id}/original")
async def get_cv_original(cv_id: str, download: bool = False):
    """The file this CV was built from, exactly as uploaded. A PDF is shown inline (the browser's own
    viewer); a Word file can only be downloaded - /original.html is its on-page preview."""
    orig = await _cv_store.get_original(cv_id)
    if orig is None:
        raise HTTPException(status_code=404, detail="No original file for this CV")
    inline = orig["kind"] == "pdf" and not download
    disposition = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(orig['name'] or 'cv.' + orig['kind'])}"
    return Response(content=orig["data"], media_type=_ORIGINAL_MEDIA[orig["kind"]],
                    headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"})


def _pdf_original_page(cv_id: str, filename: str, download_label: str) -> str:
    """A page around an uploaded PDF so its link can carry a preview card (a bare PDF URL can't) and so
    the file opens in the browser's own viewer with a download button."""
    src = f"/audit/cv/{cv_id}/original"
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(filename)}</title><style>"
        "html,body{margin:0;height:100%;background:#f2f3f5;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif}"
        ".bar{display:flex;align-items:center;gap:12px;height:44px;padding:0 14px;background:#fff;border-bottom:1px solid #e5e5e5;font-size:13px}"
        ".bar span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#4a4a4a}"
        ".bar a{color:#1a1a1a;text-decoration:none;border:1px solid #e5e5e5;border-radius:5px;padding:5px 10px;font-weight:600;font-size:12px;background:#f7f7f7}"
        "iframe{display:block;width:100%;height:calc(100% - 45px);border:0}"
        f'</style></head><body><div class="bar"><span>{html.escape(filename)}</span><a href="{src}?download=1">{html.escape(download_label)}</a></div>'
        f'<iframe src="{src}" title="{html.escape(filename)}"></iframe></body></html>'
    )


@app.get("/cv/{cv_id}/original.html", response_class=HTMLResponse)
async def get_cv_original_page(cv_id: str, lang: str = "en"):
    """The shareable page for a CV's uploaded original: a PDF in the browser's viewer, a Word file as a readable
    preview (see src/cv/docx_preview.py), either with a preview card for Telegram/WhatsApp/LinkedIn/Facebook."""
    orig = await _cv_store.get_original(cv_id)
    row = await _cv_store.get(cv_id)
    if orig is None or row is None:
        raise HTTPException(status_code=404, detail="No original file for this CV")
    lang = normalize_lang(lang)
    profile = await _cv_store.get_profile(cv_id)
    title, description = _build_cv_share_meta(profile, lang)
    if orig["kind"] == "pdf":
        page = _pdf_original_page(cv_id, orig["name"] or "cv.pdf", t_chrome("ui.cv.download_original", lang))
    else:
        try:
            page = await asyncio.to_thread(docx_to_html, orig["data"])
        except Exception:
            raise HTTPException(status_code=422, detail="Could not preview that file")
    page = _inject_og_meta(page, title=title, description=description, url=f"{_PREVIEW_SITE}/cv/{cv_id}/original.html",
                           image=f"{_PREVIEW_SITE}/cv/{cv_id}/original-preview.jpg")
    # Escaped by construction (Word text) / fixed markup (PDF page), and locked down anyway: no script, no network;
    # the PDF page may only frame our own origin.
    return HTMLResponse(page, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-src 'self'",
        "X-Content-Type-Options": "nosniff",
    })


_PREVIEW_HEADERS = {"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"}


@app.get("/cv/{cv_id}/preview.jpg")
async def get_cv_preview(cv_id: str, template: Optional[str] = None):
    """The 1200x630 image social networks show for a link to this CV (page 1 plus name and role)."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    chosen = template or row["template"]
    if chosen not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{chosen}'. Must be one of {CV_TEMPLATES}.")
    return Response(content=await _cv_preview_jpeg(cv_id, row, chosen), media_type="image/jpeg", headers=_PREVIEW_HEADERS)


@app.get("/cv/{cv_id}/original-preview.jpg")
async def get_cv_original_preview_image(cv_id: str):
    """Same, for the uploaded original: its own first page when it's a PDF, otherwise the CV in its default template."""
    row = await _cv_store.get(cv_id)
    if row is None or not row.get("has_original"):
        raise HTTPException(status_code=404, detail="No original file for this CV")
    return Response(content=await _cv_preview_jpeg(cv_id, row, row["template"], original=True), media_type="image/jpeg", headers=_PREVIEW_HEADERS)


@app.delete("/cv/{cv_id}/original", response_model=CVResponse)
async def remove_cv_original(cv_id: str, x_edit_token: str = Header("")):
    """Deletes the stored original file (the CV and its templates stay). Token in a header, not the URL."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    if not _cv_store.verify_edit_token(row, x_edit_token):
        raise HTTPException(status_code=403, detail="Invalid or missing edit token")
    await _cv_store.set_original(cv_id, None)
    return CVResponse(id=cv_id, template=row["template"], profile=json.loads(row["profile_json"]), has_photo=bool(row.get("has_photo")), expires_at=_expires_from_now())


@app.get("/cv/{cv_id}.{extension}")
async def get_cv_rendered(cv_id: str, extension: str, template: Optional[str] = None):
    # Registered before /cv/{cv_id}, same reason as /reports/{job_id}.{extension}
    # above: Starlette's {cv_id} path converter matches dots too.
    output_format = _CV_EXTENSION_TO_FORMAT.get(extension)
    if output_format is None:
        raise HTTPException(status_code=400, detail=f"Unsupported extension '.{extension}'. Use .html, .pdf, or .md.")

    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")

    profile = await _cv_store.get_profile(cv_id)
    photo_bytes = await _cv_store.get_photo(cv_id) if row.get("has_photo") and output_format != "markdown" else None
    photo_uri = to_data_uri(photo_bytes) if photo_bytes else None
    chosen_template = template or row["template"]
    if output_format != "markdown" and chosen_template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{chosen_template}'. Must be one of {CV_TEMPLATES}.")

    if output_format == "html":
        rendered = render_cv_html(profile, chosen_template, photo_uri)
        # Also give a direct link to one of these documents (people share
        # them as-is, not just the /cv-builder/{id} comparison page) a real
        # preview card instead of a blank one.
        title, description = _build_cv_share_meta(profile, "en")
        canonical_url = f"https://tsech.online/audit/cv/{cv_id}.html?template={chosen_template}"
        rendered = _inject_og_meta(rendered, title=title, description=description, url=canonical_url,
                                   image=f"{_PREVIEW_SITE}/cv/{cv_id}/preview.jpg?template={chosen_template}")
    elif output_format == "pdf":
        try:
            rendered = await _render_in_pool(render_cv_pdf, profile, chosen_template, photo_uri)
        except (ImportError, OSError) as exc:  # OSError: WeasyPrint without system Pango/GObject
            raise HTTPException(status_code=503, detail=f"PDF rendering is unavailable on this server: {exc}")
    else:
        rendered = render_cv_markdown(profile)

    if output_format == "pdf":
        return Response(content=rendered, media_type="application/pdf")
    media_type = "text/html" if output_format == "html" else "text/markdown"
    return PlainTextResponse(content=rendered, media_type=media_type)


@app.delete("/cv/{cv_id}")
async def delete_cv(cv_id: str, x_edit_token: str = Header("")):
    """Deletes the CV for good: its profile, profile photo and the uploaded original file, so its share
    link and every rendered/original URL stop working. Needs the edit token (the id alone is a public
    share link); the token travels in a header, not the URL."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    if not _cv_store.verify_edit_token(row, x_edit_token):
        raise HTTPException(status_code=403, detail="Invalid or missing edit token")
    await _cv_store.delete(cv_id)
    _drop_preview_files(cv_id)
    return {"deleted": True}


@app.get("/cv/{cv_id}", response_model=CVResponse)
async def get_cv(cv_id: str):
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    return CVResponse(id=row["id"], template=row["template"], profile=json.loads(row["profile_json"]), has_photo=bool(row.get("has_photo")), **_orig(row))


# ---- B2C web flow --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, lang: str = "en"):
    lang = normalize_lang(lang)
    kinds = {kind: t_chrome(key, lang) for kind, key in _KIND_LABEL_KEYS.items() if _kind_allowed(kind)}
    return _templates.TemplateResponse(
        request,
        "home.html",
        {
            "lang": lang,
            "languages": SUPPORTED_LANGUAGES,
            "kinds": kinds,
            "file_kinds": sorted(_FILE_KINDS),
            "t": lambda key, **kw: t_chrome(key, lang, **kw),
        },
    )


@app.post("/submit")
async def submit(
    background_tasks: BackgroundTasks,
    kind: str = Form(...),
    input_value: str = Form(""),
    model_name: str = Form(""),
    lang: str = Form("en"),
    file: Optional[UploadFile] = File(None),
):
    if not _kind_allowed(kind):
        raise HTTPException(status_code=400, detail=f"Unsupported report kind: {kind}")
    lang = normalize_lang(lang)
    kind_label = t_chrome(_KIND_LABEL_KEYS[kind], lang)

    if kind in _FILE_KINDS:
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail=f"'{kind_label}' requires a file upload")
        upload_id = uuid.uuid4().hex
        dest = os.path.join(_UPLOAD_DIR, f"{upload_id}_{os.path.basename(file.filename)}")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
        with open(dest, "wb") as f:
            f.write(data)
        subject = dest
    else:
        if not input_value.strip():
            raise HTTPException(status_code=400, detail=f"'{kind_label}' requires a URL or path")
        subject = input_value.strip()
        try:
            await validate_network_input(kind, subject)
        except InputRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    job_id = await _store.create_job(kind=kind, subject=subject, lang=lang)
    background_tasks.add_task(_run_job, job_id, kind, subject, _check_model(model_name), lang)
    return RedirectResponse(url=f"/view/{job_id}", status_code=303)


def _build_fix_prompt(subject: str, report: Optional[dict], lang: str) -> Optional[str]:
    recommendations = (report or {}).get("recommendations") or []
    if not recommendations:
        return None
    ordered = sorted(recommendations, key=lambda r: r.get("priority", 0))
    lines = [f"{i + 1}. {r.get('action', '')}\n   {r.get('rationale', '')}" for i, r in enumerate(ordered)]
    header = t_chrome("ui.fix_prompt_header", lang)
    return f"{header} {subject}:\n\n" + "\n\n".join(lines)


def _build_share_meta(job: dict, report: Optional[dict], lang: str) -> tuple[str, str]:
    """og:title / og:description для превью ссылки в Telegram/WhatsApp — без
    этого мессенджеры показывали пустую карточку без текста и картинки."""
    kind_label = t_chrome(_KIND_LABEL_KEYS.get(job["kind"], "ui.kind.website_audit"), lang)
    title = f"{kind_label} — {job['subject']}"
    if job["status"] == "failed":
        return title, t_chrome("ui.failed_title", lang)
    if not report:
        return title, t_chrome("ui.generating_title", lang)
    score = report.get("overall_score")
    verdict = report.get("verdict") or ""
    description = f"{score}/10 — {verdict}" if score is not None else (verdict or t_chrome("ui.ready_title", lang))
    return title, description


def _build_cv_share_meta(profile: CVProfile, lang: str) -> tuple[str, str]:
    """og:title / og:description for a shared CV link — same idea as
    _build_share_meta above, applied to the CV builder's own pages."""
    label = t_chrome("ui.cv.share_label", lang)
    title = f"{profile.name} — {label}" if profile.name else label
    create_with = f"{t_chrome('ui.create_with', lang)} tsech.online"
    highlight = profile.role or profile.summary
    description = f"{highlight} · {create_with}" if highlight else create_with
    return title, description


_HEAD_OPEN_RE = re.compile(r"<head>", re.IGNORECASE)


_LOGO = "https://tsech.online/logo.png"


def _inject_og_meta(rendered_html: str, *, title: str, description: str, url: str, image: Optional[str] = None) -> str:
    """Splices og:/twitter: meta tags into an already-rendered HTML document's <head>, so a link to it gets a
    real preview card in Telegram, WhatsApp, LinkedIn, Facebook and X instead of a blank one. With `image`
    (a 1200x630 JPEG of this CV, see src/cv/preview_card.py) it's the large "summary_large_image" card;
    without, the static tsech logo as a small card."""
    t, d, u = html.escape(title), html.escape(description), html.escape(url)
    if image:
        img = html.escape(image)
        picture = (f'<meta property="og:image" content="{img}"><meta property="og:image:secure_url" content="{img}">'
                   '<meta property="og:image:type" content="image/jpeg"><meta property="og:image:width" content="1200">'
                   f'<meta property="og:image:height" content="630"><meta property="og:image:alt" content="{t}">'
                   f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{img}">')
    else:
        picture = (f'<meta property="og:image" content="{_LOGO}"><meta name="twitter:card" content="summary">'
                   f'<meta name="twitter:image" content="{_LOGO}">')
    tags = (
        '<meta property="og:type" content="website">'
        '<meta property="og:site_name" content="tsech">'
        f'<meta property="og:title" content="{t}">'
        f'<meta property="og:description" content="{d}">'
        f'<meta property="og:url" content="{u}">'
        f'<meta name="description" content="{d}">'
        f'<meta name="twitter:title" content="{t}">'
        f'<meta name="twitter:description" content="{d}">'
        + picture
    )
    injected, count = _HEAD_OPEN_RE.subn(f"<head>{tags}", rendered_html, count=1)
    return injected if count else rendered_html


# ---- link-preview images ---------------------------------------------------------------
_bg_tasks: set = set()


def _preview_key(row: dict, label: str) -> str:
    return hashlib.sha1(f"{row.get('updated_at')}|{label}|{int(bool(row.get('has_photo')))}".encode()).hexdigest()[:10]


async def _cv_preview_jpeg(cv_id: str, row: dict, template: str, original: bool = False) -> bytes:
    """The 1200x630 share image for a CV (or, original=True, for its uploaded PDF's first page). Cached on
    disk under a key that changes whenever the CV changes, so a stale preview is never served."""
    label = "original" if original else template
    path = _PREVIEW_DIR / f"{cv_id}.{label}.{_preview_key(row, label)}.jpg"
    if path.exists():
        return path.read_bytes()
    lock = _preview_locks.setdefault(f"{cv_id}.{label}", asyncio.Lock())
    try:
        async with lock:
            if path.exists():
                return path.read_bytes()
            try:
                await asyncio.wait_for(_preview_gate.acquire(), timeout=15)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=503, detail="Preview is busy - try again shortly")
            try:
                profile = CVProfile.model_validate(json.loads(row["profile_json"]))
                png = None
                if original:
                    orig = await _cv_store.get_original(cv_id)
                    if orig and orig["kind"] == "pdf":
                        try:
                            png = await asyncio.to_thread(rasterize_first_page, orig["data"])
                        except RasterError:
                            png = None  # an unrenderable upload still gets a card: our own render of the CV
                if png is None:
                    photo = await _cv_store.get_photo(cv_id) if row.get("has_photo") else None
                    pdf = await _render_in_pool(render_cv_pdf, profile, template, to_data_uri(photo) if photo else None)
                    png = await asyncio.to_thread(rasterize_first_page, pdf)
                card = await asyncio.to_thread(compose_card, png, profile.name, profile.role or None)
            except RasterError:
                raise HTTPException(status_code=503, detail="Could not render a preview")
            finally:
                _preview_gate.release()
            _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(card)
            tmp.replace(path)
            for old in _PREVIEW_DIR.glob(f"{cv_id}.{label}.*.jpg"):
                if old != path:
                    old.unlink(missing_ok=True)
            return card
    finally:
        _preview_locks.pop(f"{cv_id}.{label}", None)


def _prewarm_preview(cv_id: str) -> None:
    """Renders the default template's preview in the background after a CV is created or changed, so the
    first crawler that fetches a freshly shared link finds it ready instead of waiting on a render."""
    async def run():
        try:
            row = await _cv_store.get(cv_id)
            if row:
                await _cv_preview_jpeg(cv_id, row, row["template"])
        except Exception:
            pass
    task = asyncio.create_task(run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@app.get("/view/{job_id}", response_class=HTMLResponse)
async def view_report(request: Request, job_id: str):
    job = await _store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    lang = normalize_lang(job["lang"])
    report = json.loads(job["report_json"]) if job["report_json"] else None
    fix_prompt = _build_fix_prompt(job["subject"], report, lang)
    meta_title, meta_description = _build_share_meta(job, report, lang)
    return _templates.TemplateResponse(request, "view.html", {
        "job": job, "report": report, "fix_prompt": fix_prompt, "lang": lang,
        "meta_title": meta_title, "meta_description": meta_description,
        "t": lambda key, **kw: t_chrome(key, lang, **kw),
    })


# ---- CV builder web flow -------------------------------------------------------


def _render_cv_builder_form(request: Request, lang: str, *, profile: Optional[CVProfile] = None, extract_error: Optional[str] = None, pasted_text: str = "", status_code: int = 200) -> HTMLResponse:
    return _templates.TemplateResponse(
        request,
        "cv_builder.html",
        {
            "lang": lang,
            "languages": SUPPORTED_LANGUAGES,
            "t": lambda key, **kw: t_chrome(key, lang, **kw),
            "profile": profile.model_dump() if profile else None,
            "extract_error": extract_error,
            "pasted_text": pasted_text,
        },
        status_code=status_code,
    )


@app.get("/cv-builder", response_class=HTMLResponse)
async def cv_builder_form(request: Request, lang: str = "en"):
    lang = normalize_lang(lang)
    return _render_cv_builder_form(request, lang)


@app.post("/cv-builder/from-text", response_class=HTMLResponse)
async def cv_builder_from_text(request: Request, text: str = Form(..., max_length=20_000), model_name: str = Form(""), lang: str = Form("en")):
    lang = normalize_lang(lang)
    chosen_model = _check_model(model_name)
    # The same ceiling as /cv/from-text: this route used to call the model with no size cap and outside the concurrency gate.
    try:
        await _acquire_or_reject(_llm_gate)
    except ServerBusy:
        raise HTTPException(status_code=503, detail="Server is busy right now - please try again shortly")
    try:
        profile = await extract_cv_profile(text.strip(), chosen_model or _DEFAULT_CV_EXTRACTION_MODEL)
    finally:
        _llm_gate.release()
    if profile is None:
        return _render_cv_builder_form(request, lang, extract_error=t_chrome("ui.cv.extract_failed", lang), pasted_text=text, status_code=422)
    return _render_cv_builder_form(request, lang, profile=profile)


def _split_csv(value: str) -> list:
    return [part.strip() for part in value.split(",") if part.strip()]


@app.post("/cv-builder")
async def cv_builder_submit(
    name: str = Form(...),
    role: str = Form(""),
    summary: str = Form(""),
    email: str = Form(...),
    phone: str = Form(""),
    location: str = Form(""),
    links: str = Form(""),
    skills: str = Form(""),
    languages: str = Form(""),
    lang: str = Form("en"),
    exp_title: List[str] = Form([]),
    exp_organization: List[str] = Form([]),
    exp_location: List[str] = Form([]),
    exp_start: List[str] = Form([]),
    exp_end: List[str] = Form([]),
    exp_bullets: List[str] = Form([]),
    edu_degree: List[str] = Form([]),
    edu_institution: List[str] = Form([]),
    edu_year: List[str] = Form([]),
):
    lang = normalize_lang(lang)
    if not name.strip() or not email.strip():
        raise HTTPException(status_code=422, detail="Name and email are required")

    experience = []
    for i, title in enumerate(exp_title):
        if not title.strip():
            continue
        bullets = [b.strip() for b in exp_bullets[i].splitlines() if b.strip()] if i < len(exp_bullets) else []
        experience.append(
            Experience(
                title=title.strip(),
                organization=exp_organization[i].strip() if i < len(exp_organization) else "",
                location=(exp_location[i].strip() or None) if i < len(exp_location) else None,
                start=exp_start[i].strip() if i < len(exp_start) else "",
                end=(exp_end[i].strip() or "Present") if i < len(exp_end) else "Present",
                bullets=bullets,
            )
        )

    education = []
    for i, degree in enumerate(edu_degree):
        if not degree.strip():
            continue
        education.append(
            Education(
                degree=degree.strip(),
                institution=edu_institution[i].strip() if i < len(edu_institution) else "",
                year=(edu_year[i].strip() or None) if i < len(edu_year) else None,
            )
        )

    profile = CVProfile(
        name=name.strip(),
        role=role.strip() or None,
        summary=summary.strip() or None,
        contact=Contact(email=email.strip(), phone=phone.strip() or None, location=location.strip() or None, links=_split_csv(links)),
        experience=experience,
        education=education,
        skills=_split_csv(skills),
        languages=_split_csv(languages),
    )

    # The web form has no "describe changes" UI, so the edit token is simply
    # never surfaced anywhere for a CV created this way - same fail-closed
    # effect as not having one.
    cv_id, _edit_token = await _cv_store.create(profile, template=CV_TEMPLATES[0])
    return RedirectResponse(url=f"/cv-builder/{cv_id}?lang={lang}", status_code=303)


@app.get("/cv-builder/{cv_id}", response_class=HTMLResponse)
async def cv_builder_result(request: Request, cv_id: str, lang: str = "en"):
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    lang = normalize_lang(lang)
    profile = await _cv_store.get_profile(cv_id)
    template_labels = {name: t_chrome(f"ui.cv.template.{name}", lang) for name in CV_TEMPLATES}
    meta_title, meta_description = _build_cv_share_meta(profile, lang)
    return _templates.TemplateResponse(
        request,
        "cv_result.html",
        {
            "lang": lang, "cv_id": cv_id, "cv_templates": template_labels,
            "original": {"name": row.get("original_name"), "kind": row.get("original_kind")} if row.get("has_original") else None,
            "og_image": f"{_PREVIEW_SITE}/cv/{cv_id}/preview.jpg?template={row['template']}",
            "meta_title": meta_title, "meta_description": meta_description,
            "t": lambda key, **kw: t_chrome(key, lang, **kw),
        },
    )
