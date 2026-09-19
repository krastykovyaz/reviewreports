import re
from typing import Any, Dict, List, Optional

from src.audit.http_client import FetchResult
from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_WEIGHT_KEYS = {"check.title": 2, "check.meta_description": 1.5, "check.indexability": 3, "check.h1": 2, "check.canonical": 1}

_M = {
    "check.title": {"en": "Title tag", "ru": "Тег title", "fr": "Balise title"},
    "check.meta_description": {"en": "Meta description", "ru": "Meta description", "fr": "Meta description"},
    "check.indexability": {"en": "Indexability (robots meta)", "ru": "Индексируемость (robots meta)", "fr": "Indexabilité (meta robots)"},
    "check.h1": {"en": "H1 heading", "ru": "Заголовок H1", "fr": "Titre H1"},
    "check.heading_structure": {"en": "Heading structure", "ru": "Структура заголовков", "fr": "Structure des titres"},
    "check.canonical": {"en": "Canonical URL", "ru": "Канонический URL", "fr": "URL canonique"},
    "check.og": {"en": "Open Graph tags", "ru": "Теги Open Graph", "fr": "Balises Open Graph"},
    "check.twitter": {"en": "Twitter card", "ru": "Twitter card", "fr": "Carte Twitter"},
    "check.structured_data": {"en": "Structured data", "ru": "Микроразметка (structured data)", "fr": "Données structurées"},
    "check.favicon": {"en": "Favicon", "ru": "Favicon", "fr": "Favicon"},
    "check.robots_txt": {"en": "robots.txt", "ru": "robots.txt", "fr": "robots.txt"},
    "check.sitemap": {"en": "sitemap.xml", "ru": "sitemap.xml", "fr": "sitemap.xml"},
    "check.hreflang": {"en": "hreflang", "ru": "hreflang", "fr": "hreflang"},

    "len.missing": {"en": "Missing", "ru": "Отсутствует", "fr": "Manquant"},
    "len.ok": {"en": "{n} {unit} (within {lo}–{hi})", "ru": "{n} {unit} (в пределах {lo}–{hi})", "fr": "{n} {unit} (entre {lo} et {hi})"},
    "len.short": {"en": "Too short ({n} {unit}; recommended {lo}–{hi})", "ru": "Слишком коротко ({n} {unit}; рекомендуется {lo}–{hi})", "fr": "Trop court ({n} {unit} ; recommandé {lo}–{hi})"},
    "len.long": {"en": "Too long ({n} {unit}; recommended {lo}–{hi}) — will be truncated in search results", "ru": "Слишком длинно ({n} {unit}; рекомендуется {lo}–{hi}) — будет обрезано в результатах поиска", "fr": "Trop long ({n} {unit} ; recommandé {lo}–{hi}) — sera tronqué dans les résultats de recherche"},
    "len.evidence": {"en": '"{value}" ({n} {unit})', "ru": '«{value}» ({n} {unit})', "fr": '« {value} » ({n} {unit})'},
    "unit.characters": {"en": "characters", "ru": "симв.", "fr": "caractères"},

    "title.missing_fix": {"en": "Add a descriptive <title> of 30–60 characters containing the page's primary topic.", "ru": "Добавьте содержательный тег <title> длиной 30–60 символов с ключевой темой страницы.", "fr": "Ajoutez une balise <title> descriptive de 30 à 60 caractères contenant le sujet principal de la page."},
    "title.short_fix": {"en": "Expand to 30–60 characters so it reads fully in search results.", "ru": "Расширьте до 30–60 символов, чтобы он полностью отображался в результатах поиска.", "fr": "Étendez-le à 30–60 caractères pour qu'il s'affiche entièrement dans les résultats de recherche."},
    "title.long_fix": {"en": "Shorten to 30–60 characters, keeping the key phrase near the start.", "ru": "Сократите до 30–60 символов, разместив ключевую фразу в начале.", "fr": "Raccourcissez à 30–60 caractères, en plaçant la phrase clé au début."},
    "meta.missing_fix": {"en": 'Add <meta name="description"> of 70–160 characters summarising the page; it is the snippet shown in search results.', "ru": 'Добавьте <meta name="description"> длиной 70–160 символов с кратким описанием страницы — это сниппет в результатах поиска.', "fr": 'Ajoutez une balise <meta name="description"> de 70 à 160 caractères résumant la page ; c\'est l\'extrait affiché dans les résultats de recherche.'},
    "meta.short_fix": {"en": "Expand to 70–160 characters describing the page's value.", "ru": "Расширьте до 70–160 символов с описанием ценности страницы.", "fr": "Étendez-la à 70–160 caractères décrivant la valeur de la page."},
    "meta.long_fix": {"en": "Shorten to 70–160 characters; search engines truncate longer descriptions.", "ru": "Сократите до 70–160 символов — поисковики обрезают более длинные описания.", "fr": "Raccourcissez à 70–160 caractères ; les moteurs de recherche tronquent les descriptions plus longues."},

    "noindex": {"en": "Page is marked noindex — search engines will not index it", "ru": "Страница помечена noindex — поисковики не будут её индексировать", "fr": "La page est marquée noindex — les moteurs de recherche ne l'indexeront pas"},
    "noindex_fix": {"en": "Remove noindex from the robots meta tag unless this page is intentionally hidden from search.", "ru": "Уберите noindex из meta robots, если только страница не должна быть скрыта от поиска намеренно.", "fr": "Retirez noindex de la balise meta robots, sauf si cette page doit être volontairement masquée des recherches."},
    "indexable": {"en": "Indexable", "ru": "Индексируется", "fr": "Indexable"},
    "no_robots_meta": {"en": "no robots meta tag (defaults to index,follow)", "ru": "meta robots отсутствует (по умолчанию index,follow)", "fr": "aucune balise meta robots (index,follow par défaut)"},

    "h1.one": {"en": "Exactly one <h1>", "ru": "Ровно один <h1>", "fr": "Exactement un <h1>"},
    "h1.zero": {"en": "No <h1> element — the page has no primary heading for search engines or assistive tech", "ru": "Нет элемента <h1> — у страницы нет главного заголовка для поисковиков и вспомогательных технологий", "fr": "Aucun élément <h1> — la page n'a pas de titre principal pour les moteurs de recherche ou les technologies d'assistance"},
    "h1.zero_fix": {"en": "Wrap the page's main headline in a single <h1> containing the primary topic.", "ru": "Оберните основной заголовок страницы в единственный <h1> с ключевой темой.", "fr": "Placez le titre principal de la page dans un unique <h1> contenant le sujet principal."},
    "h1.many": {"en": "{n} <h1> elements (should be exactly one)", "ru": "{n} элементов <h1> (должен быть ровно один)", "fr": "{n} éléments <h1> (un seul est recommandé)"},
    "h1.many_fix": {"en": "Keep one <h1> for the main topic and demote the others to <h2>.", "ru": "Оставьте один <h1> для главной темы, остальные понизьте до <h2>.", "fr": "Conservez un seul <h1> pour le sujet principal et rétrogradez les autres en <h2>."},

    "heading.thin_no_h2": {"en": "{words} words with no <h2> subheadings", "ru": "{words} слов без подзаголовков <h2>", "fr": "{words} mots sans sous-titres <h2>"},
    "heading.thin_no_h2_fix": {"en": "Break long content into sections with <h2>/<h3> subheadings.", "ru": "Разбейте длинный текст на разделы с подзаголовками <h2>/<h3>.", "fr": "Découpez le contenu long en sections avec des sous-titres <h2>/<h3>."},
    "heading.ok": {"en": "h2={h2}, h3={h3} for {words} words", "ru": "h2={h2}, h3={h3} на {words} слов", "fr": "h2={h2}, h3={h3} pour {words} mots"},

    "canonical.missing": {"en": "No canonical link — duplicate URLs (query strings, trailing slashes, www/non-www) may split ranking signals", "ru": "Нет canonical-ссылки — дублирующиеся URL (параметры, слеш в конце, www/без www) могут распылять сигналы ранжирования", "fr": "Aucun lien canonique — les URL dupliquées (paramètres, barre oblique finale, www/non-www) peuvent diviser les signaux de classement"},
    "canonical.missing_fix": {"en": 'Add <link rel="canonical" href="https://your-domain/page"> pointing to the preferred URL.', "ru": 'Добавьте <link rel="canonical" href="https://ваш-домен/страница">, указывающий на предпочитаемый URL.', "fr": 'Ajoutez <link rel="canonical" href="https://votre-domaine/page"> pointant vers l\'URL préférée.'},
    "canonical.mismatch": {"en": "Canonical points to a different URL than the one served", "ru": "Canonical указывает на другой URL, отличный от текущего", "fr": "Le canonique pointe vers une URL différente de celle servie"},
    "canonical.mismatch_fix": {"en": "Confirm the canonical target is intentional; usually it should match the served URL.", "ru": "Убедитесь, что это сделано намеренно; обычно canonical должен совпадать с текущим URL.", "fr": "Vérifiez que la cible canonique est intentionnelle ; elle doit généralement correspondre à l'URL servie."},
    "canonical.self": {"en": "Self-referencing canonical", "ru": "Canonical указывает сам на себя", "fr": "Canonique auto-référent"},

    "og.all": {"en": "og:title, og:description, og:image present", "ru": "og:title, og:description, og:image присутствуют", "fr": "og:title, og:description, og:image présents"},
    "og.none": {"en": "No Open Graph tags — shared links show no preview card", "ru": "Нет тегов Open Graph — при расшаривании ссылки не будет превью", "fr": "Aucune balise Open Graph — les liens partagés n'affichent pas de carte d'aperçu"},
    "og.none_fix": {"en": "Add og:title, og:description and og:image (1200×630) so links render rich previews on social platforms.", "ru": "Добавьте og:title, og:description и og:image (1200×630), чтобы ссылки показывали превью в соцсетях.", "fr": "Ajoutez og:title, og:description et og:image (1200×630) pour que les liens affichent un aperçu enrichi sur les réseaux sociaux."},
    "og.partial": {"en": "Missing og:{missing}", "ru": "Отсутствует og:{missing}", "fr": "og:{missing} manquant"},
    "og.partial_fix": {"en": "Add the missing og:{missing} tag(s).", "ru": "Добавьте недостающие теги og:{missing}.", "fr": "Ajoutez la ou les balises og:{missing} manquantes."},

    "twitter.present": {"en": 'twitter:card="{value}"', "ru": 'twitter:card="{value}"', "fr": 'twitter:card="{value}"'},
    "twitter.missing": {"en": "No twitter:card meta tag", "ru": "Нет тега twitter:card", "fr": "Aucune balise meta twitter:card"},
    "twitter.missing_fix": {"en": 'Add <meta name="twitter:card" content="summary_large_image"> (falls back to OG tags for the rest).', "ru": 'Добавьте <meta name="twitter:card" content="summary_large_image"> (остальное подхватится из OG-тегов).', "fr": 'Ajoutez <meta name="twitter:card" content="summary_large_image"> (le reste utilise les balises OG).'},

    "sd.invalid": {"en": "A JSON-LD block failed to parse", "ru": "Не удалось разобрать блок JSON-LD", "fr": "Un bloc JSON-LD n'a pas pu être analysé"},
    "sd.invalid_fix": {"en": "Validate the JSON-LD with Google's Rich Results Test and fix the syntax error.", "ru": "Проверьте JSON-LD через Google Rich Results Test и исправьте синтаксическую ошибку.", "fr": "Validez le JSON-LD avec le Rich Results Test de Google et corrigez l'erreur de syntaxe."},
    "sd.present": {"en": "{n} JSON-LD block(s)", "ru": "{n} блок(ов) JSON-LD", "fr": "{n} bloc(s) JSON-LD"},
    "sd.missing": {"en": "No schema.org JSON-LD — no eligibility for rich results", "ru": "Нет микроразметки schema.org (JSON-LD) — страница не подходит для расширенных результатов поиска", "fr": "Aucun JSON-LD schema.org — la page n'est pas éligible aux résultats enrichis"},
    "sd.missing_fix": {"en": "Add JSON-LD for the page type (Organization/WebSite on the homepage; Product, Article, FAQ where relevant).", "ru": "Добавьте JSON-LD, соответствующий типу страницы (Organization/WebSite на главной; Product, Article, FAQ — где уместно).", "fr": "Ajoutez du JSON-LD adapté au type de page (Organization/WebSite sur l'accueil ; Product, Article, FAQ selon le cas)."},

    "favicon.present": {"en": "Present", "ru": "Есть", "fr": "Présent"},
    "favicon.missing": {"en": "No favicon link — browser tabs and search results show a blank icon", "ru": "Нет favicon — во вкладках браузера и в поиске будет пустая иконка", "fr": "Aucun favicon — les onglets du navigateur et les résultats de recherche affichent une icône vide"},
    "favicon.missing_fix": {"en": 'Add <link rel="icon" href="/favicon.ico"> plus an apple-touch-icon.', "ru": 'Добавьте <link rel="icon" href="/favicon.ico"> и apple-touch-icon.', "fr": 'Ajoutez <link rel="icon" href="/favicon.ico"> ainsi qu\'un apple-touch-icon.'},

    "robots.missing": {"en": "Not found ({code}) — crawlers assume everything is allowed", "ru": "Не найден ({code}) — краулеры считают, что всё разрешено", "fr": "Introuvable ({code}) — les robots supposent que tout est autorisé"},
    "robots.missing_fix": {"en": "Publish /robots.txt with at least a Sitemap: line and any Disallow rules you need.", "ru": "Опубликуйте /robots.txt хотя бы со строкой Sitemap: и нужными правилами Disallow.", "fr": "Publiez /robots.txt avec au moins une ligne Sitemap: et les règles Disallow nécessaires."},
    "robots.blocked_all": {"en": "robots.txt disallows the whole site for all crawlers", "ru": "robots.txt запрещает индексацию всего сайта для всех краулеров", "fr": "robots.txt interdit tout le site à tous les robots"},
    "robots.blocked_all_fix": {"en": "Remove or narrow the 'Disallow: /' rule under 'User-agent: *'.", "ru": "Удалите или сузьте правило 'Disallow: /' под 'User-agent: *'.", "fr": "Supprimez ou restreignez la règle 'Disallow: /' sous 'User-agent: *'."},
    "robots.ok_with_sitemap": {"en": "Present and declares a Sitemap", "ru": "Присутствует и указывает Sitemap", "fr": "Présent et déclare un Sitemap"},
    "robots.ok_no_sitemap": {"en": "Present (no Sitemap: line)", "ru": "Присутствует (без строки Sitemap:)", "fr": "Présent (sans ligne Sitemap:)"},

    "sitemap.missing": {"en": "Not found ({code})", "ru": "Не найден ({code})", "fr": "Introuvable ({code})"},
    "sitemap.missing_fix": {"en": "Generate /sitemap.xml listing canonical URLs and reference it from robots.txt and Search Console.", "ru": "Сгенерируйте /sitemap.xml со списком канонических URL и укажите его в robots.txt и Search Console.", "fr": "Générez /sitemap.xml listant les URL canoniques et référencez-le dans robots.txt et Search Console."},
    "sitemap.ok_urls": {"en": "Present with {n} <loc> entries", "ru": "Присутствует, {n} записей <loc>", "fr": "Présent avec {n} entrées <loc>"},
    "sitemap.ok_no_urls": {"en": "Present (no <loc> entries found — may be a sitemap index)", "ru": "Присутствует (записей <loc> не найдено — возможно, это индекс сайтмапов)", "fr": "Présent (aucune entrée <loc> trouvée — peut-être un index de sitemaps)"},

    "hreflang.some": {"en": "{n} alternate-language link(s)", "ru": "{n} ссылок на альтернативные языковые версии", "fr": "{n} lien(s) de langue alternative"},
    "hreflang.none": {"en": "No hreflang links (only needed for multilingual sites)", "ru": "Нет hreflang-ссылок (нужны только многоязычным сайтам)", "fr": "Aucun lien hreflang (nécessaire uniquement pour les sites multilingues)"},

    "summary": {"en": 'Title: "{title}"; {h1_state}', "ru": 'Заголовок: «{title}»; {h1_state}', "fr": 'Titre : « {title} » ; {h1_state}'},
    "summary.no_title": {"en": "missing", "ru": "отсутствует", "fr": "manquant"},
    "summary.h1_present": {"en": "H1 present", "ru": "H1 есть", "fr": "H1 présent"},
    "summary.h1_absent": {"en": "no H1", "ru": "нет H1", "fr": "pas de H1"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    """Weight lookups happen on the (translated) check text, so the weight
    table must be keyed the same way per language — otherwise scores would
    silently drift between languages as every lookup falls back to 1.0."""
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def _len_check(lang: str, key_prefix: str, check_key: str, value: Optional[str], lo: int, hi: int) -> Finding:
    check = _t(check_key, lang)
    unit = _t("unit.characters", lang)
    if not value:
        return Finding(check=check, status=Status.BAD, detail=_t("len.missing", lang), evidence=None, fix=_t(f"{key_prefix}.missing_fix", lang))
    n = len(value)
    evidence = _t("len.evidence", lang, value=value[:120], n=n, unit=unit)
    if n < lo:
        return Finding(check=check, status=Status.WARN, detail=_t("len.short", lang, n=n, unit=unit, lo=lo, hi=hi), evidence=evidence, fix=_t(f"{key_prefix}.short_fix", lang))
    if n > hi:
        return Finding(check=check, status=Status.WARN, detail=_t("len.long", lang, n=n, unit=unit, lo=lo, hi=hi), evidence=evidence, fix=_t(f"{key_prefix}.long_fix", lang))
    return Finding(check=check, status=Status.OK, detail=_t("len.ok", lang, n=n, unit=unit, lo=lo, hi=hi), evidence=evidence)


def _normalize(url: str) -> str:
    return url.rstrip("/").lower()


def collect_seo(facts: Dict[str, Any], robots: Optional[FetchResult], sitemap: Optional[FetchResult], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    findings: List[Finding] = []

    findings.append(_len_check(lang, "title", "check.title", facts.get("title"), 30, 60))
    findings.append(_len_check(lang, "meta", "check.meta_description", facts.get("metaDescription"), 70, 160))

    robots_meta = (facts.get("robotsMeta") or "").lower()
    if "noindex" in robots_meta:
        findings.append(Finding(check=_t("check.indexability", lang), status=Status.BAD, detail=_t("noindex", lang), evidence=f'robots="{facts.get("robotsMeta")}"', fix=_t("noindex_fix", lang)))
    else:
        evidence = f'robots="{facts.get("robotsMeta")}"' if facts.get("robotsMeta") else _t("no_robots_meta", lang)
        findings.append(Finding(check=_t("check.indexability", lang), status=Status.OK, detail=_t("indexable", lang), evidence=evidence))

    h1_count = facts.get("h1Count", 0)
    h1_texts = facts.get("h1Texts") or []
    if h1_count == 1:
        findings.append(Finding(check=_t("check.h1", lang), status=Status.OK, detail=_t("h1.one", lang), evidence=f'"{h1_texts[0][:100]}"' if h1_texts else None))
    elif h1_count == 0:
        findings.append(Finding(check=_t("check.h1", lang), status=Status.BAD, detail=_t("h1.zero", lang), fix=_t("h1.zero_fix", lang)))
    else:
        findings.append(Finding(check=_t("check.h1", lang), status=Status.WARN, detail=_t("h1.many", lang, n=h1_count), evidence="; ".join(f'"{t[:60]}"' for t in h1_texts), fix=_t("h1.many_fix", lang)))

    h2, h3, word_count = facts.get("h2Count", 0), facts.get("h3Count", 0), facts.get("wordCount", 0)
    if word_count > 300 and h2 == 0:
        findings.append(Finding(check=_t("check.heading_structure", lang), status=Status.WARN, detail=_t("heading.thin_no_h2", lang, words=word_count), evidence=f"h2={h2}, h3={h3}", fix=_t("heading.thin_no_h2_fix", lang)))
    else:
        findings.append(Finding(check=_t("check.heading_structure", lang), status=Status.OK, detail=_t("heading.ok", lang, h2=h2, h3=h3, words=word_count), evidence=f"h2={h2}, h3={h3}"))

    canonical = facts.get("canonical")
    page_url = facts.get("url") or ""
    if not canonical:
        findings.append(Finding(check=_t("check.canonical", lang), status=Status.WARN, detail=_t("canonical.missing", lang), fix=_t("canonical.missing_fix", lang)))
    elif _normalize(canonical) != _normalize(page_url):
        findings.append(Finding(check=_t("check.canonical", lang), status=Status.WARN, detail=_t("canonical.mismatch", lang), evidence=f"canonical={canonical}; page={page_url}", fix=_t("canonical.mismatch_fix", lang)))
    else:
        findings.append(Finding(check=_t("check.canonical", lang), status=Status.OK, detail=_t("canonical.self", lang), evidence=canonical))

    og = facts.get("og") or {}
    missing_og = [k for k in ("title", "description", "image") if not og.get(k)]
    if not missing_og:
        findings.append(Finding(check=_t("check.og", lang), status=Status.OK, detail=_t("og.all", lang), evidence=f'og:title="{(og.get("title") or "")[:60]}"'))
    elif len(missing_og) == 3:
        findings.append(Finding(check=_t("check.og", lang), status=Status.WARN, detail=_t("og.none", lang), fix=_t("og.none_fix", lang)))
    else:
        missing_str = ", og:".join(missing_og)
        findings.append(Finding(check=_t("check.og", lang), status=Status.WARN, detail=_t("og.partial", lang, missing=missing_str), evidence=f"present: {[k for k in og if og.get(k)]}", fix=_t("og.partial_fix", lang, missing=missing_str)))

    twitter = facts.get("twitterCard")
    findings.append(Finding(check=_t("check.twitter", lang), status=Status.OK if twitter else Status.WARN, detail=_t("twitter.present", lang, value=twitter) if twitter else _t("twitter.missing", lang), evidence=twitter, fix=None if twitter else _t("twitter.missing_fix", lang)))

    sd = facts.get("structuredData") or []
    if "invalid" in sd:
        findings.append(Finding(check=_t("check.structured_data", lang), status=Status.BAD, detail=_t("sd.invalid", lang), evidence=", ".join(sd), fix=_t("sd.invalid_fix", lang)))
    elif sd:
        findings.append(Finding(check=_t("check.structured_data", lang), status=Status.OK, detail=_t("sd.present", lang, n=len(sd)), evidence=", ".join(sd)))
    else:
        findings.append(Finding(check=_t("check.structured_data", lang), status=Status.WARN, detail=_t("sd.missing", lang), fix=_t("sd.missing_fix", lang)))

    findings.append(Finding(check=_t("check.favicon", lang), status=Status.OK if facts.get("favicon") else Status.WARN, detail=_t("favicon.present", lang) if facts.get("favicon") else _t("favicon.missing", lang), fix=None if facts.get("favicon") else _t("favicon.missing_fix", lang)))

    if robots is None or robots.status_code != 200:
        code = robots.status_code if robots else "request failed"
        findings.append(Finding(check=_t("check.robots_txt", lang), status=Status.WARN, detail=_t("robots.missing", lang, code=code), fix=_t("robots.missing_fix", lang)))
    else:
        body = robots.text.lower()
        blocked_all = "user-agent: *" in body and re.search(r"^disallow:\s*/\s*$", body, re.MULTILINE) is not None
        robots_excerpt = " | ".join(line.strip() for line in robots.text.splitlines() if line.strip())[:200]
        if blocked_all:
            findings.append(Finding(check=_t("check.robots_txt", lang), status=Status.BAD, detail=_t("robots.blocked_all", lang), evidence=robots_excerpt, fix=_t("robots.blocked_all_fix", lang)))
        else:
            has_sitemap_line = "sitemap:" in body
            findings.append(Finding(check=_t("check.robots_txt", lang), status=Status.OK, detail=_t("robots.ok_with_sitemap", lang) if has_sitemap_line else _t("robots.ok_no_sitemap", lang), evidence=robots_excerpt))

    if sitemap is None or sitemap.status_code != 200:
        code = sitemap.status_code if sitemap else "request failed"
        findings.append(Finding(check=_t("check.sitemap", lang), status=Status.WARN, detail=_t("sitemap.missing", lang, code=code), fix=_t("sitemap.missing_fix", lang)))
    else:
        url_count = sitemap.text.count("<loc>")
        findings.append(Finding(check=_t("check.sitemap", lang), status=Status.OK, detail=_t("sitemap.ok_urls", lang, n=url_count) if url_count else _t("sitemap.ok_no_urls", lang), evidence=f"{len(sitemap.text)} bytes"))

    hreflang = facts.get("hreflangCount", 0)
    findings.append(Finding(check=_t("check.hreflang", lang), status=Status.NA, detail=_t("hreflang.some", lang, n=hreflang) if hreflang else _t("hreflang.none", lang)))

    score = score_findings(findings, _weights_for(lang))
    h1_state = _t("summary.h1_present", lang) if h1_count else _t("summary.h1_absent", lang)
    summary = _t("summary", lang, title=facts.get("title") or _t("summary.no_title", lang), h1_state=h1_state)
    return Pillar(name=pillar_name("SEO", lang), score=score, summary=summary, findings=findings)
