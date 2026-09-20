"""App review: a small generated application's source directory in (static
site, Flask, or FastAPI — the exact shapes an AI app-generator like tsech
produces), one structured Report out.

Deliberately NOT code_review: code_review's structure pillar scores on
README/LICENSE/tests/CI presence, which is the right bar for an OSS repo
but unfairly tanks a small single-purpose generated app that will never
have those. This checks what actually matters for a generated app instead:
does it look runnable (detected stack + entry point), and does it avoid
the concrete security smells a code-generating LLM can introduce
(hardcoded secrets, debug mode left on, wildcard CORS, a committed .env).
"""

import os
import re
from typing import Dict, List, Optional

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.report.helpers import build_recommendations, score_findings, verdict_for
from src.report.schema import Finding, Pillar, Report, ReportMeta, Status
from src.review._code_sampling import (
    MAX_FILES_SAMPLED as _MAX_FILES_SAMPLED,
    llm_review_code,
    sample_source,
    walk_files,
)

_APP_REVIEW_PERSONA = (
    "You are a security-conscious software engineer reviewing a small, freshly generated web "
    "application (static site, Flask, or FastAPI). It is meant to be minimal, not an enterprise "
    "codebase — do not penalize it for lacking tests, CI, or documentation. Focus on correctness, "
    "security, and whether the code does what it appears to be trying to do."
)

_WEIGHT_KEYS = {"check.entry_point": 2, "check.hardcoded_secrets": 3, "check.debug_mode": 2, "check.cors_wildcard": 1.5, "check.env_file": 1.5}

_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIzaSy[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{12,}"),
    re.compile(r"(?:API_KEY|SECRET|PASSWORD|TOKEN)\s*[:=]\s*['\"][A-Za-z0-9/+_-]{12,}['\"]", re.IGNORECASE),
]
_PLACEHOLDER_RE = re.compile(r"xxx|your[-_]|changeme|<[^>]+>|example|placeholder|\$\{", re.IGNORECASE)
_TEXT_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".json", ".yml", ".yaml", ".env", ".txt", ".md", ".cfg", ".ini"}
_MAX_SCAN_BYTES = 200_000

