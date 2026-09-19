"""Code review: a repo (local directory or git URL) in, one structured Report out.

Mirrors src/audit/website.py's shape (deterministic pillar + LLM judgment
pillar, same Report/recommendation helpers) but for a codebase instead of a
live URL. Scope: reviews the repository's current state as a whole, not a
specific PR diff — reviewing an actual PR requires GitHub API access to fetch
the diff, which is a follow-up, not implemented here.
"""

import asyncio
import os
import shutil
import tempfile
from collections import Counter
from typing import List, Optional

from pydantic import BaseModel, Field

from src.i18n import normalize_lang, pillar_name
from src.i18n.core import make_translator
from src.logger import logger
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager
from src.report.helpers import build_recommendations, verdict_for
from src.report.schema import Finding, Pillar, Report, ReportMeta, Status

_IGNORED_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", ".idea", ".vscode",
}
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".cs", ".php", ".swift", ".kt",
}
_MAX_SAMPLE_CHARS = 8000
_MAX_FILES_SAMPLED = 10
_LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}

_M = {
    "check.readme": {"en": "README", "ru": "README", "fr": "README"},
    "check.license": {"en": "LICENSE", "ru": "LICENSE", "fr": "LICENSE"},
    "check.tests": {"en": "Tests", "ru": "Тесты", "fr": "Tests"},
    "check.ci": {"en": "CI configuration", "ru": "Настройка CI", "fr": "Configuration CI"},
    "check.language_breakdown": {"en": "Language breakdown", "ru": "Разбивка по языкам", "fr": "Répartition des langages"},
    "check.llm_code_review": {"en": "LLM-based code review", "ru": "Оценка кода (ИИ)", "fr": "Revue de code par IA"},

    "found": {"en": "Found", "ru": "Найден", "fr": "Trouvé"},
    "readme.missing": {"en": "No README file found", "ru": "Файл README не найден", "fr": "Aucun fichier README trouvé"},
    "license.missing": {"en": "No LICENSE file found", "ru": "Файл LICENSE не найден", "fr": "Aucun fichier LICENSE trouvé"},
    "tests.found": {"en": "Test-related files found", "ru": "Найдены файлы тестов", "fr": "Fichiers de tests trouvés"},
    "tests.missing": {"en": "No files with 'test' in their path were found", "ru": "Не найдено файлов со словом «test» в пути", "fr": "Aucun fichier avec 'test' dans son chemin n'a été trouvé"},
    "ci.missing": {"en": "No CI configuration found", "ru": "Конфигурация CI не найдена", "fr": "Aucune configuration CI trouvée"},
    "language_breakdown.none": {"en": "No recognized source files", "ru": "Распознаваемые исходные файлы не найдены", "fr": "Aucun fichier source reconnu"},

    "repo_summary": {"en": "{n} files; {langs}", "ru": "{n} файлов; {langs}", "fr": "{n} fichiers ; {langs}"},
    "repo_summary.no_langs": {"en": "no recognized languages", "ru": "языки не распознаны", "fr": "aucun langage reconnu"},

    "quality.na_no_model": {"en": "No model_name configured for this scan", "ru": "Для этого сканирования не настроена модель (model_name)", "fr": "Aucun model_name configuré pour cette analyse"},
    "quality.na_no_model_summary": {"en": "Not evaluated — requires a configured LLM", "ru": "Не оценено — требуется настроенная модель", "fr": "Non évalué — nécessite un modèle IA configuré"},
    "quality.na_no_files": {"en": "No files matched known code extensions", "ru": "Ни один файл не соответствует известным расширениям кода", "fr": "Aucun fichier ne correspond aux extensions de code connues"},
    "quality.na_no_files_summary": {"en": "No recognized source files to review", "ru": "Нет распознаваемых исходных файлов для проверки", "fr": "Aucun fichier source reconnu à analyser"},
    "quality.na_failed": {"en": "LLM review failed (model: {model})", "ru": "Оценка ИИ не удалась (модель: {model})", "fr": "L'analyse par IA a échoué (modèle : {model})"},
    "quality.na_failed_summary": {"en": "LLM review failed or returned no result", "ru": "Оценка ИИ не удалась или не вернула результат", "fr": "L'analyse par IA a échoué ou n'a renvoyé aucun résultat"},
    "quality.issues_suffix": {"en": " Issues: {issues}", "ru": " Проблемы: {issues}", "fr": " Problèmes : {issues}"},
    "quality.summary": {"en": "Quality {n}/10 (sampled up to {max_files} largest source files)", "ru": "Качество {n}/10 (проверены до {max_files} самых крупных файлов)", "fr": "Qualité {n}/10 (jusqu'à {max_files} plus gros fichiers analysés)"},

    "methodology": {"en": "Repository structure scan (README/LICENSE/tests/CI presence, language breakdown) plus an LLM-based review of the largest sampled source files.", "ru": "Сканирование структуры репозитория (наличие README/LICENSE/тестов/CI, разбивка по языкам) плюс оценка ИИ самых крупных файлов исходного кода.", "fr": "Analyse de la structure du dépôt (présence de README/LICENSE/tests/CI, répartition des langages) et revue par IA des plus gros fichiers source échantillonnés."},
    "limitations": {"en": "Only up to {max_files} of the largest source files (~{max_chars} characters total) were reviewed by the LLM, not the full codebase. Reviews the repository's current state as a whole, not a specific pull request diff.", "ru": "ИИ проверил не всю кодовую базу, а только до {max_files} самых крупных файлов (~{max_chars} символов суммарно). Оценивается текущее состояние репозитория целиком, а не конкретный diff pull request'а.", "fr": "Seuls jusqu'à {max_files} des plus gros fichiers source (~{max_chars} caractères au total) ont été analysés par l'IA, pas l'ensemble du code. L'évaluation porte sur l'état actuel du dépôt dans son ensemble, pas sur un diff de pull request spécifique."},
    "limitations.no_model_suffix": {"en": " No model_name was configured, so Code Quality was not evaluated.", "ru": " Модель (model_name) не была настроена, поэтому раздел «Качество кода» не оценивался.", "fr": " Aucun model_name n'a été configuré, le pilier Qualité du code n'a donc pas été évalué."},
}
_t = make_translator(_M)


