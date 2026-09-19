"""Document review: one file in (resume, presentation, or book/manuscript),
one structured Report out.

Deliberately one generic implementation parametrized by `flavor` rather than
three bespoke pipelines: all three inputs reduce to the same shape — convert
whatever file format to text (via markitdown, which already handles pdf,
docx, pptx, txt, md, ...), run a couple of deterministic checks, then an
LLM critique with a flavor-specific rubric prompt.
"""

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.i18n import flavor_label, normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.logger import logger
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager
from src.report.helpers import build_recommendations, verdict_for
from src.report.schema import Finding, Pillar, Report, ReportMeta, Status

_MAX_REVIEW_CHARS = 12000
_LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}
_FLAVOR_PILLAR_KEYS = {"resume": "Resume Review", "presentation": "Presentation Review", "book": "Book/Manuscript Review"}

_FLAVOR_RUBRICS: Dict[str, str] = {
    "resume": (
        "You are an experienced technical recruiter reviewing a resume. Judge clarity, impact "
        "(quantified achievements vs. vague duties), structure (contact info, experience, education, "
        "skills all present and well-ordered), and ATS-friendliness (parseable formatting, no missing "
        "sections). Be concrete about what's missing or weak."
    ),
    "presentation": (
        "You are a presentation coach reviewing a deck's extracted content. Judge structure and flow "
        "(clear narrative arc), clarity of each slide's message, and whether the content supports a "
        "compelling presentation (not just extracted text quality)."
    ),
    "book": (
        "You are a literary editor reviewing this manuscript or book text. Judge structure, pacing, "
        "clarity of voice, and overall strengths/weaknesses as you would in an editorial review."
    ),
}

_M = {
    "check.file_readable": {"en": "File exists and was readable", "ru": "Файл существует и был прочитан", "fr": "Le fichier existe et a pu être lu"},
    "check.content_volume": {"en": "Content volume", "ru": "Объём текста", "fr": "Volume de contenu"},
    "check.llm_review": {"en": "LLM-based review", "ru": "Оценка ИИ", "fr": "Analyse par IA"},

    "volume.low": {"en": "Only {n} characters extracted — file may be mostly images or empty", "ru": "Извлечено всего {n} символов — файл может состоять в основном из изображений или быть пустым", "fr": "Seulement {n} caractères extraits — le fichier est peut-être surtout composé d'images ou vide"},
    "volume.ok": {"en": "{n} characters extracted", "ru": "Извлечено {n} символов", "fr": "{n} caractères extraits"},
    "doc_summary": {"en": "{n} characters extracted from {filename}", "ru": "Извлечено {n} символов из {filename}", "fr": "{n} caractères extraits de {filename}"},

    "review.na_no_model": {"en": "Not evaluated — requires a configured LLM", "ru": "Не оценено — требуется настроенная модель", "fr": "Non évalué — nécessite un modèle IA configuré"},
    "review.na_no_model_detail": {"en": "No model_name configured for this scan", "ru": "Для этого сканирования не настроена модель (model_name)", "fr": "Aucun model_name configuré pour cette analyse"},
    "review.na_no_text": {"en": "No extractable text", "ru": "Нет извлекаемого текста", "fr": "Aucun texte extractible"},
    "review.na_no_text_detail": {"en": "No text could be extracted from the file", "ru": "Из файла не удалось извлечь текст", "fr": "Aucun texte n'a pu être extrait du fichier"},
    "review.na_failed": {"en": "LLM review failed or returned no result", "ru": "Оценка ИИ не удалась или не вернула результат", "fr": "L'analyse par IA a échoué ou n'a renvoyé aucun résultat"},
    "review.na_failed_detail": {"en": "LLM review failed (model: {model})", "ru": "Оценка ИИ не удалась (модель: {model})", "fr": "L'analyse par IA a échoué (modèle : {model})"},
    "review.strengths_suffix": {"en": " Strengths: {items}", "ru": " Сильные стороны: {items}", "fr": " Points forts : {items}"},
    "review.weaknesses_suffix": {"en": " Weaknesses: {items}", "ru": " Слабые стороны: {items}", "fr": " Points faibles : {items}"},
    "review.summary": {"en": "Rating {n}/10 — {summary}", "ru": "Оценка {n}/10 — {summary}", "fr": "Note {n}/10 — {summary}"},

    "methodology": {"en": "Text extracted via markitdown, then reviewed against a {flavor}-specific rubric.", "ru": "Текст извлечён с помощью markitdown, затем оценён по критериям для типа «{flavor}».", "fr": "Texte extrait via markitdown, puis évalué selon une grille spécifique au type « {flavor} »."},
    "limitations": {"en": "Only the first ~{n} characters of extracted text were reviewed by the LLM.", "ru": "ИИ проверил только первые ~{n} символов извлечённого текста.", "fr": "Seuls les ~{n} premiers caractères du texte extrait ont été analysés par l'IA."},
    "limitations.no_model_suffix": {"en": " No model_name was configured, so the {flavor} Review pillar was not evaluated.", "ru": " Модель (model_name) не была настроена, поэтому раздел «{flavor}: отзыв» не оценивался.", "fr": " Aucun model_name n'a été configuré, le pilier {flavor} n'a donc pas été évalué."},
}
_t = make_translator(_M)


