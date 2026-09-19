import re
from typing import Any, Dict, List, Optional

from src.audit.http_client import FetchResult
from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_WEIGHT_KEYS = {"check.https": 3, "check.csp": 2, "check.hsts": 2, "check.clickjacking": 1.5, "check.mixed_content": 2, "check.cookie_flags": 1.5}

_M = {
    "check.https": {"en": "HTTPS", "ru": "HTTPS", "fr": "HTTPS"},
    "check.http_redirect": {"en": "HTTP → HTTPS redirect", "ru": "Редирект HTTP → HTTPS", "fr": "Redirection HTTP → HTTPS"},
    "check.csp": {"en": "Content-Security-Policy", "ru": "Content-Security-Policy", "fr": "Content-Security-Policy"},
    "check.hsts": {"en": "Strict-Transport-Security (HSTS)", "ru": "Strict-Transport-Security (HSTS)", "fr": "Strict-Transport-Security (HSTS)"},
    "check.clickjacking": {"en": "Clickjacking protection", "ru": "Защита от кликджекинга", "fr": "Protection contre le clickjacking"},
    "check.xcto": {"en": "X-Content-Type-Options", "ru": "X-Content-Type-Options", "fr": "X-Content-Type-Options"},
    "check.referrer_policy": {"en": "Referrer-Policy", "ru": "Referrer-Policy", "fr": "Referrer-Policy"},
    "check.permissions_policy": {"en": "Permissions-Policy", "ru": "Permissions-Policy", "fr": "Permissions-Policy"},
    "check.mixed_content": {"en": "Mixed content", "ru": "Смешанный контент (mixed content)", "fr": "Contenu mixte"},
    "check.cookie_flags": {"en": "Cookie flags", "ru": "Флаги cookie", "fr": "Attributs des cookies"},
    "check.version_disclosure": {"en": "Version disclosure", "ru": "Раскрытие версии ПО", "fr": "Divulgation de version"},

    "https.ok": {"en": "Served over HTTPS", "ru": "Отдаётся по HTTPS", "fr": "Servi en HTTPS"},
    "https.bad": {"en": "Final URL is plain HTTP", "ru": "Итоговый URL использует обычный HTTP", "fr": "L'URL finale est en HTTP simple"},
    "https.fix": {"en": "Obtain a TLS certificate (e.g. Let's Encrypt) and serve the site over HTTPS.", "ru": "Получите TLS-сертификат (например, Let's Encrypt) и переведите сайт на HTTPS.", "fr": "Obtenez un certificat TLS (par ex. Let's Encrypt) et servez le site en HTTPS."},

    "redirect.ok": {"en": "http:// redirects to https://", "ru": "http:// перенаправляется на https://", "fr": "http:// redirige vers https://"},
    "redirect.bad": {"en": "http:// does not redirect to https://", "ru": "http:// не перенаправляется на https://", "fr": "http:// ne redirige pas vers https://"},
    "redirect.evidence": {"en": "http request ended at {url} ({code})", "ru": "http-запрос завершился на {url} ({code})", "fr": "la requête http s'est terminée sur {url} ({code})"},
    "redirect.fix": {"en": "Add a 301 redirect from http:// to https:// at the server or CDN.", "ru": "Настройте 301-редирект с http:// на https:// на сервере или CDN.", "fr": "Ajoutez une redirection 301 de http:// vers https:// au niveau du serveur ou du CDN."},

    "csp.set": {"en": "Set", "ru": "Настроен", "fr": "Défini"},
    "csp.missing": {"en": "Not set — no defence-in-depth against XSS/injection", "ru": "Не настроен — нет дополнительной защиты от XSS/инъекций", "fr": "Non défini — aucune défense en profondeur contre XSS/injection"},
    "csp.fix": {"en": "Start with a report-only policy (Content-Security-Policy-Report-Only: default-src 'self') and tighten it before enforcing.", "ru": "Начните с политики в режиме report-only (Content-Security-Policy-Report-Only: default-src 'self') и ужесточайте её перед включением.", "fr": "Commencez par une politique en mode rapport seul (Content-Security-Policy-Report-Only: default-src 'self') puis renforcez-la avant de l'appliquer."},

    "hsts.na": {"en": "Not applicable without HTTPS", "ru": "Неприменимо без HTTPS", "fr": "Non applicable sans HTTPS"},
    "hsts.missing": {"en": "Not set — browsers may still attempt plain-HTTP connections", "ru": "Не настроен — браузеры могут пытаться подключаться по обычному HTTP", "fr": "Non défini — les navigateurs peuvent encore tenter des connexions HTTP simples"},
    "hsts.missing_fix": {"en": "Add Strict-Transport-Security: max-age=31536000; includeSubDomains (then consider preload).", "ru": "Добавьте Strict-Transport-Security: max-age=31536000; includeSubDomains (затем рассмотрите preload).", "fr": "Ajoutez Strict-Transport-Security: max-age=31536000; includeSubDomains (puis envisagez preload)."},
    "hsts.ok": {"en": "Set with adequate max-age", "ru": "Настроен с достаточным max-age", "fr": "Défini avec un max-age suffisant"},
    "hsts.short": {"en": "max-age is only {n}s (recommended ≥ 15552000, 180 days)", "ru": "max-age всего {n} сек (рекомендуется ≥ 15552000, 180 дней)", "fr": "max-age n'est que de {n}s (recommandé ≥ 15552000, 180 jours)"},
    "hsts.short_fix": {"en": "Raise max-age to at least 15552000 (180 days); 31536000 recommended.", "ru": "Увеличьте max-age минимум до 15552000 (180 дней); рекомендуется 31536000.", "fr": "Augmentez max-age à au moins 15552000 (180 jours) ; 31536000 recommandé."},

    "clickjacking.ok": {"en": "Framing restricted", "ru": "Встраивание в iframe ограничено", "fr": "L'intégration en cadre est restreinte"},
    "clickjacking.bad": {"en": "Neither X-Frame-Options nor CSP frame-ancestors — the page can be embedded in a hostile iframe", "ru": "Нет ни X-Frame-Options, ни frame-ancestors в CSP — страницу можно встроить во вредоносный iframe", "fr": "Ni X-Frame-Options ni frame-ancestors CSP — la page peut être intégrée dans un iframe malveillant"},
    "clickjacking.fix": {"en": "Add X-Frame-Options: DENY (or SAMEORIGIN), or frame-ancestors 'none' in the CSP.", "ru": "Добавьте X-Frame-Options: DENY (или SAMEORIGIN), либо frame-ancestors 'none' в CSP.", "fr": "Ajoutez X-Frame-Options: DENY (ou SAMEORIGIN), ou frame-ancestors 'none' dans la CSP."},

    "xcto.ok": {"en": "nosniff set", "ru": "nosniff настроен", "fr": "nosniff défini"},
    "xcto.bad": {"en": "Not set — browsers may MIME-sniff responses", "ru": "Не настроен — браузеры могут определять MIME-тип по содержимому", "fr": "Non défini — les navigateurs peuvent deviner le type MIME"},
    "xcto.fix": {"en": "Add X-Content-Type-Options: nosniff.", "ru": "Добавьте X-Content-Type-Options: nosniff.", "fr": "Ajoutez X-Content-Type-Options: nosniff."},

    "referrer.ok": {"en": "Set", "ru": "Настроен", "fr": "Défini"},
    "referrer.bad": {"en": "Not set — full URLs may leak to third parties in the Referer header", "ru": "Не настроен — полные URL могут утекать третьим лицам через заголовок Referer", "fr": "Non défini — les URL complètes peuvent fuiter vers des tiers via l'en-tête Referer"},
    "referrer.fix": {"en": "Add Referrer-Policy: strict-origin-when-cross-origin.", "ru": "Добавьте Referrer-Policy: strict-origin-when-cross-origin.", "fr": "Ajoutez Referrer-Policy: strict-origin-when-cross-origin."},

    "permissions.ok": {"en": "Set", "ru": "Настроен", "fr": "Défini"},
    "permissions.bad": {"en": "Not set — browser features (camera, geolocation, …) are not explicitly restricted", "ru": "Не настроен — возможности браузера (камера, геолокация…) явно не ограничены", "fr": "Non défini — les fonctionnalités du navigateur (caméra, géolocalisation…) ne sont pas explicitement restreintes"},
    "permissions.fix": {"en": "Add Permissions-Policy: camera=(), microphone=(), geolocation=() (allow what you actually use).", "ru": "Добавьте Permissions-Policy: camera=(), microphone=(), geolocation=() (разрешив то, что реально используется).", "fr": "Ajoutez Permissions-Policy: camera=(), microphone=(), geolocation=() (autorisez ce que vous utilisez réellement)."},

    "mixed.bad": {"en": "{n} resource(s) loaded over plain HTTP on an HTTPS page", "ru": "{n} ресурс(ов) загружены по обычному HTTP на HTTPS-странице", "fr": "{n} ressource(s) chargée(s) en HTTP simple sur une page HTTPS"},
    "mixed.ok": {"en": "No plain-HTTP resources on the HTTPS page", "ru": "На HTTPS-странице нет ресурсов, загружаемых по HTTP", "fr": "Aucune ressource HTTP simple sur la page HTTPS"},
    "mixed.fix": {"en": "Serve every script/style/image/iframe over https:// (or protocol-relative URLs).", "ru": "Отдавайте все скрипты/стили/изображения/iframe по https:// (или через protocol-relative URL).", "fr": "Servez chaque script/style/image/iframe en https:// (ou avec des URL relatives au protocole)."},

    "cookie.na": {"en": "No cookies set by the initial response", "ru": "Начальный ответ не устанавливает cookie", "fr": "Aucun cookie défini par la réponse initiale"},
    "cookie.weak": {"en": "{weak} of {total} cookie(s) missing Secure/HttpOnly/SameSite", "ru": "{weak} из {total} cookie без Secure/HttpOnly/SameSite", "fr": "{weak} cookie(s) sur {total} sans Secure/HttpOnly/SameSite"},
    "cookie.ok": {"en": "All {total} cookie(s) carry Secure, HttpOnly and SameSite", "ru": "Все {total} cookie имеют Secure, HttpOnly и SameSite", "fr": "Les {total} cookie(s) portent Secure, HttpOnly et SameSite"},
    "cookie.fix": {"en": "Set Secure; HttpOnly; SameSite=Lax (or Strict) on every cookie that does not need JavaScript access.", "ru": "Установите Secure; HttpOnly; SameSite=Lax (или Strict) для каждого cookie, не требующего доступа из JavaScript.", "fr": "Définissez Secure; HttpOnly; SameSite=Lax (ou Strict) sur chaque cookie qui n'a pas besoin d'accès JavaScript."},

    "version.bad": {"en": "Response headers reveal software versions, which helps attackers target known CVEs", "ru": "Заголовки ответа раскрывают версии ПО, что упрощает атакующим поиск известных уязвимостей (CVE)", "fr": "Les en-têtes de réponse révèlent des versions logicielles, facilitant le ciblage de CVE connues"},
    "version.fix": {"en": "Strip version numbers (nginx: server_tokens off; Express: app.disable('x-powered-by')).", "ru": "Скройте номера версий (nginx: server_tokens off; Express: app.disable('x-powered-by')).", "fr": "Supprimez les numéros de version (nginx : server_tokens off ; Express : app.disable('x-powered-by'))."},
    "version.ok": {"en": "No software versions exposed in headers", "ru": "Версии ПО не раскрываются в заголовках", "fr": "Aucune version logicielle exposée dans les en-têtes"},

    "summary": {"en": "{set}/6 hardening headers in place; {https}", "ru": "{set}/6 защитных заголовков настроено; {https}", "fr": "{set}/6 en-têtes de sécurité en place ; {https}"},
    "summary.https_yes": {"en": "HTTPS", "ru": "HTTPS есть", "fr": "HTTPS"},
    "summary.https_no": {"en": "no HTTPS", "ru": "HTTPS отсутствует", "fr": "pas de HTTPS"},
    "summary.mixed_suffix": {"en": "; {n} mixed-content", "ru": "; {n} смешанного контента", "fr": " ; {n} contenu(s) mixte(s)"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def _all_set_cookies(headers: Dict[str, str]) -> List[str]:
    raw = _header(headers, "set-cookie")
    if not raw:
        return []
    return [c.strip() for c in re.split(r",(?=[^;,]+=)", raw) if c.strip()]


def collect_security_headers(homepage: FetchResult, http_redirect: Optional[FetchResult], facts: Dict[str, Any], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    headers = homepage.headers
    findings: List[Finding] = []

    is_https = homepage.url.startswith("https://")
    findings.append(Finding(check=_t("check.https", lang), status=Status.OK if is_https else Status.BAD, detail=_t("https.ok", lang) if is_https else _t("https.bad", lang), evidence=homepage.url, fix=None if is_https else _t("https.fix", lang)))

    if http_redirect is not None:
        redirected = http_redirect.url.startswith("https://")
        evidence = _t("redirect.evidence", lang, url=http_redirect.url, code=http_redirect.status_code)
        findings.append(Finding(check=_t("check.http_redirect", lang), status=Status.OK if redirected else Status.WARN, detail=_t("redirect.ok", lang) if redirected else _t("redirect.bad", lang), evidence=evidence, fix=None if redirected else _t("redirect.fix", lang)))

    csp = _header(headers, "content-security-policy")
    findings.append(Finding(check=_t("check.csp", lang), status=Status.OK if csp else Status.BAD, detail=_t("csp.set", lang) if csp else _t("csp.missing", lang), evidence=(csp or "")[:200] or None, fix=None if csp else _t("csp.fix", lang)))

    hsts = _header(headers, "strict-transport-security")
    if not is_https:
        findings.append(Finding(check=_t("check.hsts", lang), status=Status.NA, detail=_t("hsts.na", lang)))
    elif not hsts:
        findings.append(Finding(check=_t("check.hsts", lang), status=Status.BAD, detail=_t("hsts.missing", lang), fix=_t("hsts.missing_fix", lang)))
    else:
        m = re.search(r"max-age=(\d+)", hsts)
        max_age = int(m.group(1)) if m else 0
        ok = max_age >= 15552000
        findings.append(Finding(check=_t("check.hsts", lang), status=Status.OK if ok else Status.WARN, detail=_t("hsts.ok", lang) if ok else _t("hsts.short", lang, n=max_age), evidence=hsts, fix=None if ok else _t("hsts.short_fix", lang)))

    xfo = _header(headers, "x-frame-options")
    frame_ancestors = bool(csp and "frame-ancestors" in csp.lower())
    if xfo or frame_ancestors:
        findings.append(Finding(check=_t("check.clickjacking", lang), status=Status.OK, detail=_t("clickjacking.ok", lang), evidence=f"X-Frame-Options={xfo}" if xfo else "CSP frame-ancestors"))
    else:
        findings.append(Finding(check=_t("check.clickjacking", lang), status=Status.BAD, detail=_t("clickjacking.bad", lang), fix=_t("clickjacking.fix", lang)))

    xcto = _header(headers, "x-content-type-options")
    ok = bool(xcto and "nosniff" in xcto.lower())
    findings.append(Finding(check=_t("check.xcto", lang), status=Status.OK if ok else Status.BAD, detail=_t("xcto.ok", lang) if ok else _t("xcto.bad", lang), evidence=xcto, fix=None if ok else _t("xcto.fix", lang)))

    rp = _header(headers, "referrer-policy")
    findings.append(Finding(check=_t("check.referrer_policy", lang), status=Status.OK if rp else Status.WARN, detail=_t("referrer.ok", lang) if rp else _t("referrer.bad", lang), evidence=rp, fix=None if rp else _t("referrer.fix", lang)))

    pp = _header(headers, "permissions-policy")
    findings.append(Finding(check=_t("check.permissions_policy", lang), status=Status.OK if pp else Status.WARN, detail=_t("permissions.ok", lang) if pp else _t("permissions.bad", lang), evidence=(pp or "")[:120] or None, fix=None if pp else _t("permissions.fix", lang)))

    mixed = facts.get("mixedContent") or []
    if is_https:
        findings.append(Finding(check=_t("check.mixed_content", lang), status=Status.BAD if mixed else Status.OK, detail=_t("mixed.bad", lang, n=len(mixed)) if mixed else _t("mixed.ok", lang), evidence="; ".join(mixed[:5]) if mixed else None, fix=_t("mixed.fix", lang) if mixed else None))

    cookies = _all_set_cookies(headers)
    if cookies:
        weak = [c.split("=")[0] for c in cookies if "secure" not in c.lower() or "httponly" not in c.lower() or "samesite" not in c.lower()]
        findings.append(Finding(check=_t("check.cookie_flags", lang), status=Status.WARN if weak else Status.OK, detail=_t("cookie.weak", lang, weak=len(weak), total=len(cookies)) if weak else _t("cookie.ok", lang, total=len(cookies)), evidence=", ".join(weak[:5]) if weak else ", ".join(c.split('=')[0] for c in cookies[:5]), fix=_t("cookie.fix", lang) if weak else None))
    else:
        findings.append(Finding(check=_t("check.cookie_flags", lang), status=Status.NA, detail=_t("cookie.na", lang)))

    disclosures = [f"{h}: {_header(headers, h)}" for h in ("server", "x-powered-by", "x-aspnet-version") if _header(headers, h)]
    versioned = [d for d in disclosures if re.search(r"\d+\.\d+", d)]
    if versioned:
        findings.append(Finding(check=_t("check.version_disclosure", lang), status=Status.WARN, detail=_t("version.bad", lang), evidence="; ".join(versioned), fix=_t("version.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.version_disclosure", lang), status=Status.OK, detail=_t("version.ok", lang), evidence="; ".join(disclosures) or None))

    score = score_findings(findings, _weights_for(lang))
    set_count = sum(1 for f in findings if f.status == Status.OK and f.check in (_t("check.csp", lang), _t("check.hsts", lang), _t("check.clickjacking", lang), _t("check.xcto", lang), _t("check.referrer_policy", lang), _t("check.permissions_policy", lang)))
    summary = _t("summary", lang, set=set_count, https=_t("summary.https_yes", lang) if is_https else _t("summary.https_no", lang))
    if mixed:
        summary += _t("summary.mixed_suffix", lang, n=len(mixed))
    return Pillar(name=pillar_name("Security", lang), score=score, summary=summary, findings=findings)
