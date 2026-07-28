from __future__ import annotations

from io import BytesIO

from docx import Document

from resume_tailor.bullet_text import (
    has_bullet_structure_artifacts,
    normalize_resume_bullet_text,
)
from resume_tailor.confirmation import build_profile_with_candidate_answers
from resume_tailor.deterministic_fixes import apply_all_until_valid
from resume_tailor.docx_export import export_resume_docx
from resume_tailor.docx_styles import STYLE_BULLET
from resume_tailor.models import CandidateAnswer, CandidateQuestion
from resume_tailor.validation import build_approved_resume, word_count


MARKDOWN_CONFIRMATION = """• * **Automation Overview:** Championed the automation of the entire software development lifecycle (SDLC)—from code integration and security testing to containerization and cloud infrastructure deployment—to eliminate manual toil and human error.

* **Specific Tools & Practices:**

* **CI/CD Pipelines & Version Control:** Configured and managed automated build, test, and deployment pipelines using **Jenkins** integrated with Git, ensuring that every code commit automatically triggered rigorous validation and packaging routines before reaching staging or production.

* **Containerization:** Standardized runtime environments across development, testing, and production using **Docker**, transforming complex multi-service architectures into portable, repeatable containers.

* **Cloud Infrastructure & Provisioning:** Automated infrastructure deployment and database management within **AWS**, utilizing scripting, environment management, and container orchestration.
"""


def test_markdown_confirmation_becomes_one_concise_plain_resume_bullet():
    cleaned = normalize_resume_bullet_text(MARKDOWN_CONFIRMATION)

    assert cleaned.startswith("Championed the automation")
    assert "Automation Overview" not in cleaned
    assert "Specific Tools" not in cleaned
    assert "**" not in cleaned
    assert "\n" not in cleaned
    assert not cleaned.startswith(("•", "*", "-"))
    assert word_count(cleaned) <= 35
    assert not has_bullet_structure_artifacts(cleaned)


def test_candidate_confirmation_preserves_raw_evidence_but_stores_concise_source_bullet(
    profile, analysis
):
    question = CandidateQuestion(
        id="Q-AUTO",
        requirement_id="R2",
        question="Describe your automation experience.",
        answer_type="long_text",
    )
    answer = CandidateAnswer(
        question_id="Q-AUTO",
        requirement_id="R2",
        answer_type="long_text",
        text=MARKDOWN_CONFIRMATION,
        experience_id="nasdaq",
        placement="new_bullet",
    )

    updated = build_profile_with_candidate_answers(
        profile, analysis, [question], [answer]
    )

    evidence = updated.supplemental_evidence[-1]
    source = updated.bullet_lookup()[evidence.source_bullet_id]
    assert evidence.statement == MARKDOWN_CONFIRMATION.strip()
    assert source.startswith("Championed the automation")
    assert word_count(source) <= 35
    assert not has_bullet_structure_artifacts(source)


def test_final_repair_and_word_export_never_emit_nested_markdown_bullets(
    project_root, profile, analysis, proposal
):
    broken = proposal.model_copy(deep=True)
    target = next(item for item in broken.bullet_proposals if item.include)
    target.proposed_text = MARKDOWN_CONFIRMATION

    repaired, remaining = apply_all_until_valid(profile, analysis, broken)
    repaired_target = next(
        item
        for item in repaired.bullet_proposals
        if item.source_bullet_id == target.source_bullet_id
    )

    assert not any("markdown" in issue.issue.casefold() for issue in remaining)
    assert "\n" not in repaired_target.proposed_text
    assert "**" not in repaired_target.proposed_text
    assert not repaired_target.proposed_text.startswith(("•", "*", "-"))

    approved = build_approved_resume(profile, analysis, repaired)
    result = export_resume_docx(
        project_root / "data" / "resume_template_professional.docx",
        profile,
        approved,
    )
    document = Document(BytesIO(result))
    exported = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style.name == STYLE_BULLET
    ]
    target_text = next(text for text in exported if "Championed the automation" in text)
    assert target_text.startswith("• Championed")
    assert target_text.count("•") == 1
    assert "**" not in target_text
    assert "\n" not in target_text
