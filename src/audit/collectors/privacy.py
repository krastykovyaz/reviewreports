from typing import Any, Dict, List

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_WEIGHT_KEYS = {"check.privacy_policy": 3, "check.cookie_consent": 2}

_M = {
    "check.privacy_policy": {"en": "Privacy policy", "ru": "Политика конфиденциальности", "fr": "Politique de confidentialité"},
    "check.terms": {"en": "Terms of service", "ru": "Пользовательское соглашение", "fr": "Conditions d'utilisation"},
    "check.cookie_consent": {"en": "Cookie consent", "ru": "Согласие на cookie", "fr": "Consentement aux cookies"},
    "check.cookies_on_load": {"en": "Cookies on load", "ru": "Cookie при загрузке", "fr": "Cookies au chargement"},
    "check.third_party_flows": {"en": "Third-party data flows", "ru": "Передача данных третьим лицам", "fr": "Flux de données vers des tiers"},

    "privacy.found": {"en": "Link to a privacy policy found", "ru": "Найдена ссылка на политику конфиденциальности", "fr": "Lien vers une politique de confidentialité trouvé"},
    "privacy.missing_with_data": {"en": "No privacy policy link, although the page has forms / a login — this is a legal requirement in most jurisdictions once personal data is collected", "ru": "Нет ссылки на политику конфиденциальности, хотя на странице есть формы / вход — это юридическое требование в большинстве юрисдикций при сборе персональных данных", "fr": "Aucun lien vers une politique de confidentialité alors que la page contient des formulaires / une connexion — c'est une obligation légale dans la plupart des juridictions dès lors que des données personnelles sont collectées"},
    "privacy.missing": {"en": "No privacy policy link found", "ru": "Ссылка на политику конфиденциальности не найдена", "fr": "Aucun lien vers une politique de confidentialité trouvé"},
    "privacy.evidence": {"en": "forms={forms}, login={login}", "ru": "форм={forms}, вход={login}", "fr": "formulaires={forms}, connexion={login}"},
    "privacy.yes": {"en": "yes", "ru": "да", "fr": "oui"},
    "privacy.no": {"en": "no", "ru": "нет", "fr": "non"},
    "privacy.fix": {"en": "Publish a privacy policy (what is collected, why, retention, contact) and link it from the footer of every page.", "ru": "Опубликуйте политику конфиденциальности (что собирается, зачем, срок хранения, контакты) и разместите ссылку в подвале каждой страницы.", "fr": "Publiez une politique de confidentialité (données collectées, finalité, durée de conservation, contact) et liez-la depuis le pied de page de chaque page."},

    "terms.found": {"en": "Link to terms found", "ru": "Найдена ссылка на пользовательское соглашение", "fr": "Lien vers les conditions trouvé"},
    "terms.missing": {"en": "No terms/conditions link found", "ru": "Ссылка на условия использования не найдена", "fr": "Aucun lien vers les conditions générales trouvé"},
    "terms.fix": {"en": "Add terms of service and link them next to the privacy policy.", "ru": "Добавьте пользовательское соглашение и разместите ссылку рядом с политикой конфиденциальности.", "fr": "Ajoutez des conditions d'utilisation et liez-les à côté de la politique de confidentialité."},

    "consent.not_needed": {"en": "No cookies set and no third-party requests on load — no consent banner needed", "ru": "Cookie не устанавливаются, сторонние запросы отсутствуют — баннер согласия не нужен", "fr": "Aucun cookie défini et aucune requête tierce au chargement — bandeau de consentement non nécessaire"},
    "consent.not_needed_evidence": {"en": "0 cookies, 0 third-party domains", "ru": "0 cookie, 0 сторонних доменов", "fr": "0 cookie, 0 domaine tiers"},
    "consent.present": {"en": "Cookie/consent text present", "ru": "На странице есть текст про cookie/согласие", "fr": "Texte cookie/consentement présent"},
    "consent.present_evidence": {"en": "{cookies} cookie(s), {domains} third-party domain(s)", "ru": "{cookies} cookie, {domains} сторонних доменов", "fr": "{cookies} cookie(s), {domains} domaine(s) tiers"},
    "consent.missing": {"en": "{cookies} cookie(s) and {domains} third-party domain(s) on load but no consent notice", "ru": "{cookies} cookie и {domains} сторонних доменов при загрузке, но уведомления о согласии нет", "fr": "{cookies} cookie(s) et {domains} domaine(s) tiers au chargement mais aucun avis de consentement"},
    "consent.fix": {"en": "Add a consent banner that blocks non-essential cookies/trackers until the visitor accepts (GDPR/ePrivacy).", "ru": "Добавьте баннер согласия, блокирующий несущественные cookie/трекеры до принятия пользователем (GDPR/ePrivacy).", "fr": "Ajoutez un bandeau de consentement bloquant les cookies/traceurs non essentiels jusqu'à acceptation (RGPD/ePrivacy)."},

    "cookies_on_load.detail": {"en": "{n} cookie(s) readable by JavaScript", "ru": "{n} cookie доступны из JavaScript", "fr": "{n} cookie(s) lisible(s) par JavaScript"},
    "third_party_flows.detail": {"en": "{n} external domain(s) receive requests on load", "ru": "{n} внешних доменов получают запросы при загрузке", "fr": "{n} domaine(s) externe(s) reçoivent des requêtes au chargement"},

    "summary": {"en": "{policy}; {cookies} cookie(s), {domains} third-party domain(s)", "ru": "{policy}; {cookies} cookie, {domains} сторонних доменов", "fr": "{policy} ; {cookies} cookie(s), {domains} domaine(s) tiers"},
    "summary.present": {"en": "Privacy policy present", "ru": "Политика конфиденциальности есть", "fr": "Politique de confidentialité présente"},
    "summary.missing": {"en": "Privacy policy missing", "ru": "Политика конфиденциальности отсутствует", "fr": "Politique de confidentialité manquante"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def collect_privacy(facts: Dict[str, Any], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    findings: List[Finding] = []
    collects_data = bool(facts.get("hasPasswordField") or facts.get("hasForms"))

    has_privacy = facts.get("privacyLink", False)
    if has_privacy:
        detail = _t("privacy.found", lang)
    elif collects_data:
        detail = _t("privacy.missing_with_data", lang)
    else:
        detail = _t("privacy.missing", lang)
    evidence = _t("privacy.evidence", lang, forms=facts.get("hasForms", 0), login=_t("privacy.yes", lang) if facts.get("hasPasswordField") else _t("privacy.no", lang))
    findings.append(Finding(check=_t("check.privacy_policy", lang), status=Status.OK if has_privacy else Status.BAD, detail=detail, evidence=evidence, fix=None if has_privacy else _t("privacy.fix", lang)))

    has_terms = facts.get("termsLink", False)
    findings.append(Finding(check=_t("check.terms", lang), status=Status.OK if has_terms else Status.WARN, detail=_t("terms.found", lang) if has_terms else _t("terms.missing", lang), fix=None if has_terms else _t("terms.fix", lang)))

    cookies = facts.get("cookies") or []
    third_party = facts.get("thirdPartyDomains") or []
    needs_consent = bool(cookies or third_party)
    has_notice = facts.get("cookieNotice", False)
    if not needs_consent:
        findings.append(Finding(check=_t("check.cookie_consent", lang), status=Status.OK, detail=_t("consent.not_needed", lang), evidence=_t("consent.not_needed_evidence", lang)))
    elif has_notice:
        findings.append(Finding(check=_t("check.cookie_consent", lang), status=Status.OK, detail=_t("consent.present", lang), evidence=_t("consent.present_evidence", lang, cookies=len(cookies), domains=len(third_party))))
    else:
        findings.append(Finding(check=_t("check.cookie_consent", lang), status=Status.WARN, detail=_t("consent.missing", lang, cookies=len(cookies), domains=len(third_party)), evidence=f"cookies: {', '.join(cookies[:5]) or '-'}; domains: {', '.join(third_party[:5]) or '-'}", fix=_t("consent.fix", lang)))

    findings.append(Finding(check=_t("check.cookies_on_load", lang), status=Status.NA, detail=_t("cookies_on_load.detail", lang, n=len(cookies)), evidence=", ".join(cookies[:8]) or None))
    findings.append(Finding(check=_t("check.third_party_flows", lang), status=Status.NA, detail=_t("third_party_flows.detail", lang, n=len(third_party)), evidence=", ".join(third_party[:8]) or None))

    score = score_findings(findings, _weights_for(lang))
    policy_str = _t("summary.present", lang) if has_privacy else _t("summary.missing", lang)
    summary = _t("summary", lang, policy=policy_str, cookies=len(cookies), domains=len(third_party))
    return Pillar(name=pillar_name("Privacy & Compliance", lang), score=score, summary=summary, findings=findings)