def _is_git_url(repo: str) -> bool:
    return repo.startswith(("http://", "https://", "git@")) or repo.endswith(".git")


async def _clone_repo(url: str, dest: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--depth", "1", url, dest, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {stderr.decode(errors='replace')[:500]}")


def _walk_files(root: str) -> List[str]:
    # Excludes only explicitly-known junk/vendor dirs, not every dot-dir — .github (CI
    # config) and similar dot-dirs with real signal must stay walkable.
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        for name in filenames:
            files.append(os.path.join(dirpath, name))
    return files


def collect_repo_structure(root: str, lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    files = _walk_files(root)
    ext_counts = Counter(os.path.splitext(f)[1].lower() for f in files if os.path.splitext(f)[1])

    findings = []
    has_readme = any(os.path.basename(f).lower().startswith("readme") for f in files)
    findings.append(Finding(check=_t("check.readme", lang), status=Status.OK if has_readme else Status.WARN, detail=_t("found", lang) if has_readme else _t("readme.missing", lang)))

    has_license = any(os.path.basename(f).lower().startswith("license") for f in files)
    findings.append(Finding(check=_t("check.license", lang), status=Status.OK if has_license else Status.WARN, detail=_t("found", lang) if has_license else _t("license.missing", lang)))

    has_tests = any("test" in os.path.relpath(f, root).lower() for f in files)
    findings.append(Finding(check=_t("check.tests", lang), status=Status.OK if has_tests else Status.BAD, detail=_t("tests.found", lang) if has_tests else _t("tests.missing", lang)))

    has_ci = any(
        ".github/workflows" in os.path.relpath(f, root).replace("\\", "/") or os.path.basename(f) in (".gitlab-ci.yml", ".travis.yml")
        for f in files
    )
    findings.append(Finding(check=_t("check.ci", lang), status=Status.OK if has_ci else Status.WARN, detail=_t("found", lang) if has_ci else _t("ci.missing", lang)))

    top_langs = ", ".join(f"{ext} ({count})" for ext, count in ext_counts.most_common(5))
    findings.append(Finding(check=_t("check.language_breakdown", lang), status=Status.NA, detail=top_langs or _t("language_breakdown.none", lang)))

    score = 10.0
    if not has_readme:
        score -= 2.0
    if not has_license:
        score -= 1.0
    if not has_tests:
        score -= 4.0
    if not has_ci:
        score -= 1.5
    score = max(0.0, round(score, 1))

    summary = _t("repo_summary", lang, n=len(files), langs=top_langs or _t("repo_summary.no_langs", lang))
    return Pillar(name=pillar_name("Repository Structure", lang), score=score, summary=summary, findings=findings)


class CodeLLMReview(BaseModel):
    code_quality: int = Field(ge=1, le=10, description="Overall code quality of the sampled files")
    issues: List[str] = Field(default_factory=list, description="Specific bugs, smells, or risky patterns found, naming files when visible")
    summary: str = Field(description="One or two sentence overall assessment")


def _sample_source(root: str, max_chars: int = _MAX_SAMPLE_CHARS) -> str:
    files = [f for f in _walk_files(root) if os.path.splitext(f)[1].lower() in _CODE_EXTENSIONS]
    files.sort(key=lambda f: os.path.getsize(f), reverse=True)  # largest files first: likely the most substantive

    chunks = []
    total = 0
    for f in files[:_MAX_FILES_SAMPLED]:
        if total >= max_chars:
            break
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read(max_chars - total)
        except OSError:
            continue
        rel = os.path.relpath(f, root)
        chunk = f"--- {rel} ---\n{content}\n"
        chunks.append(chunk)
        total += len(chunk)
    return "".join(chunks)


async def _llm_review_code(sample: str, model_name: str, lang: str) -> Optional[CodeLLMReview]:
    language_name = _LANGUAGE_NAMES.get(lang, "English")
    messages = [
        SystemMessage(content=f"You are a senior software engineer performing a code review. Be concrete: cite specific issues, not generic advice. Respond in {language_name}: the 'summary' and 'issues' fields must be written in {language_name}."),
        HumanMessage(content=f"Review these source file excerpts from a repository:\n\n{sample}"),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=CodeLLMReview)
    except Exception as exc:
        logger.warning(f"| ⚠️ Code LLM review failed for model {model_name}: {exc}")
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        logger.warning(f"| ⚠️ Code LLM review returned no structured result: {getattr(response, 'message', None)}")
        return None
    return response.extra.parsed_model


async def collect_code_quality(root: str, model_name: Optional[str], lang: str = "en") -> Pillar:
    lang = normalize_lang(lang)
    name = pillar_name("Code Quality", lang)
    check = _t("check.llm_code_review", lang)

    if not model_name:
        return Pillar(name=name, score=None, summary=_t("quality.na_no_model_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_no_model", lang))])

    sample = _sample_source(root)
    if not sample.strip():
        return Pillar(name=name, score=None, summary=_t("quality.na_no_files_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_no_files", lang))])

    review = await _llm_review_code(sample, model_name, lang)
    if review is None:
        return Pillar(name=name, score=None, summary=_t("quality.na_failed_summary", lang), findings=[Finding(check=check, status=Status.NA, detail=_t("quality.na_failed", lang, model=model_name))])

    status = Status.OK if review.code_quality >= 7 else Status.WARN if review.code_quality >= 4 else Status.BAD
    detail = review.summary
    if review.issues:
        detail += _t("quality.issues_suffix", lang, issues="; ".join(review.issues))
    summary = _t("quality.summary", lang, n=review.code_quality, max_files=_MAX_FILES_SAMPLED)
    return Pillar(name=name, score=float(review.code_quality), summary=summary, findings=[Finding(check=check, status=status, detail=detail)])


