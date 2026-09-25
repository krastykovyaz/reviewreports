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
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.i18n import SUPPORTED_LANGUAGES, normalize_lang, t_chrome
from src.report.render import RENDERERS
from src.report.schema import Report
from src.review.generate import generate_report, supported_kinds
from src.service.db import JobStatus, JobStore
from src.utils import assemble_project_path

_MEDIA_TYPES = {"markdown": "text/markdown", "html": "text/html", "latex": "text/x-tex", "pdf": "application/pdf"}
_EXTENSION_TO_FORMAT = {"md": "markdown", "html": "html", "tex": "latex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_KIND_LABEL_KEYS = {
    "website_audit": "ui.kind.website_audit",
    "code_review": "ui.kind.code_review",
    "resume_review": "ui.kind.resume_review",
    "presentation_review": "ui.kind.presentation_review",
    "book_review": "ui.kind.book_review",
}
_FILE_KINDS = {"resume_review", "presentation_review", "book_review"}

_UPLOAD_DIR = assemble_project_path("workdir/uploads")

_store = JobStore(db_path="workdir/service/jobs.db")
_templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _store.init()
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    yield


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
    await _store.mark_running(job_id)
    try:
        report = await generate_report(kind=kind, input=url, model_name=model_name, lang=lang)
        await _store.mark_done(job_id, report.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001 - persist any failure onto the job record
        await _store.mark_failed(job_id, str(exc))


# ---- JSON API ----------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "reviewreports"}


@app.post("/reports", response_model=JobResponse)
async def create_report(request: CreateReportRequest, background_tasks: BackgroundTasks):
    if request.kind not in supported_kinds():
        raise HTTPException(status_code=400, detail=f"Unsupported report kind: {request.kind}. Supported: {supported_kinds()}")

    lang = normalize_lang(request.lang)
    job_id = await _store.create_job(kind=request.kind, subject=request.url, lang=lang)
    background_tasks.add_task(_run_job, job_id, request.kind, request.url, request.model_name, lang)
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
        rendered = RENDERERS[output_format](report, include_footer=footer != "0")
    except ImportError as exc:
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


# ---- B2C web flow --------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, lang: str = "en"):
    lang = normalize_lang(lang)
    kinds = {kind: t_chrome(key, lang) for kind, key in _KIND_LABEL_KEYS.items()}
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
    if kind not in supported_kinds():
        raise HTTPException(status_code=400, detail=f"Unsupported report kind: {kind}")
    lang = normalize_lang(lang)
    kind_label = t_chrome(_KIND_LABEL_KEYS[kind], lang)

    if kind in _FILE_KINDS:
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail=f"'{kind_label}' requires a file upload")
        upload_id = uuid.uuid4().hex
        dest = os.path.join(_UPLOAD_DIR, f"{upload_id}_{os.path.basename(file.filename)}")
        with open(dest, "wb") as f:
            f.write(await file.read())
        subject = dest
    else:
        if not input_value.strip():
            raise HTTPException(status_code=400, detail=f"'{kind_label}' requires a URL or path")
        subject = input_value.strip()

    job_id = await _store.create_job(kind=kind, subject=subject, lang=lang)
    background_tasks.add_task(_run_job, job_id, kind, subject, model_name.strip() or None, lang)
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
