"""Source-file walking, sampling, and LLM code-quality review — shared
between code_review (an OSS-style repo) and app_review (a small generated
app), which need identical sampling logic but different deterministic
structure pillars around it.
"""

import os
from typing import List, Optional

from pydantic import BaseModel, Field

from src.logger import logger
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager

IGNORED_DIRS = {
    ".git", "venv", ".venv", "node_modules", "__pycache__", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", ".idea", ".vscode",
}
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cpp", ".h", ".cs", ".php", ".swift", ".kt",
}
MAX_SAMPLE_CHARS = 8000
MAX_FILES_SAMPLED = 10
LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}


def walk_files(root: str) -> List[str]:
    # Excludes only explicitly-known junk/vendor dirs, not every dot-dir — .github (CI
    # config) and similar dot-dirs with real signal must stay walkable.
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            files.append(os.path.join(dirpath, name))
    return files


def sample_source(root: str, max_chars: int = MAX_SAMPLE_CHARS, max_files: int = MAX_FILES_SAMPLED) -> str:
    files = [f for f in walk_files(root) if os.path.splitext(f)[1].lower() in CODE_EXTENSIONS]
    files.sort(key=lambda f: os.path.getsize(f), reverse=True)  # largest files first: likely the most substantive

    chunks = []
    total = 0
    for f in files[:max_files]:
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


class CodeLLMReview(BaseModel):
    code_quality: int = Field(ge=1, le=10, description="Overall code quality of the sampled files")
    issues: List[str] = Field(default_factory=list, description="Specific bugs, smells, or risky patterns found, naming files when visible")
    summary: str = Field(description="One or two sentence overall assessment")


async def llm_review_code(sample: str, model_name: str, lang: str, persona: str) -> Optional[CodeLLMReview]:
    language_name = LANGUAGE_NAMES.get(lang, "English")
    messages = [
        SystemMessage(content=f"{persona} Be concrete: cite specific issues, not generic advice. Respond in {language_name}: the 'summary' and 'issues' fields must be written in {language_name}."),
        HumanMessage(content=f"Review these source file excerpts:\n\n{sample}"),
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
