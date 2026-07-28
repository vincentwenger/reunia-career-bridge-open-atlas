"""Shared Career Bridge domain foundation.

This package intentionally has no Flask, AWS, OpenAI, or document-generation
runtime dependency. Product-specific code remains in ``products/`` and will be
connected through adapters implementing the ports in :mod:`career_bridge.ports`.
"""

from career_bridge.domain.models import (
    AuthSession,
    CareerAction,
    CareerDocument,
    CareerJourney,
    CandidateEvidence,
    InterviewSession,
    ResumeArtifact,
    Score,
    Scorecard,
    SupportCase,
    Transcript,
    TranscriptSegment,
    UserProfile,
)

__all__ = [
    "AuthSession",
    "CareerAction",
    "CareerDocument",
    "CareerJourney",
    "CandidateEvidence",
    "InterviewSession",
    "ResumeArtifact",
    "Score",
    "Scorecard",
    "SupportCase",
    "Transcript",
    "TranscriptSegment",
    "UserProfile",
]
