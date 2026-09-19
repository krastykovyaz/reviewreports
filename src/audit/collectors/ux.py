"""Design & UX pillar: entirely LLM-judgment based (a screenshot critique),
so this pillar is N/A whenever no vision-capable `model_name` is configured.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.logger import logger
from src.message.types import ContentPartImage, ContentPartText, HumanMessage, ImageURL, SystemMessage
from src.model import model_manager
from src.report.schema import Finding, Pillar, Status

_LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}

_UX_PROMPT = (
    "Review this screenshot of a website's homepage for visual design and UX quality. "
    "Judge visual hierarchy, layout balance, spacing, and overall clarity of the interface."
)

_M = {
    "check.screenshot_review": {"en": "Screenshot-based design review", "ru": "Оценка дизайна по скриншоту", "fr": "Analyse du design à partir d'une capture d'écran"},
    "check.screenshot_capture": {"en": "Screenshot capture", "ru": "Захват скриншота", "fr": "Capture d'écran"},
    "na_no_model": {"en": "No model_name configured for this scan", "ru": "Для этого сканирования не настроена модель (model_name)", "fr": "Aucun model_name configuré pour cette analyse"},
    "na_failed": {"en": "LLM review failed (model: {model})", "ru": "Оценка ИИ не удалась (модель: {model})", "fr": "L'analyse par IA a échoué (modèle : {model})"},
    "detail": {"en": "Visual clarity {clarity}/10, layout quality {layout}/10", "ru": "Визуальная ясность {clarity}/10, качество компоновки {layout}/10", "fr": "Clarté visuelle {clarity}/10, qualité de la mise en page {layout}/10"},
    "summary_not_evaluated": {"en": "Not evaluated — requires a configured vision-capable LLM", "ru": "Не оценено — требуется модель с поддержкой изображений", "fr": "Non évalué — nécessite un modèle IA compatible avec la vision"},
    "summary_capture_failed": {"en": "Screenshot capture failed", "ru": "Не удалось сделать скриншот", "fr": "Échec de la capture d'écran"},
    "summary_llm_failed": {"en": "LLM review failed or returned no result", "ru": "Оценка ИИ не удалась или не вернула результат", "fr": "L'analyse par IA a échoué ou n'a renvoyé aucun résultat"},
    "summary": {"en": "Visual clarity {clarity}/10, layout {layout}/10", "ru": "Визуальная ясность {clarity}/10, компоновка {layout}/10", "fr": "Clarté visuelle {clarity}/10, mise en page {layout}/10"},
}

_t = make_translator(_M)


class UXLLMReview(BaseModel):
    visual_clarity: int = Field(ge=1, le=10, description="How clear the visual hierarchy and layout are")
    layout_quality: int = Field(ge=1, le=10, description="Overall layout/spacing/visual polish")
    issues: List[str] = Field(default_factory=list, description="Specific, concrete UX/design issues found")


async def _llm_review_screenshot(screenshot_b64: str, model_name: str, lang: str) -> Optional[UXLLMReview]:
    language_name = _LANGUAGE_NAMES.get(lang, "English")
    messages = [
        SystemMessage(content=f"You are an expert UX/UI designer reviewing a website screenshot. Be concrete and critical. Respond in {language_name}: the 'issues' field must be written in {language_name}."),
        HumanMessage(
            content=[
                ContentPartText(text=_UX_PROMPT),
                ContentPartImage(image_url=ImageURL(url=f"data:image/jpeg;base64,{screenshot_b64}", media_type="image/jpeg", detail="high")),
            ]
        ),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=UXLLMReview)
    except Exception as exc:
        logger.warning(f"| ⚠️ UX LLM review failed for model {model_name}: {exc}")
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        logger.warning(f"| ⚠️ UX LLM review returned no structured result: {getattr(response, 'message', None)}")
        return None
    return response.extra.parsed_model


async def collect_ux(page, model_name: Optional[str] = None, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    name = pillar_name("Design & UX", lang)

    if not model_name:
        return Pillar(name=name, score=None, summary=_t("summary_not_evaluated", lang), findings=[Finding(check=_t("check.screenshot_review", lang), status=Status.NA, detail=_t("na_no_model", lang))])

    try:
        screenshot_b64 = await page.screenshot(format="jpeg")
    except Exception as exc:
        return Pillar(name=name, score=None, summary=_t("summary_capture_failed", lang), findings=[Finding(check=_t("check.screenshot_capture", lang), status=Status.NA, detail=str(exc))])

    review = await _llm_review_screenshot(screenshot_b64, model_name, lang)
    if review is None:
        return Pillar(name=name, score=None, summary=_t("summary_llm_failed", lang), findings=[Finding(check=_t("check.screenshot_review", lang), status=Status.NA, detail=_t("na_failed", lang, model=model_name))])

    score = round((review.visual_clarity + review.layout_quality) / 2, 1)
    status = Status.OK if score >= 7 else Status.WARN if score >= 4 else Status.BAD
    detail = _t("detail", lang, clarity=review.visual_clarity, layout=review.layout_quality)
    if review.issues:
        detail += "; " + "; ".join(review.issues)

    return Pillar(name=name, score=score, summary=_t("summary", lang, clarity=review.visual_clarity, layout=review.layout_quality), findings=[Finding(check=_t("check.screenshot_review", lang), status=status, detail=detail)])
