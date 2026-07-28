from resume_tailor.proposal_changes import summarize_proposal_changes


def test_proposal_change_summary_identifies_visible_and_metadata_changes(profile, proposal):
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    after.professional_summary = before.professional_summary + " Added sentence."
    after.skills.hard_skills = list(before.skills.hard_skills) + ["Oracle"]
    after.bullet_proposals[0].proposed_text += " Improved wording."
    after.bullet_proposals[0].include = not before.bullet_proposals[0].include
    after.evidence_matches[0].status = "partial"

    result = summarize_proposal_changes(before, after, profile)

    assert result["has_changes"] is True
    assert result["summary_change"] is not None
    assert result["skill_changes"][0]["added"] == ["Oracle"]
    assert result["bullet_changes"][0]["wording_changed"] is True
    assert result["bullet_changes"][0]["include_changed"] is True
    assert result["evidence_changes"][0]["after_status"] == "partial"
    assert result["resume_visible_change_count"] >= 3


def test_change_summary_explains_restored_source_wording(profile, proposal):
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    source_id = "NAS-01"
    source_text = profile.bullet_lookup()[source_id]
    before_item = next(item for item in before.bullet_proposals if item.source_bullet_id == source_id)
    after_item = next(item for item in after.bullet_proposals if item.source_bullet_id == source_id)
    before_item.proposed_text = "Delivered 999 unsupported outcomes."
    after_item.proposed_text = source_text

    result = summarize_proposal_changes(
        before,
        after,
        profile,
        issue_summaries=[
            {
                "section": "Experience",
                "source_id": source_id,
                "issue": "Proposed bullet introduces new number(s): 999",
                "suggested_fix": "Use only numbers present in the source bullet.",
            }
        ],
    )

    change = next(item for item in result["bullet_changes"] if item["source_id"] == source_id)
    reasons = change["automatic_fix"]["reasons"]
    assert reasons[0]["label"] == "Restored source wording"
    assert "999" in reasons[0]["detail"]
    assert change["automatic_fix"]["after_word_count"] > change["automatic_fix"]["before_word_count"]
    assert result["bullet_reason_summary"][0]["label"] == "source wording restoration"


def test_change_summary_explains_bullet_selection_range(profile, proposal):
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    source_id = "NAS-06"
    after_item = next(item for item in after.bullet_proposals if item.source_bullet_id == source_id)
    before_item = next(item for item in before.bullet_proposals if item.source_bullet_id == source_id)
    before_item.include = False
    after_item.include = True
    experience_id = next(
        experience.id
        for experience in profile.experiences
        if any(bullet.id == source_id for bullet in experience.bullets)
    )

    result = summarize_proposal_changes(
        before,
        after,
        profile,
        issue_summaries=[
            {
                "section": "Experience",
                "source_id": experience_id,
                "issue": "Nasdaq has 5 selected bullets; required range is 6-7.",
                "suggested_fix": "Select the strongest relevant bullets within the required range.",
            }
        ],
    )

    change = next(item for item in result["bullet_changes"] if item["source_id"] == source_id)
    reasons = change["automatic_fix"]["reasons"]
    assert reasons[0]["label"] == "Included to meet the bullet range"
    assert "required range is 6-7" in reasons[0]["detail"]


def test_change_summary_counts_only_included_missing_bullets_as_resume_restorations(
    profile, proposal
):
    source_id = "NAS-06"
    before = proposal.model_copy(deep=True)
    before.bullet_proposals = [
        item for item in before.bullet_proposals
        if item.source_bullet_id != source_id
    ]

    included_after = proposal.model_copy(deep=True)
    included_item = next(
        item for item in included_after.bullet_proposals
        if item.source_bullet_id == source_id
    )
    included_item.include = True
    included_result = summarize_proposal_changes(before, included_after, profile)
    included_reason = included_result["bullet_reason_summary"][0]
    assert included_reason["category"] == "structure_restored"
    assert "included in the resume" in included_reason["label"]

    excluded_after = proposal.model_copy(deep=True)
    excluded_item = next(
        item for item in excluded_after.bullet_proposals
        if item.source_bullet_id == source_id
    )
    excluded_item.include = False
    excluded_result = summarize_proposal_changes(before, excluded_after, profile)
    excluded_reason = excluded_result["bullet_reason_summary"][0]
    assert excluded_reason["category"] == "structure_restored_excluded"
    assert "not included" in excluded_reason["label"]