_M = {
    "check.stack": {"en": "Detected stack", "ru": "Определённый стек", "fr": "Stack détectée"},
    "check.entry_point": {"en": "Entry point", "ru": "Точка входа", "fr": "Point d'entrée"},
    "check.file_count": {"en": "File count", "ru": "Количество файлов", "fr": "Nombre de fichiers"},
    "check.hardcoded_secrets": {"en": "Hardcoded secrets", "ru": "Захардкоженные секреты", "fr": "Secrets codés en dur"},
    "check.debug_mode": {"en": "Debug mode", "ru": "Режим отладки", "fr": "Mode debug"},
    "check.cors_wildcard": {"en": "CORS policy", "ru": "Политика CORS", "fr": "Politique CORS"},
    "check.env_file": {"en": "Committed .env file", "ru": "Файл .env в проекте", "fr": "Fichier .env inclus"},
    "check.llm_code_review": {"en": "LLM-based code review", "ru": "Оценка кода (ИИ)", "fr": "Revue de code par IA"},

    "stack.static": {"en": "Static site (index.html)", "ru": "Статический сайт (index.html)", "fr": "Site statique (index.html)"},
    "stack.flask": {"en": "Flask app", "ru": "Приложение на Flask", "fr": "Application Flask"},
    "stack.fastapi": {"en": "FastAPI app", "ru": "Приложение на FastAPI", "fr": "Application FastAPI"},
    "stack.unknown": {"en": "Could not determine the stack (no index.html, no Flask/FastAPI import found)", "ru": "Не удалось определить стек (нет index.html, нет импорта Flask/FastAPI)", "fr": "Impossible de déterminer la stack (pas d'index.html, pas d'import Flask/FastAPI détecté)"},

    "entry.ok": {"en": "Found: {file}", "ru": "Найдена: {file}", "fr": "Trouvé : {file}"},
    "entry.missing_static": {"en": "No index.html found — a static site needs one at its root", "ru": "Не найден index.html — для статического сайта он обязателен в корне", "fr": "Aucun index.html trouvé — un site statique doit en avoir un à la racine"},
    "entry.missing_server": {"en": "No obvious entry file (app.py/main.py/server.py) found for a {stack} app", "ru": "Не найден очевидный файл запуска (app.py/main.py/server.py) для приложения на {stack}", "fr": "Aucun fichier d'entrée évident (app.py/main.py/server.py) trouvé pour une application {stack}"},
    "entry.unknown_stack": {"en": "Stack unknown — cannot check for an entry point", "ru": "Стек не определён — невозможно проверить точку входа", "fr": "Stack inconnue — impossible de vérifier le point d'entrée"},

    "files.detail": {"en": "{n} file(s) in the project", "ru": "{n} файл(ов) в проекте", "fr": "{n} fichier(s) dans le projet"},
    "files.empty": {"en": "The project directory is empty", "ru": "Директория проекта пуста", "fr": "Le répertoire du projet est vide"},

    "secrets.none": {"en": "No hardcoded API keys, tokens, or passwords found", "ru": "Захардкоженных API-ключей, токенов или паролей не найдено", "fr": "Aucune clé API, jeton ou mot de passe codé en dur trouvé"},
    "secrets.found": {"en": "{n} likely hardcoded secret(s) found: {files}", "ru": "Найдено {n} вероятных захардкоженных секрета(ов): {files}", "fr": "{n} secret(s) probablement codé(s) en dur trouvé(s) : {files}"},
    "secrets.fix": {"en": "Move all keys/tokens/passwords to environment variables; never commit real credentials into generated source.", "ru": "Перенесите все ключи/токены/пароли в переменные окружения; никогда не включайте реальные учётные данные в исходный код.", "fr": "Déplacez toutes les clés/jetons/mots de passe dans des variables d'environnement ; ne jamais inclure de vrais identifiants dans le code source."},

    "debug.off": {"en": "No debug=True found in run configuration", "ru": "В конфигурации запуска не найдено debug=True", "fr": "Aucun debug=True trouvé dans la configuration de lancement"},
    "debug.on": {"en": "debug=True found in {file} — must not run in production", "ru": "В {file} найдено debug=True — недопустимо для продакшена", "fr": "debug=True trouvé dans {file} — ne doit pas être utilisé en production"},
    "debug.fix": {"en": "Set debug=False (or drive it from an environment variable defaulting to False) before deploying.", "ru": "Установите debug=False (или управляйте этим через переменную окружения со значением по умолчанию False) перед развёртыванием.", "fr": "Définissez debug=False (ou pilotez-le via une variable d'environnement dont la valeur par défaut est False) avant le déploiement."},

    "cors.ok": {"en": "No wildcard CORS origin found", "ru": "Не найдено CORS-политики с wildcard-источником", "fr": "Aucune origine CORS avec caractère générique trouvée"},
    "cors.wildcard": {"en": "Wildcard CORS origin (\"*\") found in {file}", "ru": "В {file} найдена CORS-политика с wildcard-источником («*»)", "fr": "Origine CORS avec caractère générique (\"*\") trouvée dans {file}"},
    "cors.fix": {"en": "Restrict allow_origins to the actual domain(s) that need access, not \"*\".", "ru": "Ограничьте allow_origins конкретными доменами, которым реально нужен доступ, вместо «*».", "fr": "Limitez allow_origins aux domaines qui ont réellement besoin d'accès, pas à \"*\"."},

    "env.absent": {"en": "No .env file in the project", "ru": "В проекте нет файла .env", "fr": "Aucun fichier .env dans le projet"},
    "env.present": {"en": "A .env file is included in the generated output", "ru": "В сгенерированном проекте есть файл .env", "fr": "Un fichier .env est inclus dans le résultat généré"},
    "env.fix": {"en": "Generated apps should not ship a .env file — provide a .env.example instead and let the deployer supply real values.", "ru": "Сгенерированные приложения не должны включать файл .env — вместо этого предоставьте .env.example, а реальные значения задаст тот, кто разворачивает приложение.", "fr": "Les applications générées ne devraient pas inclure de fichier .env — fournissez plutôt un .env.example et laissez la personne qui déploie renseigner les vraies valeurs."},

    "structure_summary": {"en": "{stack}; {n} files", "ru": "{stack}; файлов: {n}", "fr": "{stack} ; {n} fichiers"},
    "security_summary": {"en": "{issues} issue(s) found", "ru": "Найдено проблем: {issues}", "fr": "{issues} problème(s) trouvé(s)"},

    "quality.na_no_model": {"en": "No model_name configured for this scan", "ru": "Для этого сканирования не настроена модель (model_name)", "fr": "Aucun model_name configuré pour cette analyse"},
    "quality.na_no_model_summary": {"en": "Not evaluated — requires a configured LLM", "ru": "Не оценено — требуется настроенная модель", "fr": "Non évalué — nécessite un modèle IA configuré"},
    "quality.na_no_files": {"en": "No files matched known code extensions", "ru": "Ни один файл не соответствует известным расширениям кода", "fr": "Aucun fichier ne correspond aux extensions de code connues"},
    "quality.na_no_files_summary": {"en": "No recognized source files to review", "ru": "Нет распознаваемых исходных файлов для проверки", "fr": "Aucun fichier source reconnu à analyser"},
    "quality.na_failed": {"en": "LLM review failed (model: {model})", "ru": "Оценка ИИ не удалась (модель: {model})", "fr": "L'analyse par IA a échoué (modèle : {model})"},
    "quality.na_failed_summary": {"en": "LLM review failed or returned no result", "ru": "Оценка ИИ не удалась или не вернула результат", "fr": "L'analyse par IA a échoué ou n'a renvoyé aucun résultat"},
    "quality.issues_suffix": {"en": " Issues: {issues}", "ru": " Проблемы: {issues}", "fr": " Problèmes : {issues}"},
    "quality.summary": {"en": "Quality {n}/10", "ru": "Качество {n}/10", "fr": "Qualité {n}/10"},

    "methodology": {"en": "Stack detection and entry-point check, a source-code scan for hardcoded secrets/debug mode/wildcard CORS/a committed .env, plus an LLM code-quality review of the sampled source.", "ru": "Определение стека и проверка точки входа, сканирование исходного кода на захардкоженные секреты/режим отладки/wildcard CORS/файл .env, а также оценка качества кода ИИ.", "fr": "Détection de la stack et vérification du point d'entrée, analyse du code source pour les secrets codés en dur/mode debug/CORS générique/fichier .env inclus, ainsi qu'une revue de qualité du code par IA."},
    "limitations": {"en": "The secret/debug/CORS scan is pattern-based, not a full static analyzer — it can miss obfuscated cases and occasionally flag a false positive. Only the sampled source (up to {max_files} largest files) was reviewed by the LLM.", "ru": "Сканирование секретов/debug/CORS основано на шаблонах, а не на полноценном статическом анализе — оно может пропустить замаскированные случаи и иногда даёт ложные срабатывания. ИИ проверил только выборку исходного кода (до {max_files} самых крупных файлов).", "fr": "L'analyse des secrets/debug/CORS repose sur des motifs, pas sur une analyse statique complète — elle peut manquer des cas dissimulés et donner occasionnellement un faux positif. Seul un échantillon du code source (jusqu'à {max_files} plus gros fichiers) a été analysé par l'IA."},
    "limitations.no_model_suffix": {"en": " No model_name was configured, so Code Quality was not evaluated.", "ru": " Модель (model_name) не была настроена, поэтому раздел «Качество кода» не оценивался.", "fr": " Aucun model_name n'a été configuré, le pilier Qualité du code n'a donc pas été évalué."},
}
_t = make_translator(_M)


