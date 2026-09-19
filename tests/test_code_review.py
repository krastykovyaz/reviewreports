import pytest

from src.report.schema import Status
from src.review.code_review import CodeLLMReview, _is_git_url, collect_code_quality, collect_repo_structure, run_code_review
from src.model.types import LLMExtra, LLMResponse


def _make_repo(tmp_path, with_readme=True, with_license=False, with_tests=True, with_ci=False):
    if with_readme:
        (tmp_path / "README.md").write_text("# Project")
    if with_license:
        (tmp_path / "LICENSE").write_text("MIT")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def main():\n    pass\n" * 50)
    if with_tests:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    if with_ci:
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci")
    return tmp_path


def test_is_git_url():
    assert _is_git_url("https://github.com/org/repo")
    assert _is_git_url("http://example.com/repo.git")
    assert _is_git_url("git@github.com:org/repo.git")
    assert not _is_git_url("/local/path/to/repo")
    assert not _is_git_url("relative/repo")


def test_collect_repo_structure_flags_missing_pieces(tmp_path):
    _make_repo(tmp_path, with_readme=False, with_license=False, with_tests=False, with_ci=False)
    pillar = collect_repo_structure(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["README"].status == Status.WARN
    assert checks["LICENSE"].status == Status.WARN
    assert checks["Tests"].status == Status.BAD
    assert checks["CI configuration"].status == Status.WARN
    assert pillar.score < 5


def test_collect_repo_structure_scores_complete_repo_high(tmp_path):
    _make_repo(tmp_path, with_readme=True, with_license=True, with_tests=True, with_ci=True)
    pillar = collect_repo_structure(str(tmp_path))
    assert pillar.score == 10.0
    assert "py" in pillar.summary


@pytest.mark.asyncio
async def test_collect_code_quality_na_without_model(tmp_path):
    _make_repo(tmp_path)
    pillar = await collect_code_quality(str(tmp_path), model_name=None)
    assert pillar.score is None
    assert pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_collect_code_quality_na_with_no_source_files(tmp_path):
    (tmp_path / "README.md").write_text("# empty repo, no code")
    pillar = await collect_code_quality(str(tmp_path), model_name="ollama/qwen3-30b")
    assert pillar.score is None
    assert "No files matched" in pillar.findings[0].detail


@pytest.mark.asyncio
async def test_collect_code_quality_uses_llm_review(tmp_path, monkeypatch):
    _make_repo(tmp_path)
    review = CodeLLMReview(code_quality=8, issues=["missing type hints in main.py"], summary="Clean, simple module.")

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        assert response_format is CodeLLMReview
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=review))

    monkeypatch.setattr("src.review.code_review.model_manager", fake_model_manager)
    pillar = await collect_code_quality(str(tmp_path), model_name="ollama/qwen3-30b")
    assert pillar.score == 8.0
    assert pillar.findings[0].status == Status.OK
    assert "missing type hints" in pillar.findings[0].detail


@pytest.mark.asyncio
async def test_run_code_review_local_dir_without_model(tmp_path):
    _make_repo(tmp_path, with_readme=True, with_license=False, with_tests=True, with_ci=False)
    report = await run_code_review(str(tmp_path))
    assert report.meta.kind == "code_review"
    assert len(report.pillars) == 2
    assert report.pillars[1].name == "Code Quality"
    assert report.pillars[1].score is None  # no model_name -> N/A, excluded from overall
    assert report.overall_score == report.pillars[0].score
    assert report.verdict


@pytest.mark.asyncio
async def test_run_code_review_rejects_nonexistent_path():
    with pytest.raises(ValueError, match="Not a directory"):
        await run_code_review("/definitely/not/a/real/path/xyz")
