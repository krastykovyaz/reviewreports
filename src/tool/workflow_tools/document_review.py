import os
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.registry import TOOL
from src.report.render import RENDERERS
from src.review.document import run_document_review
from src.tool.types import Tool, ToolExtra, ToolResponse
from src.utils import assemble_project_path

_EXTENSIONS = {"markdown": "md", "html": "html", "latex": "tex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_DOCUMENT_REVIEW_DESCRIPTION = """Review a document file (resume, presentation, or book/manuscript) and write the report to disk.

Extracts text from the file (pdf, docx, pptx, txt, md, ...), then runs an LLM-based review
against a rubric matching the chosen flavor, producing one structured report.
"""


@TOOL.register_module(force=True)
class DocumentReviewTool(Tool):
    """A tool that reviews a resume, presentation, or book/manuscript file and produces a structured review report."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "document_review"
    description: str = _DOCUMENT_REVIEW_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")

    base_dir: str = Field(default="workdir/document_review", description="The base directory for saving review reports.")
    model_name: Optional[str] = Field(default=None, description="Default LLM used for the document judgment pillar, if not overridden per call.")

    def __init__(self, base_dir: Optional[str] = None, model_name: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir or self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        if model_name is not None:
            self.model_name = model_name

    async def __call__(
        self, file_path: str, flavor: str = "resume", output_format: str = "markdown", model_name: Optional[str] = None, lang: str = "en", **kwargs
    ) -> ToolResponse:
        """Review a document file and write the resulting report to disk.

        Args:
            file_path (str): Path to the file to review.
            flavor (str): One of "resume", "presentation", "book". Selects the review rubric. Defaults to "resume".
            output_format (str): One of "markdown", "html", "latex", "pdf". Defaults to "markdown".
            model_name (str): Optional LLM for the judgment pillar. Falls back to the tool's
                configured default, or omits that pillar's LLM pass entirely if neither is set.
            lang (str): Output language for the report: "en", "ru", or "fr". Defaults to "en".

        Returns:
            ToolResponse with the report file path and structured data in extra.
        """
        if output_format not in RENDERERS:
            return ToolResponse(success=False, message=f"Unknown output_format '{output_format}'. Must be one of {list(RENDERERS)}.")

        try:
            report = await run_document_review(file_path, flavor=flavor, model_name=model_name or self.model_name, lang=lang)
        except Exception as exc:
            return ToolResponse(success=False, message=f"Document review failed for {file_path}: {exc}")

        try:
            rendered = RENDERERS[output_format](report)
        except ImportError as exc:
            return ToolResponse(success=False, message=f"PDF rendering is unavailable in this environment: {exc}")

        safe_name = "".join(c if c.isalnum() else "_" for c in os.path.basename(file_path)).strip("_")[:80]
        out_path = os.path.join(self.base_dir, f"{flavor}_{safe_name}.{_EXTENSIONS[output_format]}")
        if output_format in _BINARY_FORMATS:
            with open(out_path, "wb") as f:
                f.write(rendered)
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(rendered)

        return ToolResponse(
            success=True,
            message=f"{flavor.title()} review for {file_path} complete. Overall score: {report.overall_score}/10.",
            extra=ToolExtra(file_path=out_path, data=report.model_dump(mode="json")),
        )
