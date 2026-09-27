"""Structured input for the CV builder: what a user fills in (or an LLM
extracts from free text) before picking a template to render it into.

Deliberately separate from src/report/schema.py's Report/Pillar/Finding
shape — that schema is for scoring an existing artifact, this one is for
generating a new one. No overlap in fields or intent.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class Contact(BaseModel):
    email: str
    phone: Optional[str] = None
    location: Optional[str] = None
    links: List[str] = Field(default_factory=list, description="e.g. linkedin.com/in/..., github.com/...")


class Experience(BaseModel):
    title: str
    organization: str
    location: Optional[str] = None
    start: str = Field(description="e.g. '2021'")
    end: str = Field(default="Present", description="e.g. '2021', 'Present'")
    bullets: List[str] = Field(default_factory=list)


class Education(BaseModel):
    degree: str
    institution: str
    year: Optional[str] = None


class CVProfile(BaseModel):
    name: str
    role: Optional[str] = Field(default=None, description="Headline under the name, e.g. 'Senior Backend Engineer'")
    summary: Optional[str] = None
    contact: Contact
    experience: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list, description="e.g. 'English (native)'")
