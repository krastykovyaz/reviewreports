"""Shared logic for scoring pillars and turning findings into recommendations,
used by every review kind so they all score and render the same way.
"""

from typing import Dict, List, Optional

from src.i18n import normalize_lang, t_chrome
from src.report.schema import Finding, Pillar, Recommendation, Status

_STATUS_CREDIT = {Status.OK: 1.0, Status.WARN: 0.5, Status.BAD: 0.0}


def score_findings(findings: List[Finding], weights: Optional[Dict[str, float]] = None) -> Optional[float]:
    """0-10 score: weighted share of credit across findings (OK=1, WARN=0.5, BAD=0).

    N/A findings carry no weight. Returns None when nothing was scorable.
    """
    weights = weights or {}
    total = 0.0
    earned = 0.0
    for finding in findings:
        if finding.status == Status.NA:
            continue
        weight = weights.get(finding.check, 1.0)
        total += weight
        earned += weight * _STATUS_CREDIT[finding.status]
    if total == 0:
        return None
    return round(10.0 * earned / total, 1)


def build_recommendations(pillars: List[Pillar], lang: str = "en") -> List[Recommendation]:
    """Every BAD finding becomes a high-priority fix, every WARN a lower-priority
    one. The rationale is the finding's fix guidance when it has one."""
    lang = normalize_lang(lang)
    recs: List[Recommendation] = []
    priority = 1
    for status, verb_key in ((Status.BAD, "recommendation.fix"), (Status.WARN, "recommendation.improve")):
        verb = t_chrome(verb_key, lang)
        for pillar in pillars:
            for finding in pillar.findings:
                if finding.status == status:
                    recs.append(
                        Recommendation(
                            priority=priority,
                            action=f"[{pillar.name}] {verb}: {finding.check}",
                            rationale=finding.fix or finding.detail,
                        )
                    )
                    priority += 1
    return recs


def verdict_for(score: Optional[float], lang: str = "en") -> str:
    lang = normalize_lang(lang)
    if score is None:
        return t_chrome("verdict.unknown", lang)
    if score >= 8:
        return t_chrome("verdict.strong", lang)
    if score >= 5:
        return t_chrome("verdict.solid", lang)
    return t_chrome("verdict.poor", lang)
