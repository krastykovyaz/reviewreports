"""Generic structured report model shared by every review kind (website audit,
code review, resume review, ...). One schema, several renderers.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    BAD = "bad"
    NA = "na"


class Finding(BaseModel):
    check: str
    status: Status
    detail: str
    evidence: Optional[str] = Field(default=None, description="The observed value the verdict is based on")
    fix: Optional[str] = Field(default=None, description="How to resolve this, when status is warn/bad")


class Pillar(BaseModel):
    name: str
    score: Optional[float] = Field(default=None, ge=0, le=10)
    summary: str
    findings: List[Finding] = Field(default_factory=list)


class Recommendation(BaseModel):
    priority: int
    action: str
    rationale: str


class ReportMeta(BaseModel):
    kind: str
    subject: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "1.0"
    lang: str = Field(default="en", description="Language the report's text was generated in (en/ru/fr)")


class Report(BaseModel):
    meta: ReportMeta
    overall_score: Optional[float] = Field(default=None, ge=0, le=10)
    verdict: str = ""
    pillars: List[Pillar] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    methodology: str = ""
    limitations: str = ""

    def compute_overall_score(self) -> Optional[float]:
        """Average of scored pillars (N/A pillars are excluded, not zeroed)."""
        scored = [p.score for p in self.pillars if p.score is not None]
        if not scored:
            return None
        return round(sum(scored) / len(scored), 1)

    def finalize(self) -> "Report":
        """Fill in overall_score from pillars if not already set."""
        if self.overall_score is None:
            self.overall_score = self.compute_overall_score()
        return self

    def status_counts(self) -> Dict[str, int]:
        counts = {s.value: 0 for s in Status}
        for pillar in self.pillars:
            for finding in pillar.findings:
                counts[finding.status.value] += 1
        return counts

    @property
    def grade(self) -> str:
        return grade_for(self.overall_score)


def grade_for(score: Optional[float]) -> str:
    if score is None:
        return "N/A"
    if score >= 9:
        return "A"
    if score >= 7.5:
        return "B"
    if score >= 6:
        return "C"
    if score >= 4.5:
        return "D"
    return "F"