class DocumentLLMReview(BaseModel):
    summary: str = Field(description="One or two sentence overall assessment")
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    rating: int = Field(ge=1, le=10, description="Overall rating for this document, given its flavor's rubric")


def _extract_text(file_path: str) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert(file_path)
    return result.markdown or result.text_content or ""


def _collect_document_stats(file_path: str, text: str, lang: str = "en") -> Pillar:
    findings = [Finding(check=_t("check.file_readable", lang), status=Status.OK, detail=os.path.basename(file_path))]
    length = len(text)
    if length < 200:
        findings.append(Finding(check=_t("check.content_volume", lang), status=Status.WARN, detail=_t("volume.low", lang, n=length)))
        score = 4.0
    else:
        findings.append(Finding(check=_t("check.content_volume", lang), status=Status.OK, detail=_t("volume.ok", lang, n=length)))
        score = 8.0

    summary = _t("doc_summary", lang, n=length, filename=os.path.basename(file_path))
    return Pillar(name=pillar_name("Document Stats", lang), score=score, summary=summary, findings=findings)


async def _llm_review_document(text: str, flavor: str, model_name: str, lang: str) -> Optional[DocumentLLMReview]:
    rubric = _FLAVOR_RUBRICS[flavor]
    language_name = _LANGUAGE_NAMES.get(lang, "English")
    messages = [
        SystemMessage(content=f"{rubric} Be concrete and critical. Respond in {language_name}: the 'summary', 'strengths', and 'weaknesses' fields must be written in {language_name}."),
        HumanMessage(content=f"Review this document:\n\n{text[:_MAX_REVIEW_CHARS]}"),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=DocumentLLMReview)
    except Exception as exc:
        logger.warning(f"| ⚠️ Document LLM review failed for model {model_name}: {exc}")
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        logger.warning(f"| ⚠️ Document LLM review returned no structured result: {getattr(response, 'message', None)}")
        return None
    return response.extra.parsed_model


async def collect_llm_review(text: str, flavor: str, model_name: Optional[str], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    name = pillar_name(_FLAVOR_PILLAR_KEYS[flavor], lang)
    check = _t("check.llm_review", lang)

    if not model_name:
        return Pillar(name=name, score=None, summary=_t("review.na_no_model", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("review.na_no_model_detail", lang))])
    if not text.strip():
        return Pillar(name=name, score=None, summary=_t("review.na_no_text", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("review.na_no_text_detail", lang))])

    review = await _llm_review_document(text, flavor, model_name, lang)
    if review is None:
        return Pillar(name=name, score=None, summary=_t("review.na_failed", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("review.na_failed_detail", lang, model=model_name))])

    status = Status.OK if review.rating >= 7 else Status.WARN if review.rating >= 4 else Status.BAD
    detail = review.summary
    if review.strengths:
        detail += _t("review.strengths_suffix", lang, items="; ".join(review.strengths))
    if review.weaknesses:
        detail += _t("review.weaknesses_suffix", lang, items="; ".join(review.weaknesses))

    summary = _t("review.summary", lang, n=review.rating, summary=review.summary)
    return Pillar(name=name, score=float(review.rating), summary=summary, findings=[Finding(check=check, status=status, detail=detail)])


async def run_document_review(input: str, flavor: str = "resume", model_name: Optional[str] = None, lang: str = "en") -> Report:
    """Review a document file. `flavor` selects the rubric: "resume", "presentation", or "book"."""
    lang = normalize_lang(lang)
    if flavor not in _FLAVOR_RUBRICS:
        raise ValueError(f"Unknown document review flavor: {flavor!r}. Must be one of {list(_FLAVOR_RUBRICS)}.")
    if not os.path.isfile(input):
        raise ValueError(f"Not a file: {input}")

    text = _extract_text(input)
    pillars = [_collect_document_stats(input, text, lang=lang), await collect_llm_review(text, flavor, model_name, lang=lang)]

    label = flavor_label(flavor, lang)
    limitations = _t("limitations", lang, n=_MAX_REVIEW_CHARS)
    if not model_name:
        limitations += _t("limitations.no_model_suffix", lang, flavor=label)

    report = Report(
        meta=ReportMeta(kind=f"{flavor}_review", subject=input, lang=lang),
        pillars=pillars,
        recommendations=build_recommendations(pillars, lang=lang),
        methodology=_t("methodology", lang, flavor=label),
        limitations=limitations,
    ).finalize()
    report.verdict = verdict_for(report.overall_score, lang=lang)
    return report
