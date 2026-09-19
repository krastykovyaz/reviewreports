from typing import Any, Dict, List, Optional

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.schema import Finding, Pillar, Status

_TRACKER_PATTERNS = ("gtag", "googletagmanager", "google-analytics", "analytics", "gtm.js", "facebook", "fbevents", "hotjar", "segment", "mixpanel", "clarity.ms", "yandex", "metrika", "amplitude", "intercom")
_CDN_HEADERS = {"cf-ray": "Cloudflare", "x-vercel-id": "Vercel", "x-amz-cf-id": "Amazon CloudFront", "x-served-by": "Fastly/Varnish", "x-github-request-id": "GitHub Pages", "x-nf-request-id": "Netlify", "x-akamai-transformed": "Akamai"}

_M = {
    "check.frontend_stack": {"en": "Frontend stack", "ru": "Фронтенд-стек", "fr": "Pile front-end"},
    "check.hosting": {"en": "Hosting / server", "ru": "Хостинг / сервер", "fr": "Hébergement / serveur"},
    "check.tracking": {"en": "Analytics / tracking scripts", "ru": "Аналитика / трекинговые скрипты", "fr": "Scripts d'analyse / de suivi"},
    "check.third_party_domains": {"en": "Third-party domains", "ru": "Сторонние домены", "fr": "Domaines tiers"},
    "check.scripts": {"en": "Scripts", "ru": "Скрипты", "fr": "Scripts"},

    "frontend.none": {"en": "No known framework signature detected", "ru": "Известных признаков фреймворка не обнаружено", "fr": "Aucune signature de framework connue détectée"},
    "hosting.none": {"en": "No server or CDN headers exposed", "ru": "Заголовки сервера или CDN не раскрыты", "fr": "Aucun en-tête serveur ou CDN exposé"},
    "tracking.none": {"en": "None detected", "ru": "Не обнаружено", "fr": "Aucun détecté"},
    "tracking.found": {"en": "{n} tracking domain(s) — check consent requirements in your markets", "ru": "{n} трекинговых домен(ов) — проверьте требования к согласию (consent) на ваших рынках", "fr": "{n} domaine(s) de suivi — vérifiez les exigences de consentement sur vos marchés"},
    "tracking.fix": {"en": "Load trackers only after consent (GDPR/ePrivacy) and disclose them in the privacy policy.", "ru": "Загружайте трекеры только после согласия пользователя (GDPR/ePrivacy) и указывайте их в политике конфиденциальности.", "fr": "Chargez les traceurs uniquement après consentement (RGPD/ePrivacy) et mentionnez-les dans la politique de confidentialité."},
    "third_party.detail": {"en": "{n} external domain(s) contacted on load", "ru": "{n} внешних доменов, к которым обращается страница при загрузке", "fr": "{n} domaine(s) externe(s) contacté(s) au chargement"},
    "scripts.detail": {"en": "{n} external script(s)", "ru": "{n} внешних скриптов", "fr": "{n} script(s) externe(s)"},

    "summary": {"en": "{stack}{cdn}; {n} tracker(s)", "ru": "{stack}{cdn}; трекеров: {n}", "fr": "{stack}{cdn} ; {n} traceur(s)"},
    "summary.unknown_stack": {"en": "Unknown stack", "ru": "Стек не определён", "fr": "Pile inconnue"},
    "summary.cdn_suffix": {"en": "; {cdn}", "ru": "; {cdn}", "fr": " ; {cdn}"},
}

_t = make_translator(_M)


def collect_tech_stack(facts: Dict[str, Any], headers: Dict[str, str], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    findings: List[Finding] = []
    fw = facts.get("frameworks") or {}

    def _header(name: str) -> Optional[str]:
        for k, v in headers.items():
            if k.lower() == name:
                return v
        return None

    detected = [name for name, flag in (("Next.js", fw.get("next")), ("Nuxt", fw.get("nuxt")), ("React", fw.get("react")), ("Vue", fw.get("vue")), ("Angular", fw.get("angular")), ("Svelte", fw.get("svelte")), ("WordPress", fw.get("wordpress")), ("Bootstrap", fw.get("bootstrap"))) if flag]
    if fw.get("jquery"):
        detected.append(f"jQuery {fw['jquery']}" if fw["jquery"] != "yes" else "jQuery")
    if facts.get("generator"):
        detected.append(f"generator: {facts['generator']}")
    findings.append(Finding(check=_t("check.frontend_stack", lang), status=Status.NA, detail=", ".join(detected) if detected else _t("frontend.none", lang), evidence=", ".join(detected) or None))

    server = _header("server")
    powered = _header("x-powered-by")
    cdn = [label for h, label in _CDN_HEADERS.items() if _header(h)]
    infra = "; ".join(x for x in (f"server: {server}" if server else None, f"x-powered-by: {powered}" if powered else None, f"CDN: {', '.join(cdn)}" if cdn else None) if x)
    findings.append(Finding(check=_t("check.hosting", lang), status=Status.NA, detail=infra or _t("hosting.none", lang), evidence=infra or None))

    scripts = facts.get("scriptSrcs") or []
    third_party = facts.get("thirdPartyDomains") or []
    trackers = sorted({d for d in third_party if any(p in d for p in _TRACKER_PATTERNS)} | {s.split("/")[2] for s in scripts if s.startswith("http") and any(p in s for p in _TRACKER_PATTERNS)})
    findings.append(Finding(check=_t("check.tracking", lang), status=Status.OK if not trackers else Status.WARN, detail=_t("tracking.none", lang) if not trackers else _t("tracking.found", lang, n=len(trackers)), evidence=", ".join(trackers) or None, fix=None if not trackers else _t("tracking.fix", lang)))

    findings.append(Finding(check=_t("check.third_party_domains", lang), status=Status.NA, detail=_t("third_party.detail", lang, n=len(third_party)), evidence=", ".join(third_party[:10]) or None))
    findings.append(Finding(check=_t("check.scripts", lang), status=Status.NA, detail=_t("scripts.detail", lang, n=len(scripts)), evidence="; ".join(s.split('/')[-1][:40] for s in scripts[:8]) or None))

    stack_str = ", ".join(detected[:3]) if detected else _t("summary.unknown_stack", lang)
    cdn_str = _t("summary.cdn_suffix", lang, cdn=", ".join(cdn)) if cdn else ""
    summary = _t("summary", lang, stack=stack_str, cdn=cdn_str, n=len(trackers))
    # Informational pillar: no "correct" stack, so it does not contribute to the overall score.
    return Pillar(name=pillar_name("Tech Stack", lang), score=None, summary=summary, findings=findings)
