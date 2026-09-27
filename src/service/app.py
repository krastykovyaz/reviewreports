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

import html
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src.cv.extract import edit_cv_profile, extract_cv_profile
from src.cv.render import TEMPLATES as CV_TEMPLATES, render_cv_html, render_cv_markdown, render_cv_pdf
from src.cv.schema import Contact, CVProfile, Education, Experience
from src.i18n import SUPPORTED_LANGUAGES, normalize_lang, t_chrome
from src.model import model_manager
from src.report.render import RENDERERS
from src.report.schema import Report
from src.review.generate import generate_report, supported_kinds
from src.service.cv_store import CVStore
from src.service.db import JobStatus, JobStore
from src.utils import assemble_project_path

_MEDIA_TYPES = {"markdown": "text/markdown", "html": "text/html", "latex": "text/x-tex", "pdf": "application/pdf"}
_EXTENSION_TO_FORMAT = {"md": "markdown", "html": "html", "tex": "latex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_CV_EXTENSION_TO_FORMAT = {"html": "html", "pdf": "pdf", "md": "markdown"}
_DEFAULT_CV_EXTRACTION_MODEL = "deepseek/deepseek-chat"

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
_templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _store.init()
    await _cv_store.init()
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    # Without this, every LLM-dependent pillar/feature across every kind (and
    # CV extraction) fails at call time with "Model X not found. Available
    # models: []" - model_manager's registry is only populated by this call,
    # and nothing else in the service was triggering it.
    await model_manager.initialize()
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
    if not _kind_allowed(request.kind):
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


# ---- CV builder ---------------------------------------------------------------


class CreateCVRequest(CVProfile):
    template: str = "modern"


class CVResponse(BaseModel):
    id: str
    template: str
    profile: dict


@app.post("/cv", response_model=CVResponse)
async def create_cv(request: CreateCVRequest):
    if request.template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{request.template}'. Must be one of {CV_TEMPLATES}.")

    profile = CVProfile.model_validate(request.model_dump(exclude={"template"}))
    cv_id = await _cv_store.create(profile, request.template)
    return CVResponse(id=cv_id, template=request.template, profile=profile.model_dump())


class CreateCVFromTextRequest(BaseModel):
    text: str
    model_name: Optional[str] = None
    template: str = "modern"


@app.post("/cv/from-text", response_model=CVResponse)
async def create_cv_from_text(request: CreateCVFromTextRequest):
    """JSON counterpart of the /cv-builder/from-text form flow, for clients
    that render the result themselves (tsech's "Create CV" mode): free text
    in, an extracted-and-stored CV out, same shape as POST /cv."""
    if request.template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{request.template}'. Must be one of {CV_TEMPLATES}.")
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is empty")

    profile = await extract_cv_profile(text, request.model_name or _DEFAULT_CV_EXTRACTION_MODEL)
    if profile is None:
        raise HTTPException(status_code=422, detail="Could not extract a CV from that text")

    cv_id = await _cv_store.create(profile, request.template)
    return CVResponse(id=cv_id, template=request.template, profile=profile.model_dump())


class EditCVRequest(BaseModel):
    instructions: str
    model_name: Optional[str] = None


@app.post("/cv/{cv_id}/edit", response_model=CVResponse)
async def edit_cv(cv_id: str, request: EditCVRequest):
    """Revises a stored CV from a plain-language change request - the CV
    equivalent of "describe changes" on a generated app or document. Keeps
    the same id/template/share link; only the stored profile changes."""
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")

    instructions = request.instructions.strip()
    if not instructions:
        raise HTTPException(status_code=422, detail="instructions is empty")

    current = await _cv_store.get_profile(cv_id)
    revised = await edit_cv_profile(current, instructions, request.model_name or _DEFAULT_CV_EXTRACTION_MODEL)
    if revised is None:
        raise HTTPException(status_code=422, detail="Could not apply that change")

    await _cv_store.update_profile(cv_id, revised)
    return CVResponse(id=cv_id, template=row["template"], profile=revised.model_dump())


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
    chosen_template = template or row["template"]
    if output_format != "markdown" and chosen_template not in CV_TEMPLATES:
        raise HTTPException(status_code=400, detail=f"Unknown template '{chosen_template}'. Must be one of {CV_TEMPLATES}.")

    if output_format == "html":
        rendered = render_cv_html(profile, chosen_template)
        # Also give a direct link to one of these documents (people share
        # them as-is, not just the /cv-builder/{id} comparison page) a real
        # preview card instead of a blank one.
        title, description = _build_cv_share_meta(profile, "en")
        canonical_url = f"https://tsech.online/audit/cv/{cv_id}.html?template={chosen_template}"
        rendered = _inject_og_meta(rendered, title=title, description=description, url=canonical_url)
    elif output_format == "pdf":
        try:
            rendered = render_cv_pdf(profile, chosen_template)
        except ImportError as exc:
            raise HTTPException(status_code=503, detail=f"PDF rendering is unavailable on this server: {exc}")
    else:
        rendered = render_cv_markdown(profile)

    if output_format == "pdf":
        return Response(content=rendered, media_type="application/pdf")
    media_type = "text/html" if output_format == "html" else "text/markdown"
    return PlainTextResponse(content=rendered, media_type=media_type)


@app.get("/cv/{cv_id}", response_model=CVResponse)
async def get_cv(cv_id: str):
    row = await _cv_store.get(cv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="CV not found")
    return CVResponse(id=row["id"], template=row["template"], profile=json.loads(row["profile_json"]))


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


def _inject_og_meta(rendered_html: str, *, title: str, description: str, url: str) -> str:
    """Splices og:/twitter: meta tags into an already-rendered HTML document's
    <head> — used for the CV templates' own standalone documents (each is a
    full page rendered by src/cv/render.py, not a Jinja template this service
    controls directly), so a link to one of them also gets a real preview
    card in Telegram/WhatsApp/Facebook instead of a blank one. Image is the
    static tsech logo, matching /view/{id}'s existing pattern, rather than a
    per-CV screenshot."""
    tags = (
        '<meta property="og:type" content="website">'
        '<meta property="og:site_name" content="tsech">'
        f'<meta property="og:title" content="{html.escape(title)}">'
        f'<meta property="og:description" content="{html.escape(description)}">'
        '<meta property="og:image" content="https://tsech.online/logo.png">'
        f'<meta property="og:url" content="{html.escape(url)}">'
        '<meta name="twitter:card" content="summary">'
        f'<meta name="twitter:title" content="{html.escape(title)}">'
        f'<meta name="twitter:description" content="{html.escape(description)}">'
        '<meta name="twitter:image" content="https://tsech.online/logo.png">'
    )
    injected, count = _HEAD_OPEN_RE.subn(f"<head>{tags}", rendered_html, count=1)
    return injected if count else rendered_html


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
async def cv_builder_from_text(request: Request, text: str = Form(...), model_name: str = Form(""), lang: str = Form("en")):
    lang = normalize_lang(lang)
    profile = await extract_cv_profile(text.strip(), model_name.strip() or _DEFAULT_CV_EXTRACTION_MODEL)
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

    cv_id = await _cv_store.create(profile, template=CV_TEMPLATES[0])
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
            "meta_title": meta_title, "meta_description": meta_description,
            "t": lambda key, **kw: t_chrome(key, lang, **kw),
        },
    )
