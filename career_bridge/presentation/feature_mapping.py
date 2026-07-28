"""Canonical mapping from imported Réunia features to Career Bridge capabilities.

Legacy implementation names remain valid inside delivery adapters so existing data,
API clients, and migration scripts continue to work. Candidate-facing code should
use the Career Bridge labels and clean routes defined here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepurposedFeature:
    legacy_name: str
    career_bridge_name: str
    recommendation: str
    route: str | None = None
    available_in_mvp: bool = True


REPURPOSED_FEATURES: tuple[RepurposedFeature, ...] = (
    RepurposedFeature(
        "Meeting Preparation",
        "Interview Preparation",
        "Rewrite around the company, role, interviewer, and likely questions.",
        "/interview-preparation",
    ),
    RepurposedFeature(
        "Meeting Materials",
        "Application Materials",
        "Store the resume, job posting, company notes, and recruiter messages.",
        "/application-builder",
    ),
    RepurposedFeature(
        "AI Context",
        "Career Profile",
        "Store professional background, accomplishments, preferences, and constraints.",
        "/career-profile",
    ),
    RepurposedFeature(
        "Knowledge Search",
        "Career Evidence Library",
        "Search verified projects, achievements, and experience.",
        "/interview-preparation",
    ),
    RepurposedFeature(
        "Meeting Package",
        "Application Workspace",
        "Use one workspace for each target position.",
        "/application-builder",
    ),
    RepurposedFeature(
        "Browser Meeting Recorder",
        "Mock Interview Recorder",
        "Keep audio recording and transcription for practice sessions.",
        "/mock-interview",
    ),
    RepurposedFeature(
        "Windows Desktop Recorder",
        "Remove from MVP",
        "Avoid desktop-client complexity in the hackathon MVP.",
        available_in_mvp=False,
    ),
    RepurposedFeature(
        "Live Q&A",
        "Remove from real interviews",
        "Do not position the product as secretly answering questions during an employer interview.",
        available_in_mvp=False,
    ),
    RepurposedFeature(
        "Meeting Review",
        "Interview Review",
        "Analyze the candidate's mock-interview answers.",
        "/interview-review",
    ),
    RepurposedFeature(
        "Meeting Scorecard",
        "Interview Scorecard",
        "Score answer relevance, evidence, structure, clarity, and delivery.",
        "/interview-review",
    ),
    RepurposedFeature(
        "Action Center",
        "Career Action Plan",
        "Track resume changes, practice needs, applications, and follow-ups.",
        "/career-action-plan",
    ),
    RepurposedFeature(
        "Analytics",
        "Career Progress",
        "Show improvement across applications and mock interviews.",
        "/progress",
    ),
    RepurposedFeature(
        "Upcoming Meetings",
        "Upcoming Interviews",
        "Optional calendar integration for interview dates.",
        "/application-builder",
    ),
    RepurposedFeature(
        "Admin Analytics",
        "Admin Analytics",
        "Retain for product operations and keep hidden from normal users.",
        "/admin/analytics",
    ),
    RepurposedFeature(
        "Incidents",
        "Incidents",
        "Keep for technical monitoring and support.",
        "/admin/analytics",
    ),
)


def repurposed_features() -> tuple[RepurposedFeature, ...]:
    return REPURPOSED_FEATURES


def feature_by_legacy_name(name: str) -> RepurposedFeature:
    normalized = name.strip().casefold()
    for feature in REPURPOSED_FEATURES:
        if feature.legacy_name.casefold() == normalized:
            return feature
    raise KeyError(name)
