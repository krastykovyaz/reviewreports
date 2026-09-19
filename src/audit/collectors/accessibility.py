from typing import Any, Dict, List

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import score_findings
from src.report.schema import Finding, Pillar, Status

_WEIGHT_KEYS = {"check.image_alt": 2, "check.form_labels": 2, "check.viewport": 1.5, "check.html_lang": 1.5, "check.landmarks": 1.5}

_M = {
    "check.landmarks": {"en": "Landmarks", "ru": "Ориентиры (landmarks)", "fr": "Points de repère"},
    "landmarks.ok": {"en": "{n} landmark region(s)", "ru": "{n} ориентир(ов) (landmark)", "fr": "{n} région(s) de repère"},
    "landmarks.bad": {"en": "No <header>/<nav>/<main>/<footer> or ARIA landmark roles — screen-reader users cannot jump between page regions", "ru": "Нет <header>/<nav>/<main>/<footer> или ARIA landmark-ролей — пользователи скринридеров не могут переходить между областями страницы", "fr": "Aucun <header>/<nav>/<main>/<footer> ni rôle ARIA landmark — les utilisateurs de lecteurs d'écran ne peuvent pas naviguer entre les régions de la page"},
    "landmarks.fix": {"en": "Wrap page regions in <header>, <nav>, <main>, <footer> (or role= equivalents).", "ru": "Оберните области страницы в <header>, <nav>, <main>, <footer> (или эквиваленты через role=).", "fr": "Enveloppez les régions de la page dans <header>, <nav>, <main>, <footer> (ou leurs équivalents role=)."},
    "check.image_alt": {"en": "Image alt text", "ru": "Alt-текст изображений", "fr": "Texte alternatif des images"},
    "check.button_labels": {"en": "Button labels", "ru": "Подписи кнопок", "fr": "Libellés des boutons"},
    "check.form_labels": {"en": "Form labels", "ru": "Подписи полей формы", "fr": "Libellés des champs de formulaire"},
    "check.link_text": {"en": "Link text", "ru": "Текст ссылок", "fr": "Texte des liens"},
    "check.viewport": {"en": "Viewport meta tag", "ru": "Meta-тег viewport", "fr": "Balise meta viewport"},
    "check.html_lang": {"en": "HTML lang attribute", "ru": "Атрибут lang у <html>", "fr": "Attribut lang de <html>"},
    "check.heading_order": {"en": "Heading order", "ru": "Порядок заголовков", "fr": "Ordre des titres"},
    "check.skip_link": {"en": "Skip link", "ru": "Ссылка «пропустить контент»", "fr": "Lien d'évitement"},
    "check.tab_order": {"en": "Tab order", "ru": "Порядок табуляции", "fr": "Ordre de tabulation"},
    "check.iframe_titles": {"en": "Iframe titles", "ru": "Заголовки iframe", "fr": "Titres des iframes"},
    "check.duplicate_ids": {"en": "Duplicate IDs", "ru": "Дублирующиеся ID", "fr": "Identifiants dupliqués"},
    "check.contrast": {"en": "Colour contrast", "ru": "Контрастность цвета", "fr": "Contraste des couleurs"},

    "unit.images": {"en": "images", "ru": "изображений", "fr": "images"},
    "unit.buttons": {"en": "buttons", "ru": "кнопок", "fr": "boutons"},
    "unit.form_fields": {"en": "form fields", "ru": "полей формы", "fr": "champs de formulaire"},
    "unit.links": {"en": "links", "ru": "ссылок", "fr": "liens"},

    "ratio.none_on_page": {"en": "No {unit} on the page", "ru": "На странице нет {unit}", "fr": "Aucun(e) {unit} sur la page"},
    "ratio.all_pass": {"en": "All {total} {unit} {passes}", "ru": "Все {total} {unit} {passes}", "fr": "Les {total} {unit} {passes}"},
    "ratio.some_fail": {"en": "{bad} of {total} {unit} {fails}", "ru": "{bad} из {total} {unit} {fails}", "fr": "{bad} sur {total} {unit} {fails}"},
    "ratio.evidence": {"en": "{bad}/{total} failing", "ru": "{bad}/{total} не проходят", "fr": "{bad}/{total} en échec"},

    "image_alt.passes": {"en": "have an alt attribute", "ru": "имеют alt-атрибут", "fr": "ont un attribut alt"},
    "image_alt.fails": {"en": "lack an alt attribute", "ru": "не имеют alt-атрибута", "fr": "n'ont pas d'attribut alt"},
    "image_alt.fix": {"en": 'Add alt text describing each informative image; use alt="" for purely decorative ones.', "ru": 'Добавьте alt-текст с описанием каждого информативного изображения; для декоративных используйте alt="".', "fr": 'Ajoutez un texte alt décrivant chaque image informative ; utilisez alt="" pour les images purement décoratives.'},

    "button_labels.passes": {"en": "have an accessible name", "ru": "имеют доступное имя", "fr": "ont un nom accessible"},
    "button_labels.fails": {"en": "have no accessible name", "ru": "не имеют доступного имени", "fr": "n'ont pas de nom accessible"},
    "button_labels.fix": {"en": 'Give icon-only buttons an aria-label (e.g. aria-label="Open menu").', "ru": 'Добавьте aria-label кнопкам без текста (например, aria-label="Открыть меню").', "fr": 'Donnez un aria-label aux boutons uniquement iconographiques (ex. aria-label="Ouvrir le menu").'},

    "form_labels.passes": {"en": "have a label", "ru": "имеют подпись", "fr": "ont un libellé"},
    "form_labels.fails": {"en": "have no associated label", "ru": "не имеют связанной подписи", "fr": "n'ont pas de libellé associé"},
    "form_labels.fix": {"en": "Associate every input with a <label for=…> or aria-label; placeholder text alone is not a label.", "ru": "Свяжите каждое поле ввода с <label for=…> или aria-label; placeholder сам по себе не является подписью.", "fr": "Associez chaque champ à un <label for=…> ou un aria-label ; le placeholder seul ne suffit pas."},

    "link_text.passes": {"en": "have accessible text", "ru": "имеют доступный текст", "fr": "ont un texte accessible"},
    "link_text.fails": {"en": "have no accessible text", "ru": "не имеют доступного текста", "fr": "n'ont pas de texte accessible"},
    "link_text.fix": {"en": "Give every link visible text or an aria-label; avoid empty or icon-only links.", "ru": "Дайте каждой ссылке видимый текст или aria-label; избегайте пустых или иконочных ссылок без подписи.", "fr": "Donnez à chaque lien un texte visible ou un aria-label ; évitez les liens vides ou uniquement iconographiques."},

    "viewport.missing": {"en": "Missing — the page is not responsive on mobile", "ru": "Отсутствует — страница не адаптивна на мобильных устройствах", "fr": "Manquant — la page n'est pas adaptative sur mobile"},
    "viewport.missing_fix": {"en": 'Add <meta name="viewport" content="width=device-width, initial-scale=1">.', "ru": 'Добавьте <meta name="viewport" content="width=device-width, initial-scale=1">.', "fr": 'Ajoutez <meta name="viewport" content="width=device-width, initial-scale=1">.'},
    "viewport.zoom_blocked": {"en": "Viewport disables pinch-zoom, which blocks low-vision users", "ru": "Viewport блокирует масштабирование жестами, что мешает слабовидящим пользователям", "fr": "Le viewport désactive le zoom, ce qui bloque les utilisateurs malvoyants"},
    "viewport.zoom_blocked_fix": {"en": "Remove user-scalable=no / maximum-scale=1 from the viewport meta tag.", "ru": "Удалите user-scalable=no / maximum-scale=1 из meta-тега viewport.", "fr": "Retirez user-scalable=no / maximum-scale=1 de la balise meta viewport."},
    "viewport.ok": {"en": "Responsive viewport, zoom allowed", "ru": "Адаптивный viewport, масштабирование разрешено", "fr": "Viewport adaptatif, zoom autorisé"},

    "lang.ok": {"en": 'lang="{value}"', "ru": 'lang="{value}"', "fr": 'lang="{value}"'},
    "lang.missing": {"en": "Missing — screen readers cannot pick the right pronunciation rules", "ru": "Отсутствует — программы экранного доступа не могут выбрать правильные правила произношения", "fr": "Manquant — les lecteurs d'écran ne peuvent pas choisir les bonnes règles de prononciation"},
    "lang.missing_fix": {"en": 'Add lang="en" (or the page language) to the <html> element.', "ru": 'Добавьте lang="ru" (или язык страницы) в элемент <html>.', "fr": 'Ajoutez lang="fr" (ou la langue de la page) à l\'élément <html>.'},

    "heading_order.ok": {"en": "No skipped heading levels", "ru": "Уровни заголовков не пропускаются", "fr": "Aucun niveau de titre sauté"},
    "heading_order.bad": {"en": "{n} place(s) where heading levels jump (e.g. h1 → h3)", "ru": "{n} мест(а), где уровни заголовков пропускаются (например, h1 → h3)", "fr": "{n} endroit(s) où les niveaux de titre sautent (ex. h1 → h3)"},
    "heading_order.fix": {"en": "Nest headings sequentially (h1 → h2 → h3) so the outline is navigable.", "ru": "Вкладывайте заголовки последовательно (h1 → h2 → h3), чтобы структура была навигируемой.", "fr": "Imbriquez les titres séquentiellement (h1 → h2 → h3) pour un plan navigable."},

    "skip_link.ok": {"en": "Skip-to-content link present", "ru": "Ссылка «перейти к содержимому» присутствует", "fr": "Lien d'évitement vers le contenu présent"},
    "skip_link.bad": {"en": "No skip-to-content link for keyboard users", "ru": "Нет ссылки «перейти к содержимому» для пользователей клавиатуры", "fr": "Aucun lien d'évitement pour les utilisateurs au clavier"},
    "skip_link.fix": {"en": 'Add a visually-hidden <a href="#main">Skip to content</a> as the first focusable element.', "ru": 'Добавьте визуально скрытую <a href="#main">Перейти к содержимому</a> как первый фокусируемый элемент.', "fr": 'Ajoutez un lien <a href="#main">Passer au contenu</a> visuellement masqué comme premier élément focalisable.'},

    "tab_order.ok": {"en": "No positive tabindex values", "ru": "Нет положительных значений tabindex", "fr": "Aucune valeur tabindex positive"},
    "tab_order.bad": {"en": "{n} element(s) with tabindex > 0, which overrides the natural tab order", "ru": "{n} элемент(ов) с tabindex > 0, что нарушает естественный порядок табуляции", "fr": "{n} élément(s) avec tabindex > 0, ce qui modifie l'ordre naturel de tabulation"},
    "tab_order.fix": {"en": 'Use tabindex="0" or -1 only; fix order in the DOM instead.', "ru": 'Используйте только tabindex="0" или -1; порядок исправляйте в DOM.', "fr": 'Utilisez uniquement tabindex="0" ou -1 ; corrigez plutôt l\'ordre dans le DOM.'},

    "iframe_titles.ok": {"en": "All iframes titled", "ru": "У всех iframe есть заголовок", "fr": "Toutes les iframes sont titrées"},
    "iframe_titles.bad": {"en": "{n} iframe(s) without a title attribute", "ru": "{n} iframe без атрибута title", "fr": "{n} iframe(s) sans attribut title"},
    "iframe_titles.fix": {"en": 'Add title="…" describing each iframe\'s content.', "ru": 'Добавьте title="…", описывающий содержимое каждого iframe.', "fr": 'Ajoutez title="…" décrivant le contenu de chaque iframe.'},

    "duplicate_ids.ok": {"en": "All element IDs unique", "ru": "Все ID элементов уникальны", "fr": "Tous les identifiants sont uniques"},
    "duplicate_ids.bad": {"en": "{n} duplicated id attribute(s) — breaks label/aria references", "ru": "{n} дублирующихся атрибутов id — нарушает связи label/aria", "fr": "{n} attribut(s) id dupliqué(s) — casse les références label/aria"},
    "duplicate_ids.fix": {"en": "Make every id attribute unique within the page.", "ru": "Сделайте каждый атрибут id уникальным в пределах страницы.", "fr": "Rendez chaque attribut id unique sur la page."},

    "contrast.na": {"en": "Not measured by this scan — run axe DevTools or Lighthouse for WCAG contrast ratios", "ru": "Не измеряется этим сканированием — используйте axe DevTools или Lighthouse для проверки контрастности по WCAG", "fr": "Non mesuré par cette analyse — utilisez axe DevTools ou Lighthouse pour les ratios de contraste WCAG"},

    "summary": {"en": "{issues} issue(s) across {checked} automated checks", "ru": "{issues} проблем(а) из {checked} автоматических проверок", "fr": "{issues} problème(s) sur {checked} contrôles automatisés"},
}