async def run_code_review(input: str, model_name: Optional[str] = None, lang: str = "en") -> Report:
    """Review a repository: `input` is a local directory path or a git clone URL."""
    lang = normalize_lang(lang)
    repo = input
    cleanup_dir: Optional[str] = None
    try:
        if _is_git_url(repo):
            cleanup_dir = tempfile.mkdtemp(prefix="code_review_")
            await _clone_repo(repo, cleanup_dir)
            root = cleanup_dir
        else:
            root = repo
            if not os.path.isdir(root):
                raise ValueError(f"Not a directory and not a recognized git URL: {repo}")

        pillars = [collect_repo_structure(root, lang=lang), await collect_code_quality(root, model_name, lang=lang)]

        limitations = _t("limitations", lang, max_files=_MAX_FILES_SAMPLED, max_chars=_MAX_SAMPLE_CHARS)
        if not model_name:
            limitations += _t("limitations.no_model_suffix", lang)

        report = Report(
            meta=ReportMeta(kind="code_review", subject=repo, lang=lang),
            pillars=pillars,
            recommendations=build_recommendations(pillars, lang=lang),
            methodology=_t("methodology", lang),
            limitations=limitations,
        ).finalize()
        report.verdict = verdict_for(report.overall_score, lang=lang)
        return report
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
