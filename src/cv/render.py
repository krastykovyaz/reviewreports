"""Renders a CVProfile into one of the three fixed one-page templates.

Layout/density for each template was validated separately in a browser
mockup against a Letter-ratio (8.5:11) frame with actual sample content, to
confirm it fits one page rather than merely asserting it. These templates
carry the same relative sizing that passed that check. That check was done
at a display scale, not true 8.5in-wide print units — re-verify against a
real PDF render (WeasyPrint) before relying on this for print output, since
proportional scaling to real page dimensions hasn't been checked yet.
"""

import os
from typing import Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.cv.schema import CVProfile

TEMPLATES: Tuple[str, ...] = ("modern", "classic", "compact")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))


def render_cv_html(profile: CVProfile, template: str) -> str:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. Must be one of {TEMPLATES}.")
    return _env.get_template(f"{template}.html").render(profile=profile)
