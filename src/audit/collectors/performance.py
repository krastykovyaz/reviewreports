from typing import Any, Dict, List, Optional

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_WEIGHT_KEYS = {"check.page_weight": 2, "check.fcp": 2, "check.ttfb": 1.5}

_M = {
    "check.ttfb": {"en": "Time to First Byte", "ru": "Время до первого байта (TTFB)", "fr": "Délai avant le premier octet (TTFB)"},
    "check.fcp": {"en": "First Contentful Paint", "ru": "First Contentful Paint", "fr": "First Contentful Paint"},
    "check.dcl": {"en": "DOMContentLoaded", "ru": "DOMContentLoaded", "fr": "DOMContentLoaded"},
    "check.load": {"en": "Full load", "ru": "Полная загрузка", "fr": "Chargement complet"},
    "check.requests": {"en": "Request count", "ru": "Количество запросов", "fr": "Nombre de requêtes"},
    "check.page_weight": {"en": "Page weight", "ru": "Вес страницы", "fr": "Poids de la page"},
    "check.largest_resources": {"en": "Largest resources", "ru": "Самые тяжёлые ресурсы", "fr": "Ressources les plus lourdes"},
    "check.render_blocking": {"en": "Render-blocking scripts", "ru": "Скрипты, блокирующие рендеринг", "fr": "Scripts bloquant le rendu"},
    "check.stylesheets": {"en": "Stylesheets", "ru": "Таблицы стилей", "fr": "Feuilles de style"},

    "na.not_reported": {"en": "Not reported by the browser in this session", "ru": "Не сообщено браузером в этой сессии", "fr": "Non signalé par le navigateur pour cette session"},
    "threshold.good": {"en": "{value} {unit} (good ≤ {good})", "ru": "{value} {unit} (хорошо ≤ {good})", "fr": "{value} {unit} (bon ≤ {good})"},
    "threshold.needs_improvement": {"en": "{value} {unit} (needs improvement; good ≤ {good})", "ru": "{value} {unit} (нужно улучшить; хорошо ≤ {good})", "fr": "{value} {unit} (à améliorer ; bon ≤ {good})"},
    "threshold.poor": {"en": "{value} {unit} (poor; good ≤ {good})", "ru": "{value} {unit} (плохо; хорошо ≤ {good})", "fr": "{value} {unit} (mauvais ; bon ≤ {good})"},
    "unit.ms": {"en": "ms", "ru": "мс", "fr": "ms"},

    "ttfb.fix": {"en": "Reduce server response time: cache at the CDN/edge, add server-side caching, or move to a faster host/region.", "ru": "Уменьшите время ответа сервера: кэшируйте на CDN/edge, добавьте серверное кэширование или переместитесь на более быстрый хостинг/регион.", "fr": "Réduisez le temps de réponse du serveur : mise en cache CDN/edge, cache côté serveur, ou hébergement/région plus rapide."},
    "fcp.fix": {"en": "Inline critical CSS, defer non-critical JS, preload fonts, and cut render-blocking resources.", "ru": "Встройте критический CSS, отложите некритичный JS, преднагрузите шрифты и уберите блокирующие рендеринг ресурсы.", "fr": "Intégrez le CSS critique en ligne, différez le JS non critique, préchargez les polices et supprimez les ressources bloquant le rendu."},
    "dcl.fix": {"en": "Reduce the amount of synchronous JavaScript executed before the DOM is ready.", "ru": "Уменьшите объём синхронного JavaScript, выполняемого до готовности DOM.", "fr": "Réduisez la quantité de JavaScript synchrone exécuté avant que le DOM soit prêt."},
    "load.fix": {"en": "Lazy-load below-the-fold images and third-party embeds; compress large assets.", "ru": "Отложите загрузку изображений вне первого экрана и сторонних виджетов; сжимайте крупные файлы.", "fr": "Chargez en différé les images sous la ligne de flottaison et les widgets tiers ; compressez les ressources volumineuses."},

    "requests.evidence": {"en": "{n} resource(s) loaded ({by_type})", "ru": "{n} ресурс(ов) загружено ({by_type})", "fr": "{n} ressource(s) chargée(s) ({by_type})"},
    "requests.fix": {"en": "Bundle/concatenate assets, use CSS sprites or SVG icons, and remove unused third-party scripts.", "ru": "Объединяйте файлы, используйте CSS-спрайты или SVG-иконки, удалите неиспользуемые сторонние скрипты.", "fr": "Regroupez/concaténez les ressources, utilisez des sprites CSS ou icônes SVG, et retirez les scripts tiers inutilisés."},

    "weight.value": {"en": "{kb} transferred", "ru": "{kb} передано", "fr": "{kb} transféré(s)"},
    "weight.unknown_suffix": {"en": " ({n} cross-origin resource(s) not measurable — add Timing-Allow-Origin on their servers)", "ru": " ({n} кросс-доменных ресурсов невозможно измерить — добавьте Timing-Allow-Origin на их серверах)", "fr": " ({n} ressource(s) cross-origin non mesurable(s) — ajoutez Timing-Allow-Origin sur leurs serveurs)"},
    "weight.fix": {"en": "Compress images (WebP/AVIF), enable Brotli/gzip, tree-shake JS bundles, and subset fonts.", "ru": "Сжимайте изображения (WebP/AVIF), включите Brotli/gzip, удаляйте неиспользуемый код из JS-бандлов, используйте подмножества шрифтов.", "fr": "Compressez les images (WebP/AVIF), activez Brotli/gzip, effectuez du tree-shaking des bundles JS, et sous-ensemblez les polices."},
    "weight.na": {"en": "Transfer sizes not exposed (cached responses or missing Timing-Allow-Origin)", "ru": "Размеры передачи недоступны (кэшированные ответы или отсутствует Timing-Allow-Origin)", "fr": "Tailles de transfert non exposées (réponses en cache ou Timing-Allow-Origin manquant)"},

    "largest.na": {"en": "Top resources by transfer size", "ru": "Самые тяжёлые ресурсы по размеру передачи", "fr": "Principales ressources par taille de transfert"},

    "render_blocking.ok": {"en": "No synchronous scripts in <head>", "ru": "Нет синхронных скриптов в <head>", "fr": "Aucun script synchrone dans <head>"},
    "render_blocking.bad": {"en": "{n} synchronous <script src> in <head> block rendering", "ru": "{n} синхронных <script src> в <head> блокируют рендеринг", "fr": "{n} <script src> synchrone(s) dans <head> bloquent le rendu"},
    "render_blocking.fix": {"en": "Add defer (or async) to head scripts, or move them before </body>.", "ru": "Добавьте defer (или async) к скриптам в head, либо перенесите их перед </body>.", "fr": "Ajoutez defer (ou async) aux scripts du head, ou déplacez-les avant </body>."},

    "stylesheets.evidence": {"en": "{n} external stylesheet(s)", "ru": "{n} внешних таблиц стилей", "fr": "{n} feuille(s) de style externe(s)"},
    "stylesheets.fix": {"en": "Combine stylesheets and inline the critical above-the-fold CSS.", "ru": "Объедините таблицы стилей и встройте критический CSS для первого экрана.", "fr": "Regroupez les feuilles de style et intégrez en ligne le CSS critique au-dessus de la ligne de flottaison."},

    "summary": {"en": "{n} requests, {weight}", "ru": "{n} запросов, {weight}", "fr": "{n} requêtes, {weight}"},
    "summary.unknown_weight": {"en": "unknown weight", "ru": "вес неизвестен", "fr": "poids inconnu"},
    "summary.fcp_suffix": {"en": ", FCP {n} ms", "ru": ", FCP {n} мс", "fr": ", FCP {n} ms"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def _kb(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    if n >= 10 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024:.1f} KB"


def _threshold(lang: str, check_key: str, value_ms, good: int, poor: int, fix_key: str) -> Finding:
    check = _t(check_key, lang)
    if value_ms is None:
        return Finding(check=check, status=Status.NA, detail=_t("na.not_reported", lang))
    unit = _t("unit.ms", lang)
    evidence = f"{value_ms} {unit}"
    if value_ms <= good:
        return Finding(check=check, status=Status.OK, detail=_t("threshold.good", lang, value=value_ms, unit=unit, good=good), evidence=evidence)
    if value_ms <= poor:
        return Finding(check=check, status=Status.WARN, detail=_t("threshold.needs_improvement", lang, value=value_ms, unit=unit, good=good), evidence=evidence, fix=_t(fix_key, lang))
    return Finding(check=check, status=Status.BAD, detail=_t("threshold.poor", lang, value=value_ms, unit=unit, good=good), evidence=evidence, fix=_t(fix_key, lang))


def collect_performance(facts: Dict[str, Any], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    findings: List[Finding] = []
    resources = facts.get("resources") or []
    timing = facts.get("timing") or {}
    paint = facts.get("paint") or {}

    findings.append(_threshold(lang, "check.ttfb", timing.get("ttfbMs"), 800, 1800, "ttfb.fix"))
    findings.append(_threshold(lang, "check.fcp", paint.get("first-contentful-paint"), 1800, 3000, "fcp.fix"))
    findings.append(_threshold(lang, "check.dcl", timing.get("domContentLoadedMs"), 2500, 4000, "dcl.fix"))
    findings.append(_threshold(lang, "check.load", timing.get("loadMs"), 4000, 7000, "load.fix"))

    count = len(resources)
    by_type: Dict[str, int] = {}
    for r in resources:
        by_type[r.get("type") or "other"] = by_type.get(r.get("type") or "other", 0) + 1
    by_type_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]))
    findings.append(Finding(check=_t("check.requests", lang), status=Status.OK if count <= 50 else Status.WARN if count <= 100 else Status.BAD, detail=_t("requests.evidence", lang, n=count, by_type=by_type_str), evidence=by_type_str, fix=None if count <= 50 else _t("requests.fix", lang)))

    measured = [r for r in resources if r.get("transferSize")]
    total_bytes = sum(r["transferSize"] for r in measured) + (timing.get("transferSize") or 0)
    if measured or timing.get("transferSize"):
        unknown = count - len(measured)
        status = Status.OK if total_bytes <= 1_500_000 else Status.WARN if total_bytes <= 3_000_000 else Status.BAD
        detail = _t("weight.value", lang, kb=_kb(total_bytes))
        if unknown:
            detail += _t("weight.unknown_suffix", lang, n=unknown)
        findings.append(Finding(check=_t("check.page_weight", lang), status=status, detail=detail, evidence=f"{total_bytes} bytes over {len(measured) + 1} measured responses", fix=None if status == Status.OK else _t("weight.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.page_weight", lang), status=Status.NA, detail=_t("weight.na", lang)))

    largest = sorted(measured, key=lambda r: -r["transferSize"])[:5]
    if largest:
        findings.append(Finding(check=_t("check.largest_resources", lang), status=Status.NA, detail=_t("largest.na", lang), evidence="; ".join(f"{r['name'].split('/')[-1][:50] or r['name'][:50]} ({_kb(r['transferSize'])})" for r in largest)))

    blocking = facts.get("headSyncScripts", 0)
    findings.append(Finding(check=_t("check.render_blocking", lang), status=Status.OK if blocking == 0 else Status.WARN, detail=_t("render_blocking.ok", lang) if blocking == 0 else _t("render_blocking.bad", lang, n=blocking), evidence=str(blocking), fix=None if blocking == 0 else _t("render_blocking.fix", lang)))

    css = facts.get("stylesheetCount", 0)
    findings.append(Finding(check=_t("check.stylesheets", lang), status=Status.OK if css <= 4 else Status.WARN, detail=_t("stylesheets.evidence", lang, n=css), evidence=str(css), fix=None if css <= 4 else _t("stylesheets.fix", lang)))

    score = score_findings(findings, _weights_for(lang))
    fcp = paint.get("first-contentful-paint")
    weight_str = _kb(total_bytes) if total_bytes else _t("summary.unknown_weight", lang)
    summary = _t("summary", lang, n=count, weight=weight_str)
    if fcp:
        summary += _t("summary.fcp_suffix", lang, n=fcp)
    return Pillar(name=pillar_name("Performance", lang), score=score, summary=summary, findings=findings)
