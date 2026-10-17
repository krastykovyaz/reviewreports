"""A failed model call is logged at WARNING without the text that was derived from the person's CV or the visited site
(pydantic validation errors quote the input they rejected; a parse failure carries the raw output). The text is DEBUG."""
import logging

import pytest

from src.audit.collectors import content, ux
from src.cv import extract as cv_extract
from src.cv.schema import CVProfile
from src.review import _code_sampling, document
from src.service.llm_log import log_llm_failure
from tests.helpers import run

@pytest.fixture()
def caplog(caplog):
    """src.logger.logger is a stand-alone Logger (no parent), so pytest's root-level capture never sees it: hook the capture
    handler onto it directly."""
    from src.logger import logger
    logger.addHandler(caplog.handler)
    old = logger.level
    yield caplog
    logger.removeHandler(caplog.handler)
    logger.setLevel(old)
    logger._cache.clear()


_PII = "Jordan Reyes jordan.reyes@email.com +1 415 555 0142 987 Mission St"
_PROFILE = CVProfile.model_validate({"name": "A B", "contact": {"email": "a@b.co"}})


class _Unparseable:
    """The shape of src.model.types.LLMResponse for a call whose output could not be parsed."""
    success = False
    message = f"could not parse model output: {_PII}"
    extra = None


def _manager(how):
    async def fake(model, messages, response_format=None, **kw):
        if how == "raises":
            raise ValueError(f"1 validation error for CVProfile\n  Input should be a valid string [input_value='{_PII}', input_type=str]")
        return _Unparseable()
    return fake


_CASES = {
    "cv_extract": (cv_extract, lambda: cv_extract.extract_cv_profile(_PII, "m")),
    "cv_edit": (cv_extract, lambda: cv_extract.edit_cv_profile(_PROFILE, "x", "m")),
    "content": (content, lambda: content._llm_review_content(_PII, "m", "en")),
    "ux": (ux, lambda: ux._llm_review_screenshot("aGVsbG8=", "m", "en")),
    "document": (document, lambda: document._llm_review_document(_PII, "resume", "m", "en")),
    "code": (_code_sampling, lambda: _code_sampling.llm_review_code(_PII, "m", "en", "You review code.")),
}


@pytest.mark.parametrize("how", ["raises", "unparseable"])
@pytest.mark.parametrize("case", sorted(_CASES))
def test_no_input_derived_text_at_warning_and_the_call_still_returns_none(monkeypatch, caplog, case, how):
    module, make = _CASES[case]
    monkeypatch.setattr(module, "model_manager", _manager(how))
    assert run(make()) is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a failure is still reported"
    text = " ".join(warnings)
    for needle in ("Jordan", "@email.com", "Mission", "415", "input_value", "could not parse"):
        assert needle not in text
    assert "m" in text and ("ValueError" in text or "success=False" in text)  # the model, and the kind of failure


@pytest.mark.parametrize("how,marker", [("raises", "input_value"), ("unparseable", "could not parse model output")])
def test_the_full_text_is_available_at_debug(monkeypatch, caplog, how, marker):
    monkeypatch.setattr(cv_extract, "model_manager", _manager(how))
    from src.logger import logger
    logger.setLevel(logging.DEBUG)  # (the fixture puts the level back)
    logger._cache.clear()           # a stand-alone Logger is not in the manager's registry, so setLevel() doesn't clear its isEnabledFor cache
    run(cv_extract.extract_cv_profile(_PII, "m"))
    debug = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    assert marker in debug and "Jordan" in debug


def test_at_the_default_level_the_detail_is_never_even_handed_to_the_logger(monkeypatch):
    """The project's logger writes every record it is given to its log file whatever the level, so with DEBUG off the
    detail must not be passed to it at all."""
    from src.service import llm_log
    seen = []
    monkeypatch.setattr(llm_log.logger, "debug", lambda msg, *a, **k: seen.append(msg))
    monkeypatch.setattr(llm_log.logger, "isEnabledFor", lambda level: False)
    log_llm_failure("X", "m", exc=ValueError(_PII))
    assert seen == []
