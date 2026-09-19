import asyncio
from typing import Any, Dict, List

import httpx

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.schema import Finding, Pillar, Status

_M = {
    "check.links_on_page": {"en": "Links on page", "ru": "Ссылки на странице", "fr": "Liens sur la page"},
    "check.link_inventory": {"en": "Link inventory", "ru": "Сводка по ссылкам", "fr": "Inventaire des liens"},
    "check.broken_links": {"en": "Broken links", "ru": "Неработающие ссылки", "fr": "Liens brisés"},
    "check.descriptive_text": {"en": "Descriptive link text", "ru": "Описательный текст ссылок", "fr": "Texte de lien descriptif"},

    "none_found": {"en": "Zero <a href> links in the rendered DOM", "ru": "В отрендеренном DOM нет ссылок <a href>", "fr": "Aucun lien <a href> dans le DOM rendu"},
    "inventory": {"en": "{total} link(s): {internal} internal, {external} external", "ru": "{total} ссылок: {internal} внутренних, {external} внешних", "fr": "{total} lien(s) : {internal} interne(s), {external} externe(s)"},
    "inventory.evidence": {"en": "{n} unique URL(s) checked (cap {cap})", "ru": "проверено {n} уникальных URL (максимум {cap})", "fr": "{n} URL unique(s) vérifiée(s) (limite {cap})"},
    "broken.some": {"en": "{n} of {total} checked link(s) are broken", "ru": "{n} из {total} проверенных ссылок не работают", "fr": "{n} lien(s) sur {total} vérifié(s) sont brisés"},
    "broken.none": {"en": "All {n} checked link(s) resolve", "ru": "Все {n} проверенных ссылок работают", "fr": "Les {n} lien(s) vérifié(s) fonctionnent"},
    "broken.evidence_none": {"en": "0 broken", "ru": "0 неработающих", "fr": "0 brisé"},
    "broken.fix": {"en": "Fix or remove the dead links; set up 301 redirects for moved pages.", "ru": "Исправьте или удалите неработающие ссылки; настройте 301-редиректы для перемещённых страниц.", "fr": "Corrigez ou supprimez les liens morts ; mettez en place des redirections 301 pour les pages déplacées."},

    "descriptive.ok": {"en": "All links have text", "ru": "У всех ссылок есть текст", "fr": "Tous les liens ont du texte"},
    "descriptive.bad": {"en": "{n} link(s) with no text", "ru": "{n} ссылок без текста", "fr": "{n} lien(s) sans texte"},
    "descriptive.fix": {"en": "Give links descriptive text (not 'click here' or empty icons).", "ru": "Дайте ссылкам описательный текст (не «нажмите здесь» и не пустые иконки).", "fr": "Donnez aux liens un texte descriptif (pas « cliquez ici » ni des icônes vides)."},

    "summary": {"en": "{ok}/{total} checked links OK; {internal} internal, {external} external", "ru": "{ok}/{total} проверенных ссылок в порядке; {internal} внутренних, {external} внешних", "fr": "{ok}/{total} liens vérifiés OK ; {internal} interne(s), {external} externe(s)"},
}

_t = make_translator(_M)


async def _check(client: httpx.AsyncClient, href: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            resp = await client.head(href)
            if resp.status_code >= 400 or resp.status_code == 405:
                resp = await client.get(href)  # some servers reject HEAD
            return href, resp.status_code, None
        except httpx.HTTPError as exc:
            return href, None, type(exc).__name__


async def collect_broken_links(facts: Dict[str, Any], max_check: int = 25, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    links: List[Dict[str, Any]] = facts.get("links") or []
    if not links:
        return Pillar(name=pillar_name("Links", lang), score=None, summary=_t("none_found", lang), findings=[Finding(check=_t("check.links_on_page", lang), status=Status.NA, detail=_t("none_found", lang))])

    internal = [l for l in links if not l.get("external")]
    external = [l for l in links if l.get("external")]
    unique = list(dict.fromkeys(l["href"] for l in links))[:max_check]

    findings: List[Finding] = [
        Finding(check=_t("check.link_inventory", lang), status=Status.NA, detail=_t("inventory", lang, total=len(links), internal=len(internal), external=len(external)), evidence=_t("inventory.evidence", lang, n=len(unique), cap=max_check)),
    ]

    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0, headers={"User-Agent": "reviewreports-linkcheck/1.0"}) as client:
        results = await asyncio.gather(*(_check(client, href, sem) for href in unique))

    broken = [(h, code, err) for h, code, err in results if code is None or code >= 400]
    if broken:
        findings.append(Finding(check=_t("check.broken_links", lang), status=Status.BAD if len(broken) / len(unique) > 0.1 else Status.WARN, detail=_t("broken.some", lang, n=len(broken), total=len(unique)), evidence="; ".join(f"{h} → {code or err}" for h, code, err in broken[:8]), fix=_t("broken.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.broken_links", lang), status=Status.OK, detail=_t("broken.none", lang, n=len(unique)), evidence=_t("broken.evidence_none", lang)))

    empty_text = facts.get("linksWithoutText", 0)
    findings.append(Finding(check=_t("check.descriptive_text", lang), status=Status.OK if empty_text == 0 else Status.WARN, detail=_t("descriptive.ok", lang) if empty_text == 0 else _t("descriptive.bad", lang, n=empty_text), fix=None if empty_text == 0 else _t("descriptive.fix", lang)))

    score = round(10.0 * (len(unique) - len(broken)) / len(unique), 1)
    summary = _t("summary", lang, ok=len(unique) - len(broken), total=len(unique), internal=len(internal), external=len(external))
    return Pillar(name=pillar_name("Links", lang), score=score, summary=summary, findings=findings)
