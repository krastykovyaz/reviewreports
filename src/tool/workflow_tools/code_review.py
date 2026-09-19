import os
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.registry import TOOL
from src.report.render import RENDERERS
from src.review.code_review import run_code_review
from src.tool.types import Tool, ToolExtra, ToolResponse
from src.utils import assemble_project_path

_EXTENSIONS = {"markdown": "md", "html": "html", "latex": "tex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_CODE_REVIEW_DESCRIPTION = """Review a code repository (local path or git clone URL) and write the report to disk.

Checks repository structure (README, LICENSE, tests, CI presence, language breakdown), then
runs an LLM-based review of the largest sampled source files, producing one structured report.
"""


@TOOL.register_module(force=True)
class CodeReviewTool(Tool):
    """A tool that reviews a code repository and produces a structured review report."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "code_review"
    description: str = _CODE_REVIEW_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")

    base_dir: str = Field(default="workdir/code_review", description="The base directory for saving review reports.")
    model_name: Optional[str] = Field(default=None, description="Default LLM used for the Code Quality judgment pillar, if not overridden per call.")

    def __init__(self, base_dir: Optional[str] = None, model_name: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir or self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        if model_name is not None:
            self.model_name = model_name

    async def __call__(self, repo: str, output_format: str = "markdown", model_name: Optional[str] = None, lang: str = "en", **kwargs) -> ToolResponse:
        """Review a repository and write the resulting report to disk.

        Args:
            repo (str): Local directory path or git clone URL of the repository to review.
            output_format (str): One of "markdown", "html", "latex", "pdf". Defaults to "markdown".
            model_name (str): Optional LLM for the Code Quality judgment pillar. Falls back to
                the tool's configured default, or omits that pillar's LLM pass entirely if neither is set.
            lang (str): Output language for the report: "en", "ru", or "fr". Defaults to "en".

        Returns:
            ToolResponse with the report file path and structured data in extra.
        """
        if output_format not in RENDERERS:
            return ToolResponse(success=False, message=f"Unknown output_format '{output_format}'. Must be one of {list(RENDERERS)}.")

        try:
            report = await run_code_review(repo, model_name=model_name or self.model_name, lang=lang)
        except Exception as exc:
            return ToolResponse(success=False, message=f"Code review failed for {repo}: {exc}")

        try:
            rendered = RENDERERS[output_format](report)
        except ImportError as exc:
            return ToolResponse(success=False, message=f"PDF rendering is unavailable in this environment: {exc}")

        safe_name = "".join(c if c.isalnum() else "_" for c in report.meta.subject).strip("_")[:80]
        file_path = os.path.join(self.base_dir, f"{safe_name}.{_EXTENSIONS[output_format]}")
        if output_format in _BINARY_FORMATS:
            with open(file_path, "wb") as f:
                f.write(rendered)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(rendered)

        return ToolResponse(
            success=True,
            message=f"Code review for {report.meta.subject} complete. Overall score: {report.overall_score}/10.",
            extra=ToolExtra(file_path=file_path, data=report.model_dump(mode="json")),
        )
