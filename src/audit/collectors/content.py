"""Content & Copy pillar: deterministic checks (volume, readability, language
consistency) always run; an LLM tone/clarity review runs when `model_name` is given.
"""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.logger import logger
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_COMMON_ENGLISH_WORDS = {"the", "and", "you", "for", "with", "your", "build", "sign", "will", "one", "our", "get", "from", "this"}
_WEIGHT_KEYS = {"check.content_volume": 2, "check.llm_review": 2}

_LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}

_M = {
    "check.content_volume": {"en": "Content volume", "ru": "Объём текста", "fr": "Volume de contenu"},
    "check.readability": {"en": "Readability", "ru": "Читабельность", "fr": "Lisibilité"},
    "check.language_consistency": {"en": "Language consistency", "ru": "Соответствие языка", "fr": "Cohérence linguistique"},
    "check.llm_review": {"en": "LLM tone/clarity review", "ru": "Оценка тона и ясности (ИИ)", "fr": "Analyse du ton/clarté (IA)"},

    "volume.very_low": {"en": "Only {n} words of visible text — little for search engines or visitors to work with", "ru": "Всего {n} слов видимого текста — мало материала для поисковиков и посетителей", "fr": "Seulement {n} mots de texte visible — peu de matière pour les moteurs de recherche ou les visiteurs"},
    "volume.very_low_fix": {"en": "Add explanatory copy: what the product does, who it is for, and why it is credible (a few hundred words).", "ru": "Добавьте пояснительный текст: что делает продукт, для кого он и почему ему можно доверять (пара сотен слов).", "fr": "Ajoutez du texte explicatif : ce que fait le produit, à qui il s'adresse et pourquoi il est crédible (quelques centaines de mots)."},
    "volume.low": {"en": "{n} words — thin content for a landing page", "ru": "{n} слов — маловато контента для посадочной страницы", "fr": "{n} mots — contenu léger pour une page d'atterrissage"},
    "volume.low_evidence": {"en": "{n} words, ~{min} min read", "ru": "{n} слов, ~{min} мин чтения", "fr": "{n} mots, ~{min} min de lecture"},
    "volume.low_fix": {"en": "Expand key sections (benefits, how it works, social proof) toward 300+ words.", "ru": "Расширьте ключевые разделы (преимущества, как это работает, отзывы) до 300+ слов.", "fr": "Étoffez les sections clés (avantages, fonctionnement, preuve sociale) vers 300+ mots."},
    "volume.ok": {"en": "{n} words (~{min} min read)", "ru": "{n} слов (~{min} мин чтения)", "fr": "{n} mots (~{min} min de lecture)"},

    "readability.na": {"en": "Not enough prose to score (needs ≥ 30 words in sentences)", "ru": "Недостаточно текста для оценки (нужно ≥ 30 слов в предложениях)", "fr": "Pas assez de texte pour évaluer (nécessite ≥ 30 mots en phrases)"},
    "readability.ok": {"en": "Flesch reading ease {score} — plain enough for a general audience", "ru": "Индекс удобочитаемости Флеша {score} — достаточно прост для широкой аудитории", "fr": "Score de lisibilité Flesch {score} — assez clair pour un public général"},
    "readability.bad": {"en": "Flesch reading ease {score} — dense/complex prose", "ru": "Индекс удобочитаемости Флеша {score} — плотный/сложный текст", "fr": "Score de lisibilité Flesch {score} — texte dense/complexe"},
    "readability.fix": {"en": "Shorten sentences and swap jargon for plain words; aim for a score of 50+.", "ru": "Сократите предложения и замените жаргон простыми словами; стремитесь к индексу 50+.", "fr": "Raccourcissez les phrases et remplacez le jargon par des mots simples ; visez un score de 50+."},

    "lang.mismatch": {"en": '<html lang="{value}"> but the visible copy reads as English', "ru": '<html lang="{value}">, но видимый текст написан на английском', "fr": '<html lang="{value}"> mais le texte visible semble être en anglais'},
    "lang.mismatch_fix": {"en": 'Set <html lang> to the language the copy is actually written in (e.g. lang="en").', "ru": 'Установите <html lang> в соответствии с реальным языком текста (например, lang="en").', "fr": "Définissez <html lang> sur la langue réellement utilisée dans le texte (par ex. lang=\"en\")."},
    "lang.consistent": {"en": 'lang="{value}" consistent with the sampled copy', "ru": 'lang="{value}" соответствует проверенному тексту', "fr": 'lang="{value}" cohérent avec le texte analysé'},
    "lang.no_lang_attr": {"en": "No lang attribute to compare against", "ru": "Нет атрибута lang для сравнения", "fr": "Aucun attribut lang à comparer"},

    "llm.na_no_model": {"en": "Not run — no model configured for this scan", "ru": "Не выполнено — для этого сканирования не настроена модель", "fr": "Non exécuté — aucun modèle configuré pour cette analyse"},
    "llm.na_failed": {"en": "LLM review failed or returned no result (model: {model})", "ru": "Оценка ИИ не удалась или не вернула результат (модель: {model})", "fr": "L'analyse par IA a échoué ou n'a renvoyé aucun résultat (modèle : {model})"},
    "llm.detail": {"en": 'Tone "{tone}"; clarity {clarity}/10, marketing effectiveness {effectiveness}/10', "ru": 'Тон «{tone}»; ясность {clarity}/10, маркетинговая эффективность {effectiveness}/10', "fr": 'Ton « {tone} » ; clarté {clarity}/10, efficacité marketing {effectiveness}/10'},

    "summary": {"en": "{n} words", "ru": "{n} слов", "fr": "{n} mots"},
    "summary.readability_suffix": {"en": ", readability {score}", "ru": ", читабельность {score}", "fr": ", lisibilité {score}"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


class ContentLLMReview(BaseModel):
    clarity: int = Field(ge=1, le=10, description="How clear and understandable the copy is")
    tone: str = Field(description="One short phrase describing the tone, e.g. 'professional', 'casual'")
    marketing_effectiveness: int = Field(ge=1, le=10, description="How persuasive the copy is at communicating value")
    issues: List[str] = Field(default_factory=list, description="Specific, concrete issues found in the copy")


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def flesch_reading_ease(text: str) -> Optional[float]:
    words = re.findall(r"[A-Za-z']+", text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(words) < 30 or not sentences:
        return None
    syllables = sum(_syllables(w) for w in words)
    return round(206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (syllables / len(words)), 1)


async def _llm_review_content(text: str, model_name: str, lang: str) -> Optional[ContentLLMReview]:
    if not text.strip():
        return None
    language_name = _LANGUAGE_NAMES.get(lang, "English")
    messages = [
        SystemMessage(content=f"You are an expert copywriter reviewing website copy for clarity, tone, and marketing effectiveness. Be concrete and critical. Respond in {language_name}: the 'tone' and 'issues' fields must be written in {language_name}."),
        HumanMessage(content=f"Review this website's visible text:\n\n{text[:4000]}"),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=ContentLLMReview)
    except Exception as exc:
        logger.warning(f"| ⚠️ Content LLM review failed for model {model_name}: {exc}")
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        logger.warning(f"| ⚠️ Content LLM review returned no structured result: {getattr(response, 'message', None)}")
        return None
    return response.extra.parsed_model


async def collect_content(facts: Dict[str, Any], model_name: Optional[str] = None, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    text = facts.get("bodyText", "") or ""
    word_count = facts.get("wordCount", 0)
    html_lang = facts.get("lang")
    findings: List[Finding] = []

    reading_min = max(1, round(word_count / 200)) if word_count else 0
    if word_count < 50:
        findings.append(Finding(check=_t("check.content_volume", lang), status=Status.WARN, detail=_t("volume.very_low", lang, n=word_count), evidence=_t("summary", lang, n=word_count), fix=_t("volume.very_low_fix", lang)))
    elif word_count < 150:
        findings.append(Finding(check=_t("check.content_volume", lang), status=Status.WARN, detail=_t("volume.low", lang, n=word_count), evidence=_t("volume.low_evidence", lang, n=word_count, min=reading_min), fix=_t("volume.low_fix", lang)))
    else:
        findings.append(Finding(check=_t("check.content_volume", lang), status=Status.OK, detail=_t("volume.ok", lang, n=word_count, min=reading_min), evidence=_t("summary", lang, n=word_count)))

    fre = flesch_reading_ease(text)
    if fre is None:
        findings.append(Finding(check=_t("check.readability", lang), status=Status.NA, detail=_t("readability.na", lang)))
    elif fre >= 50:
        findings.append(Finding(check=_t("check.readability", lang), status=Status.OK, detail=_t("readability.ok", lang, score=fre), evidence=str(fre)))
    else:
        findings.append(Finding(check=_t("check.readability", lang), status=Status.WARN, detail=_t("readability.bad", lang, score=fre), evidence=str(fre), fix=_t("readability.fix", lang)))

    words = {w.strip(".,!?;:").lower() for w in text.split()}
    looks_english = len(words & _COMMON_ENGLISH_WORDS) >= 2
    if html_lang and looks_english and not html_lang.lower().startswith("en"):
        findings.append(Finding(check=_t("check.language_consistency", lang), status=Status.WARN, detail=_t("lang.mismatch", lang, value=html_lang), evidence=f'lang="{html_lang}"', fix=_t("lang.mismatch_fix", lang)))
    else:
        detail = _t("lang.consistent", lang, value=html_lang) if html_lang else _t("lang.no_lang_attr", lang)
        findings.append(Finding(check=_t("check.language_consistency", lang), status=Status.OK, detail=detail, evidence=f'lang="{html_lang}"' if html_lang else None))

    if model_name:
        review = await _llm_review_content(text, model_name, lang)
        if review is not None:
            llm_score = (review.clarity + review.marketing_effectiveness) / 2
            status = Status.OK if llm_score >= 6 else Status.WARN if llm_score >= 4 else Status.BAD
            detail = _t("llm.detail", lang, tone=review.tone, clarity=review.clarity, effectiveness=review.marketing_effectiveness)
            findings.append(Finding(check=_t("check.llm_review", lang), status=status, detail=detail, evidence=f"model={model_name}", fix="; ".join(review.issues) if review.issues else None))
        else:
            findings.append(Finding(check=_t("check.llm_review", lang), status=Status.NA, detail=_t("llm.na_failed", lang, model=model_name)))
    else:
        findings.append(Finding(check=_t("check.llm_review", lang), status=Status.NA, detail=_t("llm.na_no_model", lang)))

    score = score_findings(findings, _weights_for(lang))
    summary = _t("summary", lang, n=word_count)
    if fre is not None:
        summary += _t("summary.readability_suffix", lang, score=fre)
    return Pillar(name=pillar_name("Content & Copy", lang), score=score, summary=summary, findings=findings)