def _weights_for(lang: str) -> Dict[str, float]:
    return {_t(key, lang): weight for key, weight in _WEIGHT_KEYS.items()}


def _detect_stack(root: str, files: List[str]) -> str:
    rel_names = {os.path.relpath(f, root).replace("\\", "/").lower() for f in files}
    py_files = [f for f in files if f.lower().endswith(".py")]

    for f in py_files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(20_000)
        except OSError:
            continue
        if re.search(r"\bfrom\s+fastapi\s+import|\bimport\s+fastapi\b|FastAPI\s*\(", content):
            return "fastapi"
        if re.search(r"\bfrom\s+flask\s+import|\bimport\s+flask\b|Flask\s*\(\s*__name__", content):
            return "flask"

    if "index.html" in rel_names:
        return "static"
    return "unknown"


def _find_entry_file(root: str, files: List[str], stack: str) -> Optional[str]:
    rel_names = {os.path.relpath(f, root).replace("\\", "/") for f in files}
    if stack == "static":
        return "index.html" if "index.html" in {n.lower() for n in rel_names} else None
    if stack in ("flask", "fastapi"):
        for candidate in ("app.py", "main.py", "server.py"):
            if candidate in rel_names:
                return candidate
        return None
    return None


def collect_app_structure(root: str, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    files = walk_files(root)
    stack = _detect_stack(root, files)
    stack_label = _t(f"stack.{stack}", lang)

    findings = [Finding(check=_t("check.stack", lang), status=Status.NA, detail=stack_label)]

    if stack == "unknown":
        findings.append(Finding(check=_t("check.entry_point", lang), status=Status.NA, detail=_t("entry.unknown_stack", lang)))
    else:
        entry = _find_entry_file(root, files, stack)
        if entry:
            findings.append(Finding(check=_t("check.entry_point", lang), status=Status.OK, detail=_t("entry.ok", lang, file=entry)))
        elif stack == "static":
            findings.append(Finding(check=_t("check.entry_point", lang), status=Status.BAD, detail=_t("entry.missing_static", lang)))
        else:
            findings.append(Finding(check=_t("check.entry_point", lang), status=Status.BAD, detail=_t("entry.missing_server", lang, stack=stack_label)))

    if files:
        findings.append(Finding(check=_t("check.file_count", lang), status=Status.NA, detail=_t("files.detail", lang, n=len(files))))
    else:
        findings.append(Finding(check=_t("check.file_count", lang), status=Status.BAD, detail=_t("files.empty", lang)))

    score = score_findings(findings, _weights_for(lang))
    summary = _t("structure_summary", lang, stack=stack_label, n=len(files))
    return Pillar(name=pillar_name("App Structure", lang), score=score, summary=summary, findings=findings)


def _scan_text_files(root: str, files: List[str]) -> Dict[str, str]:
    """path -> content, for text files under the byte cap, skipping obvious binaries."""
    contents = {}
    for f in files:
        if os.path.splitext(f)[1].lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            if os.path.getsize(f) > _MAX_SCAN_BYTES:
                continue
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                contents[os.path.relpath(f, root)] = fh.read()
        except OSError:
            continue
    return contents


def collect_security_hygiene(root: str, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    files = walk_files(root)
    contents = _scan_text_files(root, files)
    findings: List[Finding] = []

    secret_files = []
    for path, text in contents.items():
        for pattern in _SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if not _PLACEHOLDER_RE.search(match.group(0)):
                    secret_files.append(path)
                    break
            else:
                continue
            break
    secret_files = sorted(set(secret_files))
    if secret_files:
        findings.append(Finding(check=_t("check.hardcoded_secrets", lang), status=Status.BAD, detail=_t("secrets.found", lang, n=len(secret_files), files=", ".join(secret_files[:5])), evidence=", ".join(secret_files), fix=_t("secrets.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.hardcoded_secrets", lang), status=Status.OK, detail=_t("secrets.none", lang)))

    debug_file = None
    for path, text in contents.items():
        if path.lower().endswith(".py") and re.search(r"debug\s*=\s*True", text):
            debug_file = path
            break
    if debug_file:
        findings.append(Finding(check=_t("check.debug_mode", lang), status=Status.BAD, detail=_t("debug.on", lang, file=debug_file), fix=_t("debug.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.debug_mode", lang), status=Status.OK, detail=_t("debug.off", lang)))

    cors_file = None
    for path, text in contents.items():
        if path.lower().endswith(".py") and re.search(r"allow_origins\s*=\s*\[\s*['\"]\*['\"]\s*\]", text):
            cors_file = path
            break
    if cors_file:
        findings.append(Finding(check=_t("check.cors_wildcard", lang), status=Status.WARN, detail=_t("cors.wildcard", lang, file=cors_file), fix=_t("cors.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.cors_wildcard", lang), status=Status.OK, detail=_t("cors.ok", lang)))

    has_env = any(os.path.basename(f) == ".env" for f in files)
    if has_env:
        findings.append(Finding(check=_t("check.env_file", lang), status=Status.WARN, detail=_t("env.present", lang), fix=_t("env.fix", lang)))
    else:
        findings.append(Finding(check=_t("check.env_file", lang), status=Status.OK, detail=_t("env.absent", lang)))

    score = score_findings(findings, _weights_for(lang))
    issues = sum(1 for f in findings if f.status in (Status.BAD, Status.WARN))
    summary = _t("security_summary", lang, issues=issues)
    return Pillar(name=pillar_name("Security Hygiene", lang), score=score, summary=summary, findings=findings)


async def collect_code_quality(root: str, model_name: Optional[str], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    name = pillar_name("Code Quality", lang)
    check = _t("check.llm_code_review", lang)

    if not model_name:
        return Pillar(name=name, score=None, summary=_t("quality.na_no_model_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_no_model", lang))])

    sample = sample_source(root)
    if not sample.strip():
        return Pillar(name=name, score=None, summary=_t("quality.na_no_files_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_no_files", lang))])

    review = await llm_review_code(sample, model_name, lang, _APP_REVIEW_PERSONA)
    if review is None:
        return Pillar(name=name, score=None, summary=_t("quality.na_failed_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_failed", lang, model=model_name))])

    status = Status.OK if review.code_quality >= 7 else Status.WARN if review.code_quality >= 4 else Status.BAD
    detail = review.summary
    if review.issues:
        detail += _t("quality.issues_suffix", lang, issues="; ".join(review.issues))
    return Pillar(name=name, score=float(review.code_quality), summary=_t("quality.summary", lang, n=review.code_quality), findings=[Finding(check=check, status=status, detail=detail)])


async def run_app_review(input: str, model_name: Optional[str] = None, lang: str = "en") -> Report:
    """Review a generated app's source directory. `input` is a local directory path."""
    lang = normalize_lang(lang)
    if not os.path.isdir(input):
        raise ValueError(f"Not a directory: {input}")

    pillars = [
        collect_app_structure(input, lang=lang),
        collect_security_hygiene(input, lang=lang),
        await collect_code_quality(input, model_name, lang=lang),
    ]

    limitations = _t("limitations", lang, max_files=_MAX_FILES_SAMPLED)
    if not model_name:
        limitations += _t("limitations.no_model_suffix", lang)

    report = Report(
        meta=ReportMeta(kind="app_review", subject=input, lang=lang),
        pillars=pillars,
        recommendations=build_recommendations(pillars, lang=lang),
        methodology=_t("methodology", lang),
        limitations=limitations,
    ).finalize()
    report.verdict = verdict_for(report.overall_score, lang=lang)
    return report
