import pytest

# The agent-tool wrappers register with mmengine's Registry at class-definition
# time, and mmengine isn't part of the website_audit-only install. The deployed
# service calls src.audit.website directly and never goes through this wrapper.
pytest.importorskip("mmengine", reason="agent-tool wrappers need mmengine (full install); the service path is tested elsewhere")

from src.report.schema import Pillar, Report, ReportMeta
from src.tool.workflow_tools.website_audit import WebsiteAuditTool


def _stub_report() -> Report:
    return Report(
        meta=ReportMeta(kind="website_audit", subject="https://example.com"),
        pillars=[Pillar(name="SEO", score=7, summary="ok")],
    ).finalize()


@pytest.mark.asyncio
async def test_website_audit_tool_writes_markdown(tmp_path, monkeypatch):
    async def fake_run(url, model_name=None, lang="en"):
        assert url == "https://example.com"
        return _stub_report()

    monkeypatch.setattr("src.tool.workflow_tools.website_audit.run_website_audit", fake_run)

    tool = WebsiteAuditTool(base_dir=str(tmp_path))
    response = await tool(url="https://example.com", output_format="markdown")

    assert response.success is True
    assert response.extra.file_path.endswith(".md")
    with open(response.extra.file_path) as f:
        content = f.read()
    assert "# Website Audit Report" in content


@pytest.mark.asyncio
async def test_website_audit_tool_rejects_unknown_format(tmp_path):
    tool = WebsiteAuditTool(base_dir=str(tmp_path))
    response = await tool(url="https://example.com", output_format="docx")
    assert response.success is False
    assert "Unknown output_format" in response.message


@pytest.mark.asyncio
async def test_website_audit_tool_writes_pdf_binary(tmp_path, monkeypatch):
    async def fake_run(url, model_name=None, lang="en"):
        return _stub_report()

    monkeypatch.setattr("src.tool.workflow_tools.website_audit.run_website_audit", fake_run)
    monkeypatch.setitem(
        __import__("src.tool.workflow_tools.website_audit", fromlist=["RENDERERS"]).RENDERERS,
        "pdf",
        lambda report: b"%PDF-1.7 fake",
    )

    tool = WebsiteAuditTool(base_dir=str(tmp_path))
    response = await tool(url="https://example.com", output_format="pdf")

    assert response.success is True
    assert response.extra.file_path.endswith(".pdf")
    with open(response.extra.file_path, "rb") as f:
        assert f.read() == b"%PDF-1.7 fake"


@pytest.mark.asyncio
async def test_website_audit_tool_pdf_unavailable(tmp_path, monkeypatch):
    async def fake_run(url, model_name=None, lang="en"):
        return _stub_report()

    def raise_import_error(report):
        raise ImportError("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setattr("src.tool.workflow_tools.website_audit.run_website_audit", fake_run)
    monkeypatch.setitem(
        __import__("src.tool.workflow_tools.website_audit", fromlist=["RENDERERS"]).RENDERERS, "pdf", raise_import_error
    )

    tool = WebsiteAuditTool(base_dir=str(tmp_path))
    response = await tool(url="https://example.com", output_format="pdf")
    assert response.success is False
    assert "PDF rendering is unavailable" in response.message


@pytest.mark.asyncio
async def test_website_audit_tool_reports_scan_failure(tmp_path, monkeypatch):
    async def fake_run(url, model_name=None, lang="en"):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.tool.workflow_tools.website_audit.run_website_audit", fake_run)
    tool = WebsiteAuditTool(base_dir=str(tmp_path))
    response = await tool(url="https://example.com")
    assert response.success is False
    assert "boom" in response.message
