"""Post-confirmation evidence review and targeted follow-up question helpers."""

from __future__ import annotations

import re

from .audit_identity import audit_issue_family
from .models import (
    AuditIssue,
    CandidateAnswer,
    CandidateProfile,
    CandidateQuestion,
    TailoringProposal,
)

MAX_TARGETED_FOLLOW_UP_ROUNDS = 1
MAX_TARGETED_FOLLOW_UP_QUESTIONS = 3

_CANDIDATE_INFORMATION_FAMILIES = {
    "skill_evidence",
    "summary_evidence",
    "bullet_evidence",
    "requirement_evidence",
    "claim_evidence",
}

_QUOTED_PHRASE_RE = re.compile(r'["“]([^"”]{2,500})["”]|[\'‘]([^\'’]{2,500})[\'’]')


def audit_issue_requires_candidate_information(issue: AuditIssue) -> bool:
    """Return True when resolving a finding requires facts only the candidate can supply.

    Evidence-record bookkeeping and objective validation are intentionally excluded: the
    application should repair those itself. This function focuses on semantic claims that
    are unsupported, stronger than the evidence, or otherwise unverifiable.
    """
    return audit_issue_family(issue) in _CANDIDATE_INFORMATION_FAMILIES


def split_post_confirmation_issues(
    issues: list[AuditIssue],
) -> tuple[list[AuditIssue], list[AuditIssue]]:
    """Split findings into candidate questions and application-fixable findings."""
    candidate_needed: list[AuditIssue] = []
    auto_fixable: list[AuditIssue] = []
    for issue in issues:
        if audit_issue_requires_candidate_information(issue):
            candidate_needed.append(issue)
        elif issue.suggested_fix.strip():
            auto_fixable.append(issue)
    return candidate_needed, auto_fixable


def partition_targeted_follow_up_issues(
    issues: list[AuditIssue],
    *,
    max_questions: int = MAX_TARGETED_FOLLOW_UP_QUESTIONS,
) -> tuple[list[AuditIssue], list[AuditIssue]]:
    """Return the small question set and the findings to resolve conservatively.

    Blocking, source-specific findings are prioritized. Everything beyond the question
    limit must be handled with safer source-backed wording rather than another burden on
    the candidate.
    """
    indexed = list(enumerate(issues))
    # Only blocking, source-specific uncertainty is worth interrupting the user.
    # Advisory findings and broad claims are resolved with conservative wording.
    eligible = [
        item
        for item in indexed
        if item[1].severity == "blocking" and item[1].source_id.strip()
    ]
    ranked = sorted(eligible, key=lambda item: item[0])
    selected_indexes = {
        index for index, _ in ranked[: max(0, max_questions)]
    }
    selected = [
        issue for index, issue in indexed if index in selected_indexes
    ]
    conservative = [
        issue for index, issue in indexed if index not in selected_indexes
    ]
    return selected, conservative


