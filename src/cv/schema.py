"""Structured input for the CV builder: what a user fills in (or an LLM
extracts from free text) before picking a template to render it into.

Deliberately separate from src/report/schema.py's Report/Pillar/Finding
shape — that schema is for scoring an existing artifact, this one is for
generating a new one. No overlap in fields or intent.
"""

from typing import Annotated, List, Optional

from pydantic import BaseModel, Field, StringConstraints

# POST /cv (unlike /cv/from-text and /cv/{id}/edit) never touches an LLM, so
# there's no per-request cost to bound - these exist for the same reason as
# any other public, unauthenticated input: an unbounded field is an
# unbounded row in a database that's never cleaned up (see the validation
# report's PII-retention finding) and an unbounded render target. Limits are
# generous for anything a real CV would ever contain.
_Short = Annotated[str, StringConstraints(max_length=200)]
_Long = Annotated[str, StringConstraints(max_length=5000)]


class Contact(BaseModel):
    email: Annotated[str, StringConstraints(max_length=320)]  # RFC 5321's own limit
    phone: Optional[Annotated[str, StringConstraints(max_length=40)]] = None
    location: Optional[_Short] = None
    links: List[_Short] = Field(default_factory=list, max_length=50, description="e.g. linkedin.com/in/..., github.com/...")


class Experience(BaseModel):
    title: _Short
    organization: _Short
    location: Optional[_Short] = None
    start: Annotated[str, StringConstraints(max_length=40)] = Field(description="e.g. '2021'")
    end: Annotated[str, StringConstraints(max_length=40)] = Field(default="Present", description="e.g. '2021', 'Present'")
    bullets: List[_Long] = Field(default_factory=list, max_length=50)


class Education(BaseModel):
    degree: _Short
    institution: _Short
    year: Optional[Annotated[str, StringConstraints(max_length=20)]] = None


class CVProfile(BaseModel):
    name: _Short
    role: Optional[_Short] = Field(default=None, description="Headline under the name, e.g. 'Senior Backend Engineer'")
    summary: Optional[_Long] = None
    contact: Contact
    experience: List[Experience] = Field(default_factory=list, max_length=50)
    education: List[Education] = Field(default_factory=list, max_length=50)
    skills: List[_Short] = Field(default_factory=list, max_length=100)
    languages: List[_Short] = Field(default_factory=list, max_length=50, description="e.g. 'English (native)'")
