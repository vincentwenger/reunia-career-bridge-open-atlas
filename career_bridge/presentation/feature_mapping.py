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
    available_to_standard_users: bool = True


REPURPOSED_FEATURES: tuple[RepurposedFeature, ...] = (
    RepurposedFeature(
        "Meeting Preparation",
        "Interview Preparation",
        "Rewrite around the company, role, interviewer, and likely questions.",
        "/applications/interview-preparation",
    ),
    RepurposedFeature(
        "Meeting Materials",
        "Application Materials",
        "Store the resume, job posting, company notes, and recruiter messages.",
        "/application-materials",
    ),
    RepurposedFeature(
        "AI Context",
        "Career Profile",
        "Store professional background, accomplishments, preferences, and constraints.",
        "/career-profile",
    ),
    RepurposedFeature(
        "Newcomer Career Onboarding",
        "Baseline Resume",
        "Translate international titles, credentials, terminology, and transferable skills without treating onboarding context as verified evidence.",
        "/applications/career-translation",
    ),
    RepurposedFeature(
        "Knowledge Search",
        "Career Evidence Library",
        "Search verified projects, achievements, and experience.",
        "/career-evidence-library",
    ),
    RepurposedFeature(
        "Meeting Package",
        "Application Workspace",
        "Use one workspace for each target position.",
        "/applications/?tab=applications",
    ),
    RepurposedFeature(
        "Browser Meeting Recorder",
        "Adaptive Mock Interview",
        "Run answer-by-answer practice with transcription, evidence-aware evaluation, adaptive follow-ups, challenges, and a final review.",
        "/mock-interview",
    ),
    RepurposedFeature(
        "Windows Desktop Recorder",
        "Retired",
        "Use the browser-based Adaptive Mock Interview instead of a desktop recorder.",
        available_to_standard_users=False,
    ),
    RepurposedFeature(
        "Live Q&A",
        "Restricted administration feature",
        "Do not expose real-time answer generation to standard candidate accounts.",
        available_to_standard_users=False,
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
        "Progress & Outcomes",
        "Track application outcomes, resume gains, automatic interview readiness, practice improvement, and completed actions.",
        "/progress",
    ),
    RepurposedFeature(
        "Upcoming Meetings",
        "Upcoming Interviews",
        "Optional calendar integration for interview dates.",
        "/applications",
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
