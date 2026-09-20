import os
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.registry import TOOL
from src.report.render import RENDERERS
from src.review.app_review import run_app_review
from src.tool.types import Tool, ToolExtra, ToolResponse
from src.utils import assemble_project_path

_EXTENSIONS = {"markdown": "md", "html": "html", "latex": "tex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_APP_REVIEW_DESCRIPTION = """Review a small generated application's source directory (static site, Flask, or FastAPI) and write the report to disk.

Detects the app's stack and checks for an entry point, scans the source for hardcoded secrets, debug
mode left on, wildcard CORS, and a committed .env file, then runs an LLM-based code-quality review of
the sampled source. Unlike code_review, does not expect README/LICENSE/tests/CI — appropriate for a
small single-purpose app rather than an OSS-style repository.
"""


@TOOL.register_module(force=True)
class AppReviewTool(Tool):
    """A tool that reviews a small generated app's source directory and produces a structured review report."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "app_review"
    description: str = _APP_REVIEW_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")

    base_dir: str = Field(default="workdir/app_review", description="The base directory for saving review reports.")
    model_name: Optional[str] = Field(default=None, description="Default LLM used for the Code Quality judgment pillar, if not overridden per call.")

    def __init__(self, base_dir: Optional[str] = None, model_name: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir or self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        if model_name is not None:
            self.model_name = model_name

    async def __call__(self, app_dir: str, output_format: str = "markdown", model_name: Optional[str] = None, lang: str = "en", **kwargs) -> ToolResponse:
        """Review a generated app's source directory and write the resulting report to disk.

        Args:
            app_dir (str): Local directory path of the generated app to review.
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
            report = await run_app_review(app_dir, model_name=model_name or self.model_name, lang=lang)
        except Exception as exc:
            return ToolResponse(success=False, message=f"App review failed for {app_dir}: {exc}")

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
            message=f"App review for {report.meta.subject} complete. Overall score: {report.overall_score}/10.",
            extra=ToolExtra(file_path=file_path, data=report.model_dump(mode="json")),
        )
