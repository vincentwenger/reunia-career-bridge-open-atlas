from __future__ import annotations

from typing import Any

from .models import CandidateProfile, TailoringProposal
from .proposal_integrity import repair_missing_bullet_proposals
from .confirmation import is_candidate_confirmed_bullet_id
from .text_diff import build_word_diff
from .validation import numeric_tokens, sentence_count, word_count

_SKILL_FIELDS = (
    ("hard_skills", "Hard Skills"),
    ("soft_skills", "Soft Skills"),
    ("tools_software", "Tools & Software"),
    ("industry_knowledge", "Industry Knowledge"),
)


def _ordered_added(before: list[str], after: list[str]) -> list[str]:
    before_keys = {item.casefold() for item in before}
    return [item for item in after if item.casefold() not in before_keys]


def _ordered_removed(before: list[str], after: list[str]) -> list[str]:
    after_keys = {item.casefold() for item in after}
    return [item for item in before if item.casefold() not in after_keys]


def _issue_texts_by_source(
    issue_summaries: list[dict[str, str]] | None,
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in issue_summaries or []:
        source_id = str(item.get("source_id", "")).strip()
        if not source_id:
            continue
        text = str(item.get("issue", "")).strip()
        if text:
            grouped.setdefault(source_id, []).append(text)
    return grouped


def _reason(
    code: str,
    label: str,
    detail: str,
    category: str,
) -> dict[str, str]:
    return {
        "code": code,
        "label": label,
        "detail": detail,
        "category": category,
    }




def _issues_for_section(
    issue_summaries: list[dict[str, str]] | None,
    section: str,
) -> list[str]:
    target = section.strip().casefold()
    return [
        str(item.get("issue", "")).strip()
        for item in (issue_summaries or [])
        if str(item.get("section", "")).strip().casefold() == target
        and str(item.get("issue", "")).strip()
    ]


def _summary_fix_reasons(
    before_text: str,
    after_text: str,
    issue_summaries: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    before_words = word_count(before_text)
    after_words = word_count(after_text)
    before_sentences = sentence_count(before_text)
    after_sentences = sentence_count(after_text)
    issues = _issues_for_section(issue_summaries, "Professional Summary")
    issue_detail = "; ".join(issues)

    if "adjacent repeated word" in issue_detail.casefold():
        label = "Removed a repeated word"
    elif before_words < 50 and after_words >= 50:
        label = "Expanded to meet the summary guideline"
    elif before_words > 80 and after_words <= 80:
        label = "Shortened for clarity"
    elif before_sentences not in (3, 4) and after_sentences in (3, 4):
        label = "Restructured for readability"
    else:
        label = "Revised to meet resume-quality guidelines"

    detail = (
        f"The professional summary changed from {before_words} to {after_words} words "
        f"and from {before_sentences} to {after_sentences} sentences."
    )
    if issue_detail:
        detail += f" This addressed: {issue_detail}"
    else:
        detail += " The wording was adjusted using only information already supported by the Candidate Profile."
    return [_reason("summary_quality_fix", label, detail, "summary_revised")]


def _skill_fix_reasons(
    *,
    added: list[str],
    removed: list[str],
    issue_summaries: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    issues = _issues_for_section(issue_summaries, "Skills")
    issue_text = " ".join(issues).casefold()
    if "adjacent repeated word" in issue_text:
        label = "Removed a repeated word"
    elif "duplicate" in issue_text:
        label = "Removed duplicate skills"
    elif ("maximum is 16" in issue_text or "maximum is 30" in issue_text or "contains" in issue_text and "skills" in issue_text):
        label = "Focused the skills section"
    elif "verified skills" in issue_text or "not in the candidate" in issue_text:
        label = "Aligned skills with verified experience"
    elif removed and not added:
        label = "Focused the skills section"
    else:
        label = "Aligned skills with the Candidate Profile"

    if issues:
        detail = "The skills list was updated to address: " + "; ".join(issues)
    else:
        detail = "The skills list was cleaned up to keep it concise, non-duplicative, and supported by the Candidate Profile."
    return [_reason("skills_quality_fix", label, detail, "skills_revised")]


def _bullet_fix_reasons(
    *,
    source_id: str,
    source_text: str,
    experience_id: str,
    old,
    new,
    direct_issues: list[str],
    experience_issues: list[str],
    supplemental_numbers: set[str],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    old_text = old.proposed_text if old is not None else ""
    new_text = new.proposed_text if new is not None else ""
    old_words = word_count(old_text)
    new_words = word_count(new_text)
    issue_text = " ".join(direct_issues).casefold()

    if old is None and new is not None:
        if new.include:
            reasons.append(
                _reason(
                    "restored_missing_bullet",
                    "Restored and included missing bullet",
                    "The structured Draft was missing this source bullet. The automatic fix restored it from the Candidate Profile and selected it for the current resume, so it is included in the Word download.",
                    "structure_restored",
                )
            )
        else:
            reasons.append(
                _reason(
                    "restored_missing_bullet_excluded",
                    "Restored missing bullet record — not included",
                    "The structured Draft was missing this source bullet. Its source-backed record was restored, but it could not be selected because this experience had already reached its allowed bullet-count limit. It will not appear in the Word download unless another bullet is excluded.",
                    "structure_restored_excluded",
                )
            )

    if old is not None and new is not None and old_text != new_text:
        source_numbers = numeric_tokens(source_text)
        unsupported_numbers = numeric_tokens(old_text) - source_numbers - supplemental_numbers
        restored_source = bool(source_text and new_text == source_text)

        if "adjacent repeated word" in issue_text:
            reasons.append(
                _reason(
                    "removed_adjacent_repeated_word",
                    "Removed a repeated word",
                    "The automatic language check removed an accidental adjacent word repetition while preserving the rest of the verified bullet wording.",
                    "wording_revised",
                )
            )
        elif restored_source and (not old_text.strip() or "empty" in issue_text):
            reasons.append(
                _reason(
                    "restored_empty_source_wording",
                    "Restored source wording",
                    "The previous Draft bullet was empty, so the evidence-backed Candidate Profile wording was restored.",
                    "source_wording_restored",
                )
            )
        elif restored_source and (unsupported_numbers or "introduces new number" in issue_text):
            number_text = ", ".join(sorted(unsupported_numbers))
            detail = (
                f"The previous Draft introduced unsupported number(s) ({number_text}), so the Candidate Profile wording was restored."
                if number_text
                else "The previous Draft introduced unsupported numbers, so the Candidate Profile wording was restored."
            )
            reasons.append(
                _reason(
                    "restored_unsupported_numbers",
                    "Restored source wording",
                    detail,
                    "source_wording_restored",
                )
            )
        elif restored_source and (old_words > 55 or "bullet is long" in issue_text):
            reasons.append(
                _reason(
                    "restored_overlong_source_wording",
                    "Restored source wording",
                    f"The previous Draft bullet was {old_words} words and exceeded the 55-word guideline. The Candidate Profile wording was restored as the safe deterministic replacement.",
                    "source_wording_restored",
                )
            )
        elif restored_source:
            reasons.append(
                _reason(
                    "restored_source_wording",
                    "Restored source wording",
                    "The automatic cleanup replaced the Draft wording with the evidence-backed Candidate Profile wording to resolve the validation finding without inventing information.",
                    "source_wording_restored",
                )
            )
        elif new_words < old_words:
            reasons.append(
                _reason(
                    "shortened_wording",
                    "Shortened for readability",
                    f"The wording was reduced from {old_words} to {new_words} words while applying the validation fix.",
                    "shortened",
                )
            )
        elif new_words > old_words:
            cause = direct_issues[0] if direct_issues else "the selected validation finding"
            reasons.append(
                _reason(
                    "expanded_wording",
                    "Expanded to address validation",
                    f"The wording increased from {old_words} to {new_words} words while addressing: {cause}",
                    "expanded",
                )
            )
        else:
            cause = direct_issues[0] if direct_issues else "the selected validation finding"
            reasons.append(
                _reason(
                    "revised_wording",
                    "Revised to address validation",
                    f"The wording was revised while addressing: {cause}",
                    "wording_revised",
                )
            )

    if old is not None and new is not None and old.include != new.include:
        range_detail = experience_issues[0] if experience_issues else "the required bullet-count range for this experience"
        if new.include:
            reasons.append(
                _reason(
                    "included_for_range",
                    "Included to meet the bullet range",
                    f"This bullet was selected while resolving: {range_detail}",
                    "included",
                )
            )
        else:
            reasons.append(
                _reason(
                    "excluded_for_range",
                    "Excluded to meet the bullet range",
                    f"This bullet was deselected while resolving: {range_detail}",
                    "excluded",
                )
            )

    if old is not None and new is not None:
        removed_requirements = [
            item for item in old.matched_requirement_ids if item not in new.matched_requirement_ids
        ]
        if removed_requirements and "unknown job requirements" in issue_text:
            reasons.append(
                _reason(
                    "removed_unknown_requirements",
                    "Removed unknown requirement references",
                    "Invalid job-requirement IDs were removed from this bullet's metadata. The visible bullet wording may be unchanged.",
                    "metadata_cleaned",
                )
            )
        if old.evidence_note != new.evidence_note and not old.evidence_note.strip():
            reasons.append(
                _reason(
                    "restored_evidence_note",
                    "Restored evidence note",
                    "A source-backed evidence note was added because the previous structured bullet did not contain one.",
                    "metadata_cleaned",
                )
            )

    return reasons


def _experience_change_groups(
    bullet_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group concrete bullet outcomes for the validation-details panel.

    Validation issues are often recorded at an experience or proposal level. The
    deterministic repair can then restore, include, exclude, or revise several
    bullets as a consequence. Grouping the actual outcomes by experience keeps
    those downstream changes visible beside the suggestions that triggered them.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for change in bullet_changes:
        reasons = change.get("automatic_fix", {}).get("reasons", [])
        if not reasons:
            continue
        entries = groups.setdefault(change.get("context") or "Resume experience", [])
        entries.append(
            {
                "source_id": change.get("source_id", ""),
                "before_include": bool(change.get("before_include")),
                "after_include": bool(change.get("after_include")),
                "wording_changed": bool(change.get("wording_changed")),
                "reasons": [
                    {
                        "label": str(reason.get("label", "Automatic change")),
                        "detail": str(reason.get("detail", "")),
                    }
                    for reason in reasons
                ],
            }
        )

    return [
        {"context": context, "changes": changes}
        for context, changes in groups.items()
    ]


def _reason_summary(bullet_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    category_labels = {
        "source_wording_restored": ("source wording restoration", "source wording restorations"),
        "structure_restored": ("missing bullet restoration included in the resume", "missing bullet restorations included in the resume"),
        "structure_restored_excluded": ("missing bullet record restored but not included", "missing bullet records restored but not included"),
        "shortened": ("shortening edit", "shortening edits"),
        "expanded": ("validation-driven expansion", "validation-driven expansions"),
        "wording_revised": ("other wording revision", "other wording revisions"),
        "included": ("bullet inclusion", "bullet inclusions"),
        "excluded": ("bullet exclusion", "bullet exclusions"),
        "metadata_cleaned": ("metadata cleanup", "metadata cleanups"),
    }
    counts: dict[str, int] = {}
    for change in bullet_changes:
        for reason in change.get("automatic_fix", {}).get("reasons", []):
            category = reason.get("category", "")
            if category:
                counts[category] = counts.get(category, 0) + 1
    summary: list[dict[str, Any]] = []
    for category, labels in category_labels.items():
        count = counts.get(category, 0)
        if count:
            summary.append(
                {
                    "category": category,
                    "count": count,
                    "label": labels[0 if count == 1 else 1],
                }
            )
    return summary


def summarize_proposal_changes(
    before: TailoringProposal,
    after: TailoringProposal,
    profile: CandidateProfile,
    *,
    issue_summaries: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a safe, user-facing summary of changes between two proposal snapshots."""
    source_context: dict[str, str] = {}
    source_text: dict[str, str] = {}
    source_experience: dict[str, str] = {}
    for experience in profile.experiences:
        for bullet in experience.bullets:
            source_context[bullet.id] = f"{experience.title} — {experience.employer}"
            source_text[bullet.id] = bullet.text
            source_experience[bullet.id] = experience.id
    issues_by_source = _issue_texts_by_source(issue_summaries)
    supplemental_numbers = {
        token
        for evidence in profile.supplemental_evidence
        for token in numeric_tokens(evidence.statement)
    }

    summary_change = None
    if before.professional_summary != after.professional_summary:
        before_html, after_html = build_word_diff(
            before.professional_summary, after.professional_summary
        )
        summary_change = {
            "before": before.professional_summary,
            "after": after.professional_summary,
            "before_html": before_html,
            "after_html": after_html,
            "automatic_fix": {
                "reasons": _summary_fix_reasons(
                    before.professional_summary,
                    after.professional_summary,
                    issue_summaries,
                ),
                "before_word_count": word_count(before.professional_summary),
                "after_word_count": word_count(after.professional_summary),
                "word_delta": (
                    word_count(after.professional_summary)
                    - word_count(before.professional_summary)
                ),
            },
        }

    skill_changes: list[dict[str, Any]] = []
    for field_name, label in _SKILL_FIELDS:
        before_items = list(getattr(before.skills, field_name))
        after_items = list(getattr(after.skills, field_name))
        added = _ordered_added(before_items, after_items)
        removed = _ordered_removed(before_items, after_items)
        if added or removed:
            skill_changes.append(
                {
                    "category": label,
                    "added": added,
                    "removed": removed,
                    "before_count": len(before_items),
                    "after_count": len(after_items),
                    "automatic_fix": {
                        "reasons": _skill_fix_reasons(
                            added=added,
                            removed=removed,
                            issue_summaries=issue_summaries,
                        )
                    },
                }
            )

    before_bullets = {item.source_bullet_id: item for item in before.bullet_proposals}
    after_bullets = {item.source_bullet_id: item for item in after.bullet_proposals}
    bullet_changes: list[dict[str, Any]] = []
    for source_id in sorted(set(before_bullets) | set(after_bullets)):
        old = before_bullets.get(source_id)
        new = after_bullets.get(source_id)
        if old is None or new is None:
            before_text = old.proposed_text if old else ""
            after_text = new.proposed_text if new else ""
            before_html, after_html = build_word_diff(before_text, after_text)
            experience_id = source_experience.get(source_id, "")
            reasons = _bullet_fix_reasons(
                source_id=source_id,
                source_text=source_text.get(source_id, ""),
                experience_id=experience_id,
                old=old,
                new=new,
                direct_issues=issues_by_source.get(source_id, []),
                experience_issues=issues_by_source.get(experience_id, []),
                supplemental_numbers=supplemental_numbers,
            )
            bullet_changes.append(
                {
                    "source_id": source_id,
                    "context": source_context.get(source_id, "Resume experience"),
                    "include_changed": True,
                    "before_include": bool(old and old.include),
                    "after_include": bool(new and new.include),
                    "wording_changed": True,
                    "before_text": before_text if old else "Bullet was not present.",
                    "after_text": after_text if new else "Bullet was removed.",
                    "before_html": before_html,
                    "after_html": after_html,
                    "before_word_count": word_count(before_text),
                    "after_word_count": word_count(after_text),
                    "evidence_note_changed": True,
                    "before_evidence_note": old.evidence_note if old else "",
                    "after_evidence_note": new.evidence_note if new else "",
                    "requirements_added": list(new.matched_requirement_ids) if new else [],
                    "requirements_removed": list(old.matched_requirement_ids) if old else [],
                    "automatic_fix": {
                        "reasons": reasons,
                        "before_word_count": word_count(before_text),
                        "after_word_count": word_count(after_text),
                        "word_delta": word_count(after_text) - word_count(before_text),
                    },
                }
            )
            continue

        wording_changed = old.proposed_text != new.proposed_text
        include_changed = old.include != new.include
        evidence_note_changed = old.evidence_note != new.evidence_note
        requirements_added = [
            item for item in new.matched_requirement_ids if item not in old.matched_requirement_ids
        ]
        requirements_removed = [
            item for item in old.matched_requirement_ids if item not in new.matched_requirement_ids
        ]
        if not (
            wording_changed
            or include_changed
            or evidence_note_changed
            or requirements_added
            or requirements_removed
        ):
            continue
        before_html, after_html = build_word_diff(old.proposed_text, new.proposed_text)
        experience_id = source_experience.get(source_id, "")
        reasons = _bullet_fix_reasons(
            source_id=source_id,
            source_text=source_text.get(source_id, ""),
            experience_id=experience_id,
            old=old,
            new=new,
            direct_issues=issues_by_source.get(source_id, []),
            experience_issues=issues_by_source.get(experience_id, []),
            supplemental_numbers=supplemental_numbers,
        )
        before_words = word_count(old.proposed_text)
        after_words = word_count(new.proposed_text)
        bullet_changes.append(
            {
                "source_id": source_id,
                "context": source_context.get(source_id, "Resume experience"),
                "include_changed": include_changed,
                "before_include": old.include,
                "after_include": new.include,
                "wording_changed": wording_changed,
                "before_text": old.proposed_text,
                "after_text": new.proposed_text,
                "before_html": before_html,
                "after_html": after_html,
                "before_word_count": before_words,
                "after_word_count": after_words,
                "evidence_note_changed": evidence_note_changed,
                "before_evidence_note": old.evidence_note,
                "after_evidence_note": new.evidence_note,
                "requirements_added": requirements_added,
                "requirements_removed": requirements_removed,
                "automatic_fix": {
                    "reasons": reasons,
                    "before_word_count": before_words,
                    "after_word_count": after_words,
                    "word_delta": after_words - before_words,
                },
            }
        )

    before_evidence = {item.requirement_id: item for item in before.evidence_matches}
    after_evidence = {item.requirement_id: item for item in after.evidence_matches}
    evidence_changes: list[dict[str, Any]] = []
    for requirement_id in sorted(set(before_evidence) | set(after_evidence)):
        old = before_evidence.get(requirement_id)
        new = after_evidence.get(requirement_id)
        old_status = old.status if old else "missing"
        new_status = new.status if new else "missing"
        old_ids = list(old.evidence_ids) if old else []
        new_ids = list(new.evidence_ids) if new else []
        old_rationale = old.rationale if old else ""
        new_rationale = new.rationale if new else ""
        if (
            old_status == new_status
            and old_ids == new_ids
            and old_rationale == new_rationale
        ):
            continue
        evidence_changes.append(
            {
                "requirement_id": requirement_id,
                "before_status": old_status,
                "after_status": new_status,
                "evidence_added": [item for item in new_ids if item not in old_ids],
                "evidence_removed": [item for item in old_ids if item not in new_ids],
                "rationale_changed": old_rationale != new_rationale,
                "before_rationale": old_rationale,
                "after_rationale": new_rationale,
            }
        )

    unsupported_added = _ordered_added(
        before.unsupported_requirements, after.unsupported_requirements
    )
    unsupported_removed = _ordered_removed(
        before.unsupported_requirements, after.unsupported_requirements
    )

    total_changes = (
        int(summary_change is not None)
        + len(skill_changes)
        + len(bullet_changes)
        + len(evidence_changes)
        + len(unsupported_added)
        + len(unsupported_removed)
    )
    return {
        "has_changes": total_changes > 0,
        "total_changes": total_changes,
        "summary_change": summary_change,
        "skill_changes": skill_changes,
        "bullet_changes": bullet_changes,
        "evidence_changes": evidence_changes,
        "unsupported_added": unsupported_added,
        "unsupported_removed": unsupported_removed,
        "resume_visible_change_count": (
            int(summary_change is not None) + len(skill_changes) + len(bullet_changes)
        ),
        "metadata_change_count": (
            len(evidence_changes) + len(unsupported_added) + len(unsupported_removed)
        ),
        "summary_changed": summary_change is not None,
        "skill_added_count": sum(len(item["added"]) for item in skill_changes),
        "skill_removed_count": sum(len(item["removed"]) for item in skill_changes),
        "bullet_reason_summary": _reason_summary(bullet_changes),
        "experience_change_groups": _experience_change_groups(bullet_changes),
    }


def summarize_tailoring_changes(
    before: TailoringProposal,
    after: TailoringProposal,
    profile: CandidateProfile,
    analysis,
    *,
    reference_title: str = "",
    current_title: str = "",
) -> dict[str, Any]:
    """Explain the evidence-based tailoring choices between the Initial Resume and Job-Aligned Resume.

    Unlike ``summarize_proposal_changes``, this summary does not describe deterministic
    validation fixes. It explains the generated proposal using the requirement matches,
    inclusion decisions, and evidence notes returned with the tailored Draft.
    """
    before_source_ids = {item.source_bullet_id for item in before.bullet_proposals}
    before = repair_missing_bullet_proposals(profile, before)
    before.bullet_proposals = [
        item
        for item in before.bullet_proposals
        if not is_candidate_confirmed_bullet_id(item.source_bullet_id)
        or item.source_bullet_id in before_source_ids
    ]
    after = repair_missing_bullet_proposals(profile, after)
    raw = summarize_proposal_changes(before, after, profile)
    requirement_lookup = {
        item.id: item
        for item in (analysis.requirements if analysis is not None else [])
    }
    after_lookup = {
        item.source_bullet_id: item for item in after.bullet_proposals
    }
    confirmed_evidence = [
        item
        for item in profile.supplemental_evidence
        if item.source == "candidate_confirmation" or item.id.startswith("CONF-")
    ]
    confirmed_evidence_ids = {item.id.casefold() for item in confirmed_evidence}
    confirmed_skills = {
        skill.casefold()
        for item in confirmed_evidence
        for skill in item.verified_skills
        if skill.strip()
    }

    def change_category_for_bullet(item) -> str:
        evidence_note = item.evidence_note.casefold()
        if any(evidence_id in evidence_note for evidence_id in confirmed_evidence_ids):
            return "Verified Experience"
        return "Job Alignment"

    bullet_details: dict[str, dict[str, Any]] = {}
    requirement_rewrites = 0
    other_rewrites = 0
    exclusions = 0

    for change in raw["bullet_changes"]:
        source_id = change["source_id"]
        item = after_lookup.get(source_id)
        if item is None:
            # Inputs are repaired above. This branch is retained only as a defensive
            # safeguard for malformed data and is intentionally not surfaced as a
            # user-facing tailoring decision.
            continue

        matched_requirements = [
            requirement_lookup[requirement_id]
            for requirement_id in item.matched_requirement_ids
            if requirement_id in requirement_lookup
        ]
        requirement_labels = [
            f"{requirement.id}: {requirement.requirement}"
            for requirement in matched_requirements
        ]
        reasons: list[dict[str, str]] = []

        rewritten_prefix = "User marked this source bullet as rewritten as "
        rewritten_as_id = ""
        if not item.include and item.evidence_note.startswith(rewritten_prefix):
            rewritten_as_id = (
                item.evidence_note[len(rewritten_prefix):]
                .split(".", 1)[0]
                .strip()
            )

        if rewritten_as_id:
            reasons.append(
                _reason(
                    "tailoring_rewritten",
                    f"Rewritten as {rewritten_as_id}",
                    (
                        "The user identified the included bullet "
                        f"{rewritten_as_id} as the replacement for this source bullet. "
                        "It is tracked as a rewrite, not an exclusion."
                    ),
                    "rewritten",
                )
            )
        elif change["include_changed"] and not item.include:
            exclusions += 1
            if requirement_labels:
                reasons.append(
                    _reason(
                        "tailoring_excluded_with_matches",
                        "Not selected for the tailored Draft",
                        (
                            "The proposal matched this accomplishment to "
                            + "; ".join(requirement_labels)
                            + ", but did not select it for the Draft. No more specific "
                              "exclusion rationale was returned, so the source bullet remains "
                              "available in the Initial Resume for manual restoration."
                        ),
                        "excluded",
                    )
                )
            else:
                reasons.append(
                    _reason(
                        "tailoring_excluded_without_match",
                        "Not selected for the tailored Draft",
                        (
                            "No job requirement was matched to this accomplishment, so the "
                            "generated proposal left it out of the Draft. It remains unchanged "
                            "in the Initial Resume and can be restored manually."
                        ),
                        "excluded",
                    )
                )

        if change["include_changed"] and item.include:
            if requirement_labels:
                reasons.append(
                    _reason(
                        "tailoring_selected_with_matches",
                        "Selected for the tailored resume",
                        (
                            "This accomplishment was added because it supports "
                            + "; ".join(requirement_labels)
                            + "."
                        ),
                        "selected",
                    )
                )
            else:
                reasons.append(
                    _reason(
                        "tailoring_selected_without_match",
                        "Selected for the tailored resume",
                        (
                            "The generated proposal selected this verified accomplishment for the working resume, although no specific requirement match was recorded."
                        ),
                        "selected",
                    )
                )

        if change["wording_changed"] and item.include:
            if requirement_labels:
                requirement_rewrites += 1
                reasons.append(
                    _reason(
                        "tailoring_requirement_emphasis",
                        "Reworded to emphasize job-relevant evidence",
                        (
                            "The wording was adapted to make the documented accomplishment "
                            "more directly relevant to "
                            + "; ".join(requirement_labels)
                            + "."
                        ),
                        "requirement_emphasis",
                    )
                )
            else:
                other_rewrites += 1
                reasons.append(
                    _reason(
                        "tailoring_clarity_revision",
                        "Refined for clarity and focus",
                        (
                            "The wording was adjusted while preserving the source "
                            "accomplishment. No specific job-requirement match was recorded "
                            "for this bullet."
                        ),
                        "clarity",
                    )
                )

        if reasons and item.evidence_note.strip():
            reasons.append(
                _reason(
                    "tailoring_evidence_basis",
                    "Evidence basis",
                    item.evidence_note.strip(),
                    "evidence",
                )
            )

        if reasons:
            change_category = change_category_for_bullet(item)
            for reason in reasons:
                reason["change_category"] = change_category
            bullet_details[source_id] = {
                "change_category": change_category,
                "reasons": reasons,
                "before_word_count": change["before_word_count"],
                "after_word_count": change["after_word_count"],
                "word_delta": (
                    change["after_word_count"] - change["before_word_count"]
                ),
                "matched_requirements": requirement_labels,
                "matched_requirement_ids": list(item.matched_requirement_ids),
            }

    title_changed = bool(
        reference_title
        and current_title
        and reference_title.strip() != current_title.strip()
    )
    reason_summary: list[dict[str, Any]] = []
    if title_changed:
        reason_summary.append(
            {"count": 1, "label": "target-title alignment", "category": "title"}
        )
    if raw["summary_changed"]:
        reason_summary.append(
            {"count": 1, "label": "professional summary rewrite", "category": "summary"}
        )
    if raw["skill_added_count"]:
        count = raw["skill_added_count"]
        reason_summary.append(
            {
                "count": count,
                "label": "skill addition" if count == 1 else "skill additions",
                "category": "skills_added",
            }
        )
    if raw["skill_removed_count"]:
        count = raw["skill_removed_count"]
        reason_summary.append(
            {
                "count": count,
                "label": "skill removal" if count == 1 else "skill removals",
                "category": "skills_removed",
            }
        )
    if requirement_rewrites:
        reason_summary.append(
            {
                "count": requirement_rewrites,
                "label": (
                    "requirement-focused bullet rewrite"
                    if requirement_rewrites == 1
                    else "requirement-focused bullet rewrites"
                ),
                "category": "requirement_emphasis",
            }
        )
    if other_rewrites:
        reason_summary.append(
            {
                "count": other_rewrites,
                "label": (
                    "clarity-focused bullet rewrite"
                    if other_rewrites == 1
                    else "clarity-focused bullet rewrites"
                ),
                "category": "clarity",
            }
        )
    if exclusions:
        reason_summary.append(
            {
                "count": exclusions,
                "label": "bullet exclusion" if exclusions == 1 else "bullet exclusions",
                "category": "excluded",
            }
        )

    skill_details = {}
    for item in raw["skill_changes"]:
        uses_confirmed_skill = any(
            skill.casefold() in confirmed_skills for skill in item["added"]
        )
        skill_details[item["category"]] = {
            "label": (
                "Adjusted job-relevant skills"
                if item["added"] and item["removed"]
                else "Added supported job-relevant skills"
                if item["added"]
                else "Reduced less relevant skills"
            ),
            "detail": (
                "The skills section was focused on terminology from the target role while keeping only skills supported by the Candidate Profile."
            ),
            "added": list(item["added"]),
            "removed": list(item["removed"]),
            "change_category": (
                "Verified Experience" if uses_confirmed_skill else "Job Alignment"
            ),
        }

    verified_experience_change_count = sum(
        1
        for detail in bullet_details.values()
        if detail.get("change_category") == "Verified Experience"
    ) + sum(
        1
        for detail in skill_details.values()
        if detail.get("change_category") == "Verified Experience"
    )
    total_changes = raw["total_changes"] + int(title_changed)
    return {
        **raw,
        "has_changes": total_changes > 0,
        "total_changes": total_changes,
        "title_changed": title_changed,
        "reference_title": reference_title,
        "current_title": current_title,
        "title_reason": {
            "label": "Aligned with the target role",
            "detail": (
                "The profile title was changed to match the analyzed target position while the experience content remains grounded in the Candidate Profile."
            ),
            "change_category": "Job Alignment",
        } if title_changed else None,
        "summary_reason": {
            "label": "Focused the opening on the target role",
            "detail": (
                "The professional summary was rewritten to foreground the most relevant verified experience and target-role terminology."
            ),
            "change_category": "Job Alignment",
        } if raw["summary_changed"] else None,
        "skill_details": skill_details,
        "primary_change_category": "Job Alignment",
        "verified_experience_available": bool(confirmed_evidence),
        "verified_experience_change_count": verified_experience_change_count,
        "reason_summary": reason_summary,
        "bullet_details": bullet_details,
        "requirement_rewrite_count": requirement_rewrites,
        "clarity_rewrite_count": other_rewrites,
        "excluded_bullet_count": exclusions,
        "report_impacts": {
            "available": False,
            "title": None,
            "summary": None,
            "skills": {},
            "requirements": {},
        },
    }
