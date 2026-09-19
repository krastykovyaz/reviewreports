"""Minimal i18n: each collector/module owns a flat {key: {lang: template}}
dict and looks strings up through `t()`. Templates use Python str.format
placeholders. English is the required, canonical language — every key must
have an "en" entry (enforced by tests) — ru/fr fall back to it if missing so
a partial translation never crashes or shows a raw key.
"""

from typing import Dict

SUPPORTED_LANGUAGES = ("en", "ru", "fr")
DEFAULT_LANGUAGE = "en"


def normalize_lang(lang: str) -> str:
    lang = (lang or DEFAULT_LANGUAGE).lower()
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def make_translator(messages: Dict[str, Dict[str, str]]):
    """Build a `t(key, lang, **kwargs)` bound to one module's message table."""

    def t(key: str, lang: str, **kwargs) -> str:
        entry = messages.get(key)
        if entry is None:
            return key
        template = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or next(iter(entry.values()))
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template

    return t
