"""Website audit orchestrator: one URL in, one structured Report out.

Runs as a direct async pipeline rather than through the agent think/act loop —
a URL scan is deterministic and should be fast and reproducible. The page is
queried once for a facts snapshot; every collector is a pure function over it
(plus the raw HTTP responses), and only the LLM judgment pillars need a model.
"""

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from src.audit.browser_session import audit_page
from src.audit.collectors.accessibility import collect_accessibility
from src.audit.collectors.content import collect_content
from src.audit.collectors.links import collect_broken_links
from src.audit.collectors.performance import collect_performance
from src.audit.collectors.privacy import collect_privacy
from src.audit.collectors.security_headers import collect_security_headers
from src.audit.collectors.seo import collect_seo
from src.audit.collectors.tech_stack import collect_tech_stack
from src.audit.collectors.ux import collect_ux
from src.audit.http_client import FetchResult, fetch, try_fetch
from src.audit.page_facts import gather_page_facts
from src.i18n import normalize_lang
from src.i18n.core import make_translator
from src.report.helpers import build_recommendations, verdict_for
from src.report.schema import Pillar, Report, ReportMeta

_M = {
    "methodology": {
        "en": "Homepage rendered in a headless Chromium session (DOM, Navigation/Resource/Paint Timing APIs, "
        "screenshot) plus direct HTTP requests for response headers, the plain-HTTP redirect, robots.txt "
        "and sitemap.xml; up to 25 outbound links are HEAD/GET-checked. Scores are the weighted share of "
        "passed checks per pillar (OK = 1, warning = 0.5, missing = 0); N/A checks and informational pillars "
        "are excluded from the overall score. Thresholds follow common industry guidance (Google web vitals "
        "for timing, 30–60 / 70–160 character title/description ranges, OWASP secure headers).",
        "ru": "Главная страница отрендерена в headless-сессии Chromium (DOM, Navigation/Resource/Paint Timing API, "
        "скриншот), плюс прямые HTTP-запросы для заголовков ответа, редиректа с обычного HTTP, robots.txt "
        "и sitemap.xml; до 25 исходящих ссылок проверяются HEAD/GET-запросами. Оценки — это взвешенная доля "
        "пройденных проверок по разделу (ОК = 1, предупреждение = 0.5, критично = 0); проверки Н/Д и "
        "информационные разделы не учитываются в итоговой оценке. Пороговые значения основаны на общепринятых "
        "отраслевых рекомендациях (Google Web Vitals для скорости, диапазоны 30–60 / 70–160 символов для "
        "title/description, защитные заголовки OWASP).",
        "fr": "La page d'accueil est rendue dans une session Chromium headless (DOM, API Navigation/Resource/Paint "
        "Timing, capture d'écran), avec des requêtes HTTP directes pour les en-têtes de réponse, la redirection "
        "HTTP simple, robots.txt et sitemap.xml ; jusqu'à 25 liens sortants sont vérifiés par HEAD/GET. Les scores "
        "sont la part pondérée des contrôles réussis par pilier (OK = 1, avertissement = 0.5, manquant = 0) ; les "
        "contrôles N/A et les piliers informatifs sont exclus du score global. Les seuils suivent les recommandations "
        "courantes du secteur (Web Vitals de Google pour la vitesse, plages de 30–60 / 70–160 caractères pour le "
        "titre/la description, en-têtes de sécurité OWASP).",
    },
    "limitations.no_llm": {
        "en": "Single page (the homepage) from one location on an unthrottled connection, so timings are indicative "
        "rather than field data. Colour contrast, keyboard operability and cross-origin resource sizes are not "
        "measured. Authenticated pages and deeper crawls are out of scope. No model was configured, so the LLM "
        "tone/clarity review and the Design & UX pillar were skipped.",
        "ru": "Проверена только одна страница (главная) из одной точки на нелимитированном соединении, поэтому "
        "показатели скорости ориентировочные, а не полевые данные. Контрастность цвета, доступность с клавиатуры "
        "и размер кросс-доменных ресурсов не измеряются. Страницы за авторизацией и более глубокий обход сайта "
        "не входят в рамки проверки. Модель не была настроена, поэтому оценка тона/ясности текста и раздел "
        "«Дизайн и UX» были пропущены.",
        "fr": "Une seule page (l'accueil) depuis un seul emplacement sur une connexion non limitée : les temps sont "
        "donc indicatifs et non des données de terrain. Le contraste des couleurs, l'accessibilité au clavier et "
        "la taille des ressources cross-origin ne sont pas mesurés. Les pages authentifiées et une exploration plus "
        "poussée du site sont hors périmètre. Aucun modèle n'a été configuré, l'analyse du ton/de la clarté et le "
        "pilier Design & UX ont donc été ignorés.",
    },
    "limitations.with_llm": {
        "en": "Single page (the homepage) from one location on an unthrottled connection, so timings are indicative "
        "rather than field data. Colour contrast, keyboard operability and cross-origin resource sizes are not "
        "measured. Authenticated pages and deeper crawls are out of scope. Content/Copy and Design & UX include "
        "an LLM judgment pass.",
        "ru": "Проверена только одна страница (главная) из одной точки на нелимитированном соединении, поэтому "
        "показатели скорости ориентировочные, а не полевые данные. Контрастность цвета, доступность с клавиатуры "
        "и размер кросс-доменных ресурсов не измеряются. Страницы за авторизацией и более глубокий обход сайта "
        "не входят в рамки проверки. Разделы «Контент и тексты» и «Дизайн и UX» включают оценку с помощью ИИ.",
        "fr": "Une seule page (l'accueil) depuis un seul emplacement sur une connexion non limitée : les temps sont "
        "donc indicatifs et non des données de terrain. Le contraste des couleurs, l'accessibilité au clavier et "
        "la taille des ressources cross-origin ne sont pas mesurés. Les pages authentifiées et une exploration plus "
        "poussée du site sont hors périmètre. Les piliers Contenu et rédaction et Design & UX incluent une "
        "évaluation par IA.",
    },
}
_t = make_translator(_M)