def _requirement_id_for_issue(issue: AuditIssue, proposal: TailoringProposal) -> str:
    source_id = issue.source_id.strip()
    if source_id:
        bullet = next(
            (item for item in proposal.bullet_proposals if item.source_bullet_id == source_id),
            None,
        )
        if bullet and bullet.matched_requirement_ids:
            return bullet.matched_requirement_ids[0]
        if any(item.requirement_id == source_id for item in proposal.evidence_matches):
            return source_id

    text = f"{issue.issue} {issue.suggested_fix}"
    match = re.search(r"\bR\d+\b", text, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _quoted_phrases(value: str) -> list[str]:
    phrases: list[str] = []
    for match in _QUOTED_PHRASE_RE.finditer(value):
        phrase = (match.group(1) or match.group(2) or "").strip()
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _short_claim(value: str, *, limit: int = 280) -> str:
    cleaned = " ".join(value.split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _visible_claim(issue: AuditIssue, proposal: TailoringProposal) -> str:
    """Return the actual candidate-facing resume wording under review."""
    source_id = issue.source_id.strip()
    section = " ".join(issue.section.casefold().replace("_", " ").split())
    issue_quotes = _quoted_phrases(issue.issue)

    if "summary" in section or source_id.casefold() in {"summary", "professional_summary"}:
        for phrase in issue_quotes:
            if phrase in proposal.professional_summary:
                return _short_claim(phrase)
        return _short_claim(proposal.professional_summary)

    if source_id:
        bullet = next(
            (
                item
                for item in proposal.bullet_proposals
                if item.source_bullet_id == source_id and item.include
            ),
            None,
        )
        if bullet is not None:
            return _short_claim(bullet.proposed_text)

    if issue_quotes:
        return _short_claim(max(issue_quotes, key=len))
    return _short_claim(issue.issue)


def _candidate_facing_concern(issue: AuditIssue) -> str:
    family = audit_issue_family(issue)
    return {
        "skill_evidence": "The review could not verify that this skill was used in the selected role.",
        "summary_evidence": "The review could not verify the full strength of this summary statement.",
        "bullet_evidence": "The review could not verify every technology, responsibility, or level of ownership in this bullet.",
        "requirement_evidence": "The review could not verify that the résumé fully supports this job requirement.",
        "claim_evidence": "The review could not verify the full claim from the available evidence.",
    }.get(
        family,
        "The review could not verify the full claim from the available evidence.",
    )


def _question_text(issue: AuditIssue, proposal: TailoringProposal) -> str:
    source_id = issue.source_id.strip()
    section = " ".join(issue.section.casefold().replace("_", " ").split())
    claim = _visible_claim(issue, proposal)
    family = audit_issue_family(issue)

    if family == "skill_evidence":
        return f'Did you use the skill described here in the selected role: “{claim}”?'
    if "summary" in section or source_id.casefold() in {"summary", "professional_summary"}:
        return f'The Professional Summary says: “{claim}” Is this statement fully accurate?'
    if source_id:
        return f'The résumé bullet {source_id} says: “{claim}” Is the full statement accurate for that role?'
    return f'The résumé currently says: “{claim}” Is this statement fully accurate?'


def build_targeted_follow_up_questions(
    issues: list[AuditIssue],
    proposal: TailoringProposal,
    *,
    round_number: int,
) -> list[CandidateQuestion]:
    """Convert candidate-dependent findings into the single concise follow-up round."""
    selected, _ = partition_targeted_follow_up_issues(issues)
    questions: list[CandidateQuestion] = []
    safe_round = min(max(round_number, 1), MAX_TARGETED_FOLLOW_UP_ROUNDS)
    for index, issue in enumerate(selected, start=1):
        question_id = f"FQ{safe_round}-{index}"
        suggested = " ".join(issue.suggested_fix.split())
        help_text = (
            f"{_candidate_facing_concern(issue)} "
            "Choose No to use safer source-backed wording. Choose Yes only when the "
            "complete statement is accurate."
        )
        if suggested:
            help_text += f" If it is not confirmed, the application will use this safer correction: {suggested}"
        questions.append(
            CandidateQuestion(
                id=question_id,
                requirement_id=_requirement_id_for_issue(issue, proposal),
                source_id=issue.source_id.strip(),
                question=_question_text(issue, proposal),
                answer_type="yes_no_with_details",
                details_prompt=(
                    "Briefly state what you personally did in that role, including the "
                    "specific technology, responsibility, scope, or result needed to "
                    "support this exact statement."
                ),
                help_text=help_text,
                required=True,
            )
        )
    return questions


def apply_final_follow_up_answers_locally(
    profile: CandidateProfile,
    proposal: TailoringProposal,
    questions: list[CandidateQuestion],
    answers: list[CandidateAnswer],
) -> TailoringProposal:
    """Apply the last evidence answers without another model round trip.

    The targeted follow-up is created from an independent audit, so the final
    submission should only confirm new source evidence or choose a conservative
    source-backed fallback. Running another rewrite and another audit here made
    the interactive request vulnerable to gateway timeouts. Affirmative answers
    are already attached to ``profile`` by ``build_profile_with_candidate_answers``;
    this helper handles declined claims locally before deterministic validation.
    """
    updated = proposal.model_copy(deep=True)
    answer_lookup = {answer.question_id: answer for answer in answers}
    source_bullets = profile.bullet_lookup()
    proposal_bullets = {
        item.source_bullet_id: item for item in updated.bullet_proposals
    }

    for question in questions:
        answer = answer_lookup.get(question.id)
        if answer is None or answer.yes_no is not False:
            continue

        source_id = question.source_id.strip()
        normalized_source_id = source_id.casefold()

        if normalized_source_id in {"summary", "professional_summary"}:
            updated.professional_summary = profile.current_summary
            continue

        bullet = proposal_bullets.get(source_id)
        verified_text = source_bullets.get(source_id, "").strip()
        if bullet is not None and verified_text:
            bullet.proposed_text = verified_text
            bullet.evidence_note = (
                "The candidate did not confirm the stronger generated wording, "
                "so the verified source wording was restored."
            )
            continue

        evidence_match = next(
            (
                item
                for item in updated.evidence_matches
                if item.requirement_id == source_id
            ),
            None,
        )
        if evidence_match is not None:
            evidence_match.status = "unsupported"
            evidence_match.evidence_ids = []
            evidence_match.rationale = (
                "The candidate did not confirm this requirement during the final "
                "evidence follow-up."
            )
            for item in updated.bullet_proposals:
                item.matched_requirement_ids = [
                    requirement_id
                    for requirement_id in item.matched_requirement_ids
                    if requirement_id != source_id
                ]
            continue

        # Some skill findings use the skill itself as the source identifier. Remove
        # only an exact skill match; broader cleanup remains deterministic.
        if normalized_source_id:
            for field in (
                "hard_skills",
                "soft_skills",
                "tools_software",
                "industry_knowledge",
            ):
                values = getattr(updated.skills, field)
                setattr(
                    updated.skills,
                    field,
                    [
                        value
                        for value in values
                        if value.casefold() != normalized_source_id
                    ],
                )

    return updated
