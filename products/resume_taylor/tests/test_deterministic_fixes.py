from __future__ import annotations

from io import BytesIO

from docx import Document

from resume_tailor.deterministic_fixes import apply_all_until_valid
from resume_tailor.docx_export import export_resume_docx
from resume_tailor.models import BulletProposal, EvidenceMatch, SkillSet
from resume_tailor.validation import build_approved_resume, validate_proposal


def test_apply_all_deterministic_fixes_clears_every_validation_item(profile, analysis, proposal):
    broken = proposal.model_copy(deep=True)
    broken.professional_summary = "Too short."

    verified = profile.all_verified_skills()
    broken.skills = SkillSet(
        hard_skills=verified[:17] + [verified[0], "Invented Skill"],
        soft_skills=[],
        tools_software=[],
        industry_knowledge=[],
    )

    # Create missing, duplicate, unknown, empty, overlong, new-number, and invalid-requirement issues.
    broken.bullet_proposals = broken.bullet_proposals[:-1]
    broken.bullet_proposals[0] = broken.bullet_proposals[0].model_copy(
        update={
            "include": True,
            "proposed_text": "Delivered 999 unsupported outcomes " + "word " * 60,
            "matched_requirement_ids": ["UNKNOWN-REQ"],
            "evidence_note": "",
        }
    )
    broken.bullet_proposals.append(broken.bullet_proposals[0].model_copy(deep=True))
    broken.bullet_proposals.append(
        BulletProposal(
            source_bullet_id="UNKNOWN-BULLET",
            include=True,
            proposed_text="Unknown source.",
            matched_requirement_ids=[],
            evidence_note="Unknown.",
        )
    )
    for item in broken.bullet_proposals:
        item.include = False

    broken.evidence_matches = [
        EvidenceMatch(
            requirement_id="R1",
            status="supported",
            evidence_ids=[],
            rationale="Unsupported full match.",
        ),
        EvidenceMatch(
            requirement_id="R1",
            status="partial",
            evidence_ids=["NAS-01"],
            rationale="Duplicate match.",
        ),
        EvidenceMatch(
            requirement_id="UNKNOWN-REQ",
            status="supported",
            evidence_ids=["NAS-01"],
            rationale="Unknown requirement.",
        ),
    ]

    assert validate_proposal(profile, analysis, broken)

    fixed, remaining = apply_all_until_valid(profile, analysis, broken)

    assert remaining == []
    assert validate_proposal(profile, analysis, fixed) == []
    assert len(fixed.bullet_proposals) == sum(
        len(experience.bullets) for experience in profile.experiences
    )
    assert fixed.skills.total_count() <= 30
    assert [match.requirement_id for match in fixed.evidence_matches] == [
        requirement.id for requirement in analysis.requirements
    ]



def test_missing_bullet_repairs_are_included_in_draft_and_word_export(
    project_root, profile, analysis, proposal
):
    missing_ids = {"NAS-06", "NAS-08", "AVI-01", "AVI-02", "CAP-01", "CAP-03"}
    broken = proposal.model_copy(deep=True)
    broken.bullet_proposals = [
        item for item in broken.bullet_proposals
        if item.source_bullet_id not in missing_ids
    ]

    fixed, remaining = apply_all_until_valid(profile, analysis, broken)

    assert remaining == []
    fixed_lookup = {item.source_bullet_id: item for item in fixed.bullet_proposals}
    assert all(fixed_lookup[source_id].include for source_id in missing_ids)

    approved = build_approved_resume(profile, analysis, fixed)
    exported = export_resume_docx(
        project_root / "data" / "resume_template_professional.docx", profile, approved
    )
    document = Document(BytesIO(exported))
    body_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    source_lookup = profile.bullet_lookup()
    for source_id in missing_ids:
        assert source_lookup[source_id].rstrip(".") in body_text


def test_missing_bullet_repairs_displace_lower_priority_bullets_to_keep_limits(
    profile, analysis, proposal
):
    broken = proposal.model_copy(deep=True)
    broken.bullet_proposals = [
        item for item in broken.bullet_proposals
        if item.source_bullet_id != "NAS-06"
    ]

    fixed, remaining = apply_all_until_valid(profile, analysis, broken)

    assert remaining == []
    fixed_lookup = {item.source_bullet_id: item for item in fixed.bullet_proposals}
    assert fixed_lookup["NAS-06"].include is True
    nasdaq_selected = [
        item for item in fixed.bullet_proposals
        if item.source_bullet_id.startswith("NAS-") and item.include
    ]
    assert 6 <= len(nasdaq_selected) <= 7

def test_step_four_applies_local_deterministic_repairs_before_ai_optimization(project_root):
    app_source = (project_root / "app.py").read_text(encoding="utf-8")
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    route = app_source.split("def start_final_stage():", 1)[1].split(
        '@app.post("/resume/save/<version>")', 1
    )[0]
    assert "working, _ = apply_all_until_valid" in route
    assert "Approve &amp; optimize" in template
    assert "Apply all suggested fixes" not in template


def test_bulk_fix_removes_adjacent_repeated_words_without_rewriting_other_text(
    profile, analysis, proposal
):
    broken = proposal.model_copy(deep=True)
    broken.professional_summary = broken.professional_summary.replace(
        "and client training", "and and client training"
    )
    bullet = next(
        item for item in broken.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    original_without_duplicate = bullet.proposed_text
    bullet.proposed_text = bullet.proposed_text.replace("the", "the the", 1)
    broken.skills.hard_skills[2] = "Software Software Testing"

    fixed, remaining = apply_all_until_valid(profile, analysis, broken)

    assert remaining == []
    assert "and and" not in fixed.professional_summary.casefold()
    fixed_bullet = next(
        item for item in fixed.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    assert fixed_bullet.proposed_text == original_without_duplicate
    assert "Software Testing" in fixed.skills.hard_skills
    assert "Software Software Testing" not in fixed.skills.hard_skills
