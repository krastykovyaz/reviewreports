import pytest

from src.report.schema import Status
from src.review._code_sampling import CodeLLMReview
from src.review.app_review import collect_app_structure, collect_code_quality, collect_security_hygiene, run_app_review
from src.model.types import LLMExtra, LLMResponse


def _make_static_app(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>Hi</body></html>")
    (tmp_path / "script.js").write_text("console.log('hi');\n" * 20)
    return tmp_path


def _make_flask_app(tmp_path, debug=False, cors_wildcard=False, secret=None, with_env=False):
    lines = ["from flask import Flask", "app = Flask(__name__)"]
    if secret:
        lines.append(f'API_KEY = "{secret}"')
    if cors_wildcard:
        lines.append('allow_origins = ["*"]')
    lines.append(f"app.run(debug={debug})")
    (tmp_path / "app.py").write_text("\n".join(lines) + "\n" + "# padding\n" * 50)
    if with_env:
        (tmp_path / ".env").write_text("SOME_VAR=1")
    return tmp_path


def test_collect_app_structure_detects_static_site(tmp_path):
    _make_static_app(tmp_path)
    pillar = collect_app_structure(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert "index.html" in checks["Detected stack"].detail
    assert checks["Entry point"].status == Status.OK
    assert pillar.score == 10.0


def test_collect_app_structure_flags_missing_entry_point(tmp_path):
    (tmp_path / "notes.txt").write_text("nothing runnable here")
    pillar = collect_app_structure(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Detected stack"].detail == "Could not determine the stack (no index.html, no Flask/FastAPI import found)"
    assert checks["Entry point"].status == Status.NA


def test_collect_app_structure_detects_flask_missing_entry_file(tmp_path):
    (tmp_path / "lib.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    pillar = collect_app_structure(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert "Flask" in checks["Detected stack"].detail
    assert checks["Entry point"].status == Status.BAD


def test_collect_app_structure_flags_empty_dir(tmp_path):
    pillar = collect_app_structure(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["File count"].status == Status.BAD


def test_collect_security_hygiene_clean_app(tmp_path):
    _make_flask_app(tmp_path, debug=False, cors_wildcard=False)
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Hardcoded secrets"].status == Status.OK
    assert checks["Debug mode"].status == Status.OK
    assert checks["CORS policy"].status == Status.OK
    assert checks["Committed .env file"].status == Status.OK
    assert pillar.score == 10.0


def test_collect_security_hygiene_flags_hardcoded_secret(tmp_path):
    _make_flask_app(tmp_path, secret="sk-ant-abcdefghijklmnopqrstuvwx")
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Hardcoded secrets"].status == Status.BAD
    assert "app.py" in checks["Hardcoded secrets"].evidence


def test_collect_security_hygiene_ignores_placeholder_secret(tmp_path):
    _make_flask_app(tmp_path, secret="your-api-key-here-xxxxxxxxxxxx")
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Hardcoded secrets"].status == Status.OK


def test_collect_security_hygiene_flags_debug_mode(tmp_path):
    _make_flask_app(tmp_path, debug=True)
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Debug mode"].status == Status.BAD
    assert "app.py" in checks["Debug mode"].detail


def test_collect_security_hygiene_flags_cors_wildcard(tmp_path):
    _make_flask_app(tmp_path, cors_wildcard=True)
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["CORS policy"].status == Status.WARN


def test_collect_security_hygiene_flags_committed_env_file(tmp_path):
    _make_flask_app(tmp_path, with_env=True)
    pillar = collect_security_hygiene(str(tmp_path))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Committed .env file"].status == Status.WARN


@pytest.mark.asyncio
async def test_collect_code_quality_na_without_model(tmp_path):
    _make_flask_app(tmp_path)
    pillar = await collect_code_quality(str(tmp_path), model_name=None)
    assert pillar.score is None
    assert pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_collect_code_quality_na_with_no_source_files(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    pillar = await collect_code_quality(str(tmp_path), model_name="ollama/qwen3-30b")
    assert pillar.score is None
    assert "No files matched" in pillar.findings[0].detail


@pytest.mark.asyncio
async def test_collect_code_quality_uses_llm_review(tmp_path, monkeypatch):
    _make_flask_app(tmp_path)
    review = CodeLLMReview(code_quality=6, issues=["no input validation on the route"], summary="Reasonable for a small app.")

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        assert response_format is CodeLLMReview
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=review))

    monkeypatch.setattr("src.review._code_sampling.model_manager", fake_model_manager)
    pillar = await collect_code_quality(str(tmp_path), model_name="ollama/qwen3-30b")
    assert pillar.score == 6.0
    assert pillar.findings[0].status == Status.WARN
    assert "no input validation" in pillar.findings[0].detail


@pytest.mark.asyncio
async def test_run_app_review_static_site_without_model(tmp_path):
    _make_static_app(tmp_path)
    report = await run_app_review(str(tmp_path))
    assert report.meta.kind == "app_review"
    assert len(report.pillars) == 3
    assert report.pillars[0].name == "App Structure"
    assert report.pillars[1].name == "Security Hygiene"
    assert report.pillars[2].name == "Code Quality"
    assert report.pillars[2].score is None  # no model_name -> N/A, excluded from overall
    assert report.overall_score is not None
    assert report.verdict


@pytest.mark.asyncio
async def test_run_app_review_rejects_nonexistent_path():
    with pytest.raises(ValueError, match="Not a directory"):
        await run_app_review("/definitely/not/a/real/path/xyz")
