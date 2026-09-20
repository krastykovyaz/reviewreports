"""Translations for everything that is NOT collector-specific finding text:
report chrome (headings, status labels, verdicts), pillar/kind names, and the
web UI strings."""

from src.i18n.core import make_translator

PILLAR_NAMES = {
    "Security": {"en": "Security", "ru": "Безопасность", "fr": "Sécurité"},
    "SEO": {"en": "SEO", "ru": "SEO", "fr": "SEO"},
    "Accessibility": {"en": "Accessibility", "ru": "Доступность", "fr": "Accessibilité"},
    "Performance": {"en": "Performance", "ru": "Производительность", "fr": "Performance"},
    "Content & Copy": {"en": "Content & Copy", "ru": "Контент и тексты", "fr": "Contenu et rédaction"},
    "Privacy & Compliance": {"en": "Privacy & Compliance", "ru": "Конфиденциальность и соответствие", "fr": "Confidentialité et conformité"},
    "Links": {"en": "Links", "ru": "Ссылки", "fr": "Liens"},
    "Tech Stack": {"en": "Tech Stack", "ru": "Технологический стек", "fr": "Pile technologique"},
    "Design & UX": {"en": "Design & UX", "ru": "Дизайн и UX", "fr": "Design et UX"},
    "Repository Structure": {"en": "Repository Structure", "ru": "Структура репозитория", "fr": "Structure du dépôt"},
    "Code Quality": {"en": "Code Quality", "ru": "Качество кода", "fr": "Qualité du code"},
    "Document Stats": {"en": "Document Stats", "ru": "Статистика документа", "fr": "Statistiques du document"},
    "Resume Review": {"en": "Resume Review", "ru": "Резюме: отзыв", "fr": "CV : critique"},
    "Presentation Review": {"en": "Presentation Review", "ru": "Презентация: отзыв", "fr": "Présentation : critique"},
    "Book/Manuscript Review": {"en": "Book/Manuscript Review", "ru": "Книга/рукопись: отзыв", "fr": "Livre/manuscrit : critique"},
}

FLAVOR_LABELS = {
    "resume": {"en": "Resume", "ru": "Резюме", "fr": "CV"},
    "presentation": {"en": "Presentation", "ru": "Презентация", "fr": "Présentation"},
    "book": {"en": "Book/Manuscript", "ru": "Книга/рукопись", "fr": "Livre/manuscrit"},
}

KIND_TITLES = {
    "website_audit": {"en": "Website Audit Report", "ru": "Отчёт об аудите сайта", "fr": "Rapport d'audit de site"},
    "code_review": {"en": "Code Review Report", "ru": "Отчёт о ревью кода", "fr": "Rapport de revue de code"},
    "resume_review": {"en": "Resume Review Report", "ru": "Отчёт о проверке резюме", "fr": "Rapport de révision du CV"},
    "presentation_review": {"en": "Presentation Review Report", "ru": "Отчёт о проверке презентации", "fr": "Rapport de révision de la présentation"},
    "book_review": {"en": "Book Review Report", "ru": "Отчёт о рецензии книги", "fr": "Rapport de critique du livre"},
}