def test_change_summary_groups_resulting_capgemini_changes(profile, proposal):
    before = proposal.model_copy(deep=True)
    before.bullet_proposals = [
        item
        for item in before.bullet_proposals
        if item.source_bullet_id not in {"CAP-01", "CAP-02"}
    ]
    before_cap_03 = next(
        item for item in before.bullet_proposals if item.source_bullet_id == "CAP-03"
    )
    before_cap_03.include = True

    after = proposal.model_copy(deep=True)
    after_cap_01 = next(
        item for item in after.bullet_proposals if item.source_bullet_id == "CAP-01"
    )
    after_cap_02 = next(
        item for item in after.bullet_proposals if item.source_bullet_id == "CAP-02"
    )
    after_cap_03 = next(
        item for item in after.bullet_proposals if item.source_bullet_id == "CAP-03"
    )
    after_cap_01.include = True
    after_cap_02.include = True
    after_cap_03.include = False

    result = summarize_proposal_changes(
        before,
        after,
        profile,
        issue_summaries=[
            {
                "section": "Experience",
                "source_id": "capgemini",
                "issue": "Capgemini does not contain the required complete bullet structure.",
                "suggested_fix": "Restore missing source bullets and keep the selected count in range.",
            }
        ],
    )

    capgemini = next(
        group
        for group in result["experience_change_groups"]
        if group["context"] == "QA Engineer — Capgemini"
    )
    changes = {item["source_id"]: item for item in capgemini["changes"]}
    assert set(changes) == {"CAP-01", "CAP-02", "CAP-03"}
    assert changes["CAP-01"]["reasons"][0]["label"] == "Restored and included missing bullet"
    assert changes["CAP-02"]["reasons"][0]["label"] == "Restored and included missing bullet"
    assert changes["CAP-03"]["reasons"][0]["label"] == "Excluded to meet the bullet range"


def test_change_summary_explains_summary_quality_change(profile, proposal):
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    before.professional_summary = "Too short."
    after.professional_summary = (
        "Experienced software engineer with documented delivery across regulated financial systems and complex reporting platforms. "
        "Skilled in SQL, Python, testing, release automation, production support, and careful validation of business requirements. "
        "Collaborates with clients and internal teams to investigate issues, explain technical decisions, and deliver reliable releases. "
        "Brings a practical, evidence-based approach to improving software quality and operational outcomes."
    )

    result = summarize_proposal_changes(
        before,
        after,
        profile,
        issue_summaries=[
            {
                "section": "Professional Summary",
                "source_id": "",
                "issue": "Summary has 2 words; required range is 50-80.",
                "suggested_fix": "Expand the summary without adding unsupported claims.",
            }
        ],
    )

    fix = result["summary_change"]["automatic_fix"]
    assert fix["reasons"][0]["label"] == "Expanded to meet the summary guideline"
    assert "required range is 50-80" in fix["reasons"][0]["detail"]
    assert fix["after_word_count"] > fix["before_word_count"]


def test_change_summary_explains_skill_quality_change(profile, proposal):
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    before.skills.hard_skills = list(after.skills.hard_skills) + ["Invented Skill"]

    result = summarize_proposal_changes(
        before,
        after,
        profile,
        issue_summaries=[
            {
                "section": "Skills",
                "source_id": "Invented Skill",
                "issue": "'Invented Skill' is not in the candidate's verified skills.",
                "suggested_fix": "Remove it.",
            }
        ],
    )

    change = next(item for item in result["skill_changes"] if item["category"] == "Hard Skills")
    reason = change["automatic_fix"]["reasons"][0]
    assert reason["label"] == "Aligned skills with verified experience"
    assert "Invented Skill" in reason["detail"]