_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def _ratio_finding(lang: str, check_key: str, bad: int, total: int, unit_key: str, prefix: str, fix_key: str, critical: bool = False) -> Finding:
    check = _t(check_key, lang)
    unit = _t(unit_key, lang)
    if total == 0:
        return Finding(check=check, status=Status.NA, detail=_t("ratio.none_on_page", lang, unit=unit))
    if bad == 0:
        return Finding(check=check, status=Status.OK, detail=_t("ratio.all_pass", lang, total=total, unit=unit, passes=_t(f"{prefix}.passes", lang)), evidence=_t("ratio.evidence", lang, bad=0, total=total))
    status = Status.BAD if (critical and bad / total > 0.2) else Status.WARN
    return Finding(check=check, status=status, detail=_t("ratio.some_fail", lang, bad=bad, total=total, unit=unit, fails=_t(f"{prefix}.fails", lang)), evidence=_t("ratio.evidence", lang, bad=bad, total=total), fix=_t(fix_key, lang))


def collect_accessibility(facts: Dict[str, Any], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    findings: List[Finding] = []

    landmarks = facts.get("landmarks", 0)
    findings.append(Finding(check=_t("check.landmarks", lang), status=Status.OK if landmarks else Status.BAD, detail=_t("landmarks.ok", lang, n=landmarks) if landmarks else _t("landmarks.bad", lang), evidence=str(landmarks), fix=None if landmarks else _t("landmarks.fix", lang)))

    findings.append(_ratio_finding(lang, "check.image_alt", facts.get("imagesMissingAlt", 0), facts.get("totalImages", 0), "unit.images", "image_alt", "image_alt.fix", critical=True))
    findings.append(_ratio_finding(lang, "check.button_labels", facts.get("buttonsWithoutLabel", 0), facts.get("totalButtons", 0), "unit.buttons", "button_labels", "button_labels.fix"))
    findings.append(_ratio_finding(lang, "check.form_labels", facts.get("inputsWithoutLabel", 0), facts.get("totalInputs", 0), "unit.form_fields", "form_labels", "form_labels.fix", critical=True))
    findings.append(_ratio_finding(lang, "check.link_text", facts.get("linksWithoutText", 0), facts.get("totalLinks", 0), "unit.links", "link_text", "link_text.fix"))

    viewport = facts.get("viewport")
    zoom_blocked = bool(viewport and ("user-scalable=no" in viewport or "maximum-scale=1" in viewport.replace(" ", "")))
    if not viewport:
        findings.append(Finding(check=_t("check.viewport", lang), status=Status.BAD, detail=_t("viewport.missing", lang), fix=_t("viewport.missing_fix", lang)))
    elif zoom_blocked:
        findings.append(Finding(check=_t("check.viewport", lang), status=Status.WARN, detail=_t("viewport.zoom_blocked", lang), evidence=viewport, fix=_t("viewport.zoom_blocked_fix", lang)))
    else:
        findings.append(Finding(check=_t("check.viewport", lang), status=Status.OK, detail=_t("viewport.ok", lang), evidence=viewport))

    html_lang = facts.get("lang")
    findings.append(Finding(check=_t("check.html_lang", lang), status=Status.OK if html_lang else Status.BAD, detail=_t("lang.ok", lang, value=html_lang) if html_lang else _t("lang.missing", lang), evidence=html_lang, fix=None if html_lang else _t("lang.missing_fix", lang)))

    skips = facts.get("headingSkips", 0)
    findings.append(Finding(check=_t("check.heading_order", lang), status=Status.OK if skips == 0 else Status.WARN, detail=_t("heading_order.ok", lang) if skips == 0 else _t("heading_order.bad", lang, n=skips), evidence=f"skips={skips}", fix=None if skips == 0 else _t("heading_order.fix", lang)))

    findings.append(Finding(check=_t("check.skip_link", lang), status=Status.OK if facts.get("skipLink") else Status.WARN, detail=_t("skip_link.ok", lang) if facts.get("skipLink") else _t("skip_link.bad", lang), fix=None if facts.get("skipLink") else _t("skip_link.fix", lang)))

    tab = facts.get("positiveTabindex", 0)
    findings.append(Finding(check=_t("check.tab_order", lang), status=Status.OK if tab == 0 else Status.WARN, detail=_t("tab_order.ok", lang) if tab == 0 else _t("tab_order.bad", lang, n=tab), evidence=str(tab), fix=None if tab == 0 else _t("tab_order.fix", lang)))

    iframes = facts.get("iframesWithoutTitle", 0)
    findings.append(Finding(check=_t("check.iframe_titles", lang), status=Status.OK if iframes == 0 else Status.WARN, detail=_t("iframe_titles.ok", lang) if iframes == 0 else _t("iframe_titles.bad", lang, n=iframes), fix=None if iframes == 0 else _t("iframe_titles.fix", lang)))

    dup = facts.get("duplicateIds", 0)
    findings.append(Finding(check=_t("check.duplicate_ids", lang), status=Status.OK if dup == 0 else Status.WARN, detail=_t("duplicate_ids.ok", lang) if dup == 0 else _t("duplicate_ids.bad", lang, n=dup), evidence=str(dup), fix=None if dup == 0 else _t("duplicate_ids.fix", lang)))

    findings.append(Finding(check=_t("check.contrast", lang), status=Status.NA, detail=_t("contrast.na", lang)))

    score = score_findings(findings, _weights_for(lang))
    issues = sum(1 for f in findings if f.status in (Status.BAD, Status.WARN))
    checked = sum(1 for f in findings if f.status != Status.NA)
    return Pillar(name=pillar_name("Accessibility", lang), score=score, summary=_t("summary", lang, issues=issues, checked=checked), findings=findings)
