import pytest

from src.report.schema import Pillar, Report, ReportMeta
from src.tool.workflow_tools.code_review import CodeReviewTool
from src.tool.workflow_tools.document_review import DocumentReviewTool


def _stub_report(kind: str, subject: str) -> Report:
    return Report(meta=ReportMeta(kind=kind, subject=subject), pillars=[Pillar(name="X", score=7, summary="ok")]).finalize()


# ---- CodeReviewTool ---------------------------------------------------------


@pytest.mark.asyncio
async def test_code_review_tool_writes_markdown(tmp_path, monkeypatch):
    async def fake_run(repo, model_name=None, lang="en"):
        assert repo == "https://github.com/org/repo"
        return _stub_report("code_review", "https://github.com/org/repo")

    monkeypatch.setattr("src.tool.workflow_tools.code_review.run_code_review", fake_run)
    tool = CodeReviewTool(base_dir=str(tmp_path))
    response = await tool(repo="https://github.com/org/repo")

    assert response.success is True
    assert response.extra.file_path.endswith(".md")


@pytest.mark.asyncio
async def test_code_review_tool_reports_failure(tmp_path, monkeypatch):
    async def fake_run(repo, model_name=None, lang="en"):
        raise RuntimeError("clone failed")

    monkeypatch.setattr("src.tool.workflow_tools.code_review.run_code_review", fake_run)
    tool = CodeReviewTool(base_dir=str(tmp_path))
    response = await tool(repo="https://github.com/org/repo")
    assert response.success is False
    assert "clone failed" in response.message


@pytest.mark.asyncio
async def test_code_review_tool_rejects_unknown_format(tmp_path):
    tool = CodeReviewTool(base_dir=str(tmp_path))
    response = await tool(repo="/some/path", output_format="docx")
    assert response.success is False


# ---- DocumentReviewTool ------------------------------------------------------


@pytest.mark.asyncio
async def test_document_review_tool_writes_markdown(tmp_path, monkeypatch):
    async def fake_run(file_path, flavor="resume", model_name=None, lang="en"):
        assert flavor == "presentation"
        return _stub_report("presentation_review", file_path)

    monkeypatch.setattr("src.tool.workflow_tools.document_review.run_document_review", fake_run)
    tool = DocumentReviewTool(base_dir=str(tmp_path))
    response = await tool(file_path="/path/to/deck.pptx", flavor="presentation")

    assert response.success is True
    assert "presentation_deck_pptx" in response.extra.file_path
    assert response.extra.file_path.endswith(".md")


@pytest.mark.asyncio
async def test_document_review_tool_reports_failure(tmp_path, monkeypatch):
    async def fake_run(file_path, flavor="resume", model_name=None, lang="en"):
        raise ValueError("Not a file: /nope")

    monkeypatch.setattr("src.tool.workflow_tools.document_review.run_document_review", fake_run)
    tool = DocumentReviewTool(base_dir=str(tmp_path))
    response = await tool(file_path="/nope")
    assert response.success is False
    assert "Not a file" in response.message