_CHROME = {
    "status.ok": {"en": "OK", "ru": "ОК", "fr": "OK"},
    "status.warn": {"en": "Warning", "ru": "Предупреждение", "fr": "Avertissement"},
    "status.bad": {"en": "Missing", "ru": "Критично", "fr": "Manquant"},
    "status.na": {"en": "N/A", "ru": "Н/Д", "fr": "N/A"},

    "report.subject": {"en": "Subject", "ru": "Объект проверки", "fr": "Sujet"},
    "report.generated": {"en": "Generated", "ru": "Сформировано", "fr": "Généré"},
    "report.executive_summary": {"en": "Executive summary", "ru": "Краткое резюме", "fr": "Résumé exécutif"},
    "report.overall": {"en": "Overall: {score} (grade {grade})", "ru": "Итоговая оценка: {score} (уровень {grade})", "fr": "Score global : {score} (note {grade})"},
    "report.counts": {
        "en": "{bad} critical issue(s), {warn} warning(s), {ok} check(s) passed, {na} informational.",
        "ru": "{bad} критичных проблем, {warn} предупреждений, {ok} успешных проверок, {na} информационных.",
        "fr": "{bad} problème(s) critique(s), {warn} avertissement(s), {ok} contrôle(s) réussi(s), {na} information(s).",
    },
    "report.top_priorities": {"en": "Top priorities", "ru": "Первоочередные задачи", "fr": "Priorités principales"},
    "report.scorecard": {"en": "Scorecard", "ru": "Сводная таблица", "fr": "Tableau de bord"},
    "report.recommendations": {"en": "Recommendations", "ru": "Рекомендации", "fr": "Recommandations"},
    "report.methodology": {"en": "Methodology", "ru": "Методология", "fr": "Méthodologie"},
    "report.limitations": {"en": "Limitations", "ru": "Ограничения", "fr": "Limites"},
    "report.evidence": {"en": "Evidence", "ru": "Данные", "fr": "Preuve"},
    "report.fix": {"en": "Fix", "ru": "Как исправить", "fr": "Solution"},
    "report.col_pillar": {"en": "Pillar", "ru": "Раздел", "fr": "Pilier"},
    "report.col_score": {"en": "Score", "ru": "Оценка", "fr": "Score"},
    "report.col_summary": {"en": "Summary", "ru": "Итог", "fr": "Résumé"},
    "report.col_status": {"en": "Status", "ru": "Статус", "fr": "Statut"},
    "report.col_check": {"en": "Check", "ru": "Проверка", "fr": "Contrôle"},
    "report.col_finding": {"en": "Finding", "ru": "Результат", "fr": "Constat"},
    "report.na_value": {"en": "N/A", "ru": "Н/Д", "fr": "N/D"},

    "verdict.strong": {"en": "Strong health across the scanned pillars.", "ru": "Отличные показатели по всем проверенным разделам.", "fr": "Excellents résultats sur tous les piliers analysés."},
    "verdict.solid": {"en": "Solid foundation with meaningful gaps to close.", "ru": "Хорошая основа, но есть заметные пробелы.", "fr": "Bonne base, mais des lacunes importantes à combler."},
    "verdict.poor": {"en": "Multiple significant issues found across scanned pillars.", "ru": "Обнаружено множество серьёзных проблем по разным разделам.", "fr": "De nombreux problèmes importants ont été détectés."},
    "verdict.unknown": {"en": "Unable to compute an overall score.", "ru": "Не удалось рассчитать итоговую оценку.", "fr": "Impossible de calculer un score global."},

    "recommendation.fix": {"en": "Fix", "ru": "Исправить", "fr": "Corriger"},
    "recommendation.improve": {"en": "Improve", "ru": "Улучшить", "fr": "Améliorer"},

    "ui.app_title": {"en": "reviewreports", "ru": "reviewreports", "fr": "reviewreports"},
    "ui.tagline": {"en": "Submit a URL, a repository, or a document and get a structured review report.", "ru": "Отправьте ссылку, репозиторий или документ и получите структурированный отчёт проверки.", "fr": "Soumettez une URL, un dépôt ou un document et obtenez un rapport de revue structuré."},
    "ui.language": {"en": "Language", "ru": "Язык", "fr": "Langue"},
    "ui.report_kind": {"en": "Report kind", "ru": "Тип отчёта", "fr": "Type de rapport"},
    "ui.url_or_repo": {"en": "URL or repository (path or git URL)", "ru": "URL или репозиторий (путь или git-адрес)", "fr": "URL ou dépôt (chemin ou URL git)"},
    "ui.file": {"en": "File", "ru": "Файл", "fr": "Fichier"},
    "ui.model_optional": {"en": "Model (optional — enables the LLM judgment pillars)", "ru": "Модель (необязательно — включает разделы с оценкой ИИ)", "fr": "Modèle (facultatif — active les piliers évalués par IA)"},
    "ui.generate": {"en": "Generate report", "ru": "Сформировать отчёт", "fr": "Générer le rapport"},
    "ui.new_report": {"en": "New report", "ru": "Новый отчёт", "fr": "Nouveau rapport"},
    "ui.generating_title": {"en": "Generating your report…", "ru": "Формируем ваш отчёт…", "fr": "Génération de votre rapport…"},
    "ui.status": {"en": "Status", "ru": "Статус", "fr": "Statut"},
    "ui.subject": {"en": "Subject", "ru": "Объект", "fr": "Sujet"},
    "ui.refresh_note": {"en": "This page refreshes automatically every 2 seconds.", "ru": "Страница обновляется автоматически каждые 2 секунды.", "fr": "Cette page s'actualise automatiquement toutes les 2 secondes."},
    "ui.failed_title": {"en": "Report generation failed", "ru": "Не удалось сформировать отчёт", "fr": "Échec de la génération du rapport"},
    "ui.ready_title": {"en": "Report ready", "ru": "Отчёт готов", "fr": "Rapport prêt"},
    "ui.download_pdf": {"en": "Download PDF", "ru": "Скачать PDF", "fr": "Télécharger le PDF"},
    "ui.download_md": {"en": "Markdown", "ru": "Markdown", "fr": "Markdown"},
    "ui.download_html": {"en": "HTML", "ru": "HTML", "fr": "HTML"},
    "ui.download_tex": {"en": "LaTeX", "ru": "LaTeX", "fr": "LaTeX"},
    "ui.fix_prompt": {"en": "Copy fix prompt", "ru": "Скопировать промпт для исправлений", "fr": "Copier le prompt de correction"},
    "ui.copied": {"en": "Copied", "ru": "Скопировано", "fr": "Copié"},
    "ui.fix_prompt_header": {"en": "Fix these issues found on", "ru": "Исправь эти проблемы, найденные на", "fr": "Corrige ces problèmes trouvés sur"},
    "ui.kind.website_audit": {"en": "Website audit (URL)", "ru": "Аудит сайта (URL)", "fr": "Audit de site (URL)"},
    "ui.kind.code_review": {"en": "Code review (repo path or git URL)", "ru": "Ревью кода (путь или git-адрес)", "fr": "Revue de code (chemin ou URL git)"},
    "ui.kind.resume_review": {"en": "Resume review (upload file)", "ru": "Проверка резюме (загрузить файл)", "fr": "Révision de CV (importer un fichier)"},
    "ui.kind.presentation_review": {"en": "Presentation review (upload file)", "ru": "Проверка презентации (загрузить файл)", "fr": "Révision de présentation (importer un fichier)"},
    "ui.kind.book_review": {"en": "Book / manuscript review (upload file)", "ru": "Рецензия книги / рукописи (загрузить файл)", "fr": "Critique de livre / manuscrit (importer un fichier)"},
}

t_chrome = make_translator(_CHROME)


def pillar_name(canonical_name: str, lang: str) -> str:
    entry = PILLAR_NAMES.get(canonical_name)
    if entry is None:
        return canonical_name
    return entry.get(lang) or entry.get("en") or canonical_name


def flavor_label(flavor: str, lang: str) -> str:
    entry = FLAVOR_LABELS.get(flavor, {})
    return entry.get(lang) or entry.get("en") or flavor.title()


def kind_title(kind: str, lang: str) -> str:
    entry = KIND_TITLES.get(kind)
    if entry is None:
        return kind.replace("_", " ").title() + " Report"
    return entry.get(lang) or entry.get("en")
