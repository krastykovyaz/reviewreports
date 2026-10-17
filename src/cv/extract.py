"""Extracts a structured CVProfile from free text (a rough draft resume, a
LinkedIn export, informal notes) via an LLM, as an alternative to filling in
the CV builder form field by field. Mirrors src/review/_code_sampling.py's
llm_review_code(): same model_manager + response_format pattern, applied to
extraction instead of judgment.
"""

from typing import Optional

from src.cv.schema import CVProfile
from src.service.llm_log import log_llm_failure
from src.message.types import HumanMessage, SystemMessage
from src.model import model_manager

_EXTRACTION_PERSONA = (
    "You extract a structured CV/resume profile from a person's own free-text description of "
    "themselves (a rough draft, a LinkedIn export, informal notes). Preserve the facts as given - "
    "never invent employers, dates, or credentials that are not present in the text. Preserve the "
    "text's own language rather than translating it. Leave a field empty (or an empty list) when "
    "the text does not mention it, rather than guessing."
)


async def extract_cv_profile(text: str, model_name: str) -> Optional[CVProfile]:
    messages = [
        SystemMessage(content=_EXTRACTION_PERSONA),
        HumanMessage(content=text),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=CVProfile)
    except Exception as exc:
        log_llm_failure("CV extraction", model_name, exc=exc)
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        log_llm_failure("CV extraction", model_name, response=response)
        return None
    return response.extra.parsed_model


_EDIT_PERSONA = (
    "You revise a structured CV/resume profile (given to you as JSON) according to a person's "
    "plain-language change request - the same kind of \"describe changes\" instruction used to "
    "iterate on a generated app or document. Return the FULL updated profile, not a diff: copy "
    "every field the request doesn't touch unchanged, and apply only what was actually asked for. "
    "Never invent employers, dates, or credentials that weren't already present or explicitly given "
    "in the request. Preserve the profile's existing language rather than translating it, unless the "
    "request asks for that."
)


async def edit_cv_profile(current: CVProfile, instructions: str, model_name: str) -> Optional[CVProfile]:
    messages = [
        SystemMessage(content=_EDIT_PERSONA),
        HumanMessage(content=f"Current profile (JSON):\n{current.model_dump_json()}\n\nRequested change:\n{instructions}"),
    ]
    try:
        response = await model_manager(model=model_name, messages=messages, response_format=CVProfile)
    except Exception as exc:
        log_llm_failure("CV edit", model_name, exc=exc)
        return None
    if not response.success or not response.extra or not response.extra.parsed_model:
        log_llm_failure("CV edit", model_name, response=response)
        return None
    return response.extra.parsed_model