def normalize_url(url: str) -> str:
    if not urlparse(url).scheme:
        url = f"https://{url}"
    return url


async def _http_variant(url: str) -> Optional[FetchResult]:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    return await try_fetch(url.replace("https://", "http://", 1), timeout=10.0)


async def run_website_audit(url: str, model_name: Optional[str] = None, lang: str = "en") -> Report:
    """Run the full multi-pillar website audit and return a structured Report.

    Args:
        url: The site to audit.
        model_name: Optional LLM for the judgment pillars (Content tone/clarity,
            Design & UX). Both degrade to N/A when omitted or when the call fails.
        lang: Output language for all report text: "en", "ru", or "fr".
    """
    lang = normalize_lang(lang)
    target_url = normalize_url(url)
    homepage = await fetch(target_url)
    http_redirect, robots, sitemap = await asyncio.gather(
        _http_variant(homepage.url),
        try_fetch(urljoin(homepage.url, "/robots.txt")),
        try_fetch(urljoin(homepage.url, "/sitemap.xml")),
    )

    async with audit_page(homepage.url) as page:
        facts: Dict[str, Any] = await gather_page_facts(page)
        ux = await collect_ux(page, model_name=model_name, lang=lang)

    pillars: List[Pillar] = [
        collect_security_headers(homepage, http_redirect, facts, lang=lang),
        collect_seo(facts, robots, sitemap, lang=lang),
        collect_accessibility(facts, lang=lang),
        collect_performance(facts, lang=lang),
        await collect_content(facts, model_name=model_name, lang=lang),
        collect_privacy(facts, lang=lang),
        await collect_broken_links(facts, lang=lang),
        collect_tech_stack(facts, homepage.headers, lang=lang),
        ux,
    ]

    limitations = _t("limitations.with_llm", lang) if model_name else _t("limitations.no_llm", lang)
    report = Report(
        meta=ReportMeta(kind="website_audit", subject=target_url, lang=lang),
        pillars=pillars,
        recommendations=build_recommendations(pillars, lang=lang),
        methodology=_t("methodology", lang),
        limitations=limitations,
    ).finalize()
    report.verdict = verdict_for(report.overall_score, lang=lang)
    return report
