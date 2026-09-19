import os
from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from src.audit.website import run_website_audit
from src.registry import TOOL
from src.report.render import RENDERERS
from src.tool.types import Tool, ToolExtra, ToolResponse
from src.utils import assemble_project_path

_EXTENSIONS = {"markdown": "md", "html": "html", "latex": "tex", "pdf": "pdf"}
_BINARY_FORMATS = {"pdf"}

_WEBSITE_AUDIT_DESCRIPTION = """Run a multi-pillar website audit for a single URL and write the report to disk.

Scans SEO, security headers, accessibility, performance, tech stack, broken links,
privacy/compliance, and content signals, then produces one structured report
rendered as markdown, html, latex, or pdf.
"""


@TOOL.register_module(force=True)
class WebsiteAuditTool(Tool):
    """A tool that audits a website URL and produces a structured review report."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = "website_audit"
    description: str = _WEBSITE_AUDIT_DESCRIPTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")

    base_dir: str = Field(default="workdir/website_audit", description="The base directory for saving audit reports.")
    model_name: Optional[str] = Field(
        default=None, description="Default LLM used for the Content and Design/UX judgment pillars, if not overridden per call."
    )

    def __init__(self, base_dir: Optional[str] = None, model_name: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.base_dir = assemble_project_path(base_dir or self.base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        if model_name is not None:
            self.model_name = model_name

    async def __call__(
        self, url: str, output_format: str = "markdown", model_name: Optional[str] = None, lang: str = "en", **kwargs
    ) -> ToolResponse:
        """Audit a website URL and write the resulting report to disk.

        Args:
            url (str): The URL to audit (e.g. "https://example.com").
            output_format (str): One of "markdown", "html", "latex". Defaults to "markdown".
            model_name (str): Optional LLM for the Content and Design/UX judgment pillars.
                Falls back to the tool's configured default, or omits those pillars' LLM
                pass entirely if neither is set.
            lang (str): Output language for the report: "en", "ru", or "fr". Defaults to "en".

        Returns:
            ToolResponse with the report file path and structured data in extra.
        """
        if output_format not in RENDERERS:
            return ToolResponse(
                success=False,
                message=f"Unknown output_format '{output_format}'. Must be one of {list(RENDERERS)}.",
            )

        try:
            report = await run_website_audit(url, model_name=model_name or self.model_name, lang=lang)
        except Exception as exc:
            return ToolResponse(success=False, message=f"Website audit failed for {url}: {exc}")

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
            message=f"Website audit for {report.meta.subject} complete. Overall score: {report.overall_score}/10.",
            extra=ToolExtra(file_path=file_path, data=report.model_dump(mode="json")),
        )
