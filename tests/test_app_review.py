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


# ---- Secret scan: regression cases from the 2026-10-02 validation review --------
# The old scan missed 7 of these 8 shapes and scored the pillar 10/10.

import os  # noqa: E402

from src.review.app_review import collect_security_hygiene as _hygiene  # noqa: E402

_REAL_SECRET_CASES = [
    ("app.py", 'SECRET_KEY = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ2bC4dF"\n'),
    ("app.py", 'app.config["SECRET_KEY"] = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ2bC4dF"\n'),
    ("app.py", 'cfg = {"password": "aB3dE5gH7jK9mN1pQ3sT5vW7"}\n'),
    ("app.py", 'client = OpenAI("sk-proj-Ab12_cd34-Ef56_gh78-Ij90_kl12")\n'),
    ("app.py", 'PASSWORD = "Sup3r$ecret!Pass#2024"\n'),
    ("app.py", 'DB = "postgresql://admin:Sup3rSecretPw99@db.internal:5432/app"\n'),
    ("app.py", 'API_KEY = "aB3dE5gH7jKxXx9mN1pQ3sT5vW7yZ"\n'),  # contains "xxx" - the old check skipped it
    (".env", "API_KEY=aB3dE5gH7jK9mN1pQ3sT5vW7yZ2bC4dF\n"),  # dotenv contents were never scanned
    (".env.production", "DB_PASSWORD=Sup3rSecretPw99\n"),
    ("settings.yml", 'api_key: "Abc123Def456Ghi789Jkl"\n'),
    ("k.py", 'KEY = """-----BEGIN RSA PRIVATE KEY-----\nMIIE"""\n'),
]

_NOT_SECRET_CASES = [
    ("app.py", 'API_KEY = "your-api-key-here"\n'),
    ("app.py", 'TOKEN = "<your-token>"\n'),
    ("app.py", 'PASSWORD = "${DB_PASSWORD}"\n'),
    ("app.py", 'API_KEY = "xxxxxxxxxxxxxxxx"\n'),
    ("app.py", 'API_KEY = os.environ["API_KEY"]\n'),
    ("app.py", 'API_KEY_HEADER = "X-Api-Key"\n'),  # name contains API_KEY, value is not a secret
    (".env.example", "API_KEY=your-key-here\nDB_PASSWORD=changeme\n"),
    ("app.py", 'TOKEN = "abc"\n'),
]


def _secrets_status(tmp_path, filename, content):
    (tmp_path / filename).write_text(content)
    pillar = _hygiene(str(tmp_path))
    return next(f.status for f in pillar.findings if f.check == "Hardcoded secrets")


@pytest.mark.parametrize("filename,content", _REAL_SECRET_CASES)
def test_secret_scan_flags_real_secret_shapes(tmp_path, filename, content):
    assert _secrets_status(tmp_path, filename, content) == Status.BAD


@pytest.mark.parametrize("filename,content", _NOT_SECRET_CASES)
def test_secret_scan_ignores_placeholders_and_non_secrets(tmp_path, filename, content):
    assert _secrets_status(tmp_path, filename, content) == Status.OK


@pytest.mark.parametrize(
    "content,expected",
    [
        ("DEBUG = True\n", Status.BAD),
        ('app.config["DEBUG"] = True\n', Status.BAD),
        ("# never run with debug=True in production\napp.run(debug=False)\n", Status.OK),
    ],
)
def test_debug_detection_is_case_insensitive_and_ignores_comments(tmp_path, content, expected):
    (tmp_path / "a.py").write_text(content)
    pillar = _hygiene(str(tmp_path))
    assert next(f.status for f in pillar.findings if f.check == "Debug mode") == expected


@pytest.mark.parametrize(
    "content,expected",
    [
        ("from flask_cors import CORS\nCORS(app)\n", Status.WARN),  # allows every origin by default
        ('CORS(app, origins="*")\n', Status.WARN),
        ('app.add_middleware(CORSMiddleware, allow_origins=["*"])\n', Status.WARN),
        ('CORS(app, origins=["https://a.com"])\n', Status.OK),
    ],
)
def test_cors_detection_covers_flask_cors(tmp_path, content, expected):
    (tmp_path / "a.py").write_text(content)
    pillar = _hygiene(str(tmp_path))
    assert next(f.status for f in pillar.findings if f.check == "CORS policy") == expected


def test_env_variants_are_detected_but_example_files_are_not(tmp_path):
    (tmp_path / ".env.local").write_text("X=1\n")
    assert next(f.status for f in _hygiene(str(tmp_path)).findings if f.check == "Committed .env file") == Status.WARN
    os.remove(tmp_path / ".env.local")
    (tmp_path / ".env.example").write_text("X=1\n")
    assert next(f.status for f in _hygiene(str(tmp_path)).findings if f.check == "Committed .env file") == Status.OK
