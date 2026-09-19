"""Every message catalog must have en/ru/fr for every key, non-empty, with
matching sets of {placeholders} across languages (a template that forgets a
placeholder in one language would silently drop data or raise at render time)."""

import re

import pytest

from src.i18n.chrome import _CHROME, FLAVOR_LABELS, KIND_TITLES, PILLAR_NAMES
from src.i18n.core import SUPPORTED_LANGUAGES

import src.audit.collectors.accessibility as accessibility
import src.audit.collectors.content as content
import src.audit.collectors.links as links
import src.audit.collectors.performance as performance
import src.audit.collectors.privacy as privacy
import src.audit.collectors.security_headers as security_headers
import src.audit.collectors.seo as seo
import src.audit.collectors.tech_stack as tech_stack
import src.audit.collectors.ux as ux
import src.audit.website as website
import src.review.code_review as code_review
import src.review.document as document

_ALL_CATALOGS = {
    "chrome._CHROME": _CHROME,
    "chrome.PILLAR_NAMES": PILLAR_NAMES,
    "chrome.FLAVOR_LABELS": FLAVOR_LABELS,
    "chrome.KIND_TITLES": KIND_TITLES,
    "seo._M": seo._M,
    "security_headers._M": security_headers._M,
    "accessibility._M": accessibility._M,
    "performance._M": performance._M,
    "tech_stack._M": tech_stack._M,
    "links._M": links._M,
    "privacy._M": privacy._M,
    "content._M": content._M,
    "ux._M": ux._M,
    "website._M": website._M,
    "code_review._M": code_review._M,
    "document._M": document._M,
}


def _placeholders(template: str) -> set:
    return set(re.findall(r"\{(\w+)\}", template))


@pytest.mark.parametrize("catalog_name,catalog", _ALL_CATALOGS.items())
def test_catalog_has_all_languages_non_empty(catalog_name, catalog):
    missing = []
    empty = []
    for key, entry in catalog.items():
        for lang in SUPPORTED_LANGUAGES:
            if lang not in entry:
                missing.append(f"{catalog_name}[{key!r}] missing lang={lang}")
            elif not entry[lang] or not entry[lang].strip():
                empty.append(f"{catalog_name}[{key!r}][{lang}] is empty")
    assert not missing, "\n".join(missing)
    assert not empty, "\n".join(empty)


@pytest.mark.parametrize("catalog_name,catalog", _ALL_CATALOGS.items())
def test_catalog_placeholders_match_across_languages(catalog_name, catalog):
    mismatches = []
    for key, entry in catalog.items():
        en_placeholders = _placeholders(entry["en"])
        for lang in ("ru", "fr"):
            other = _placeholders(entry[lang])
            if other != en_placeholders:
                mismatches.append(f"{catalog_name}[{key!r}]: en has {en_placeholders}, {lang} has {other}")
    assert not mismatches, "\n".join(mismatches)


def test_no_lang_placeholder_collision():
    """A template using {lang} as a placeholder name would collide with the
    translator's own `lang` positional argument (TypeError at call time)."""
    offenders = []
    for catalog_name, catalog in _ALL_CATALOGS.items():
        for key, entry in catalog.items():
            for lang_code, template in entry.items():
                if "{lang}" in template:
                    offenders.append(f"{catalog_name}[{key!r}][{lang_code}]")
    assert not offenders, "\n".join(offenders)
