from __future__ import annotations

from resume_tailor.proposal_changes import summarize_tailoring_changes
from resume_tailor.resume_report import build_initial_resume_proposal, initial_resume_title


def test_tailoring_summary_explains_requirement_focused_rewrite_and_exclusion(
    profile, analysis, proposal
):
    before = build_initial_resume_proposal(profile)
    after = proposal.model_copy(deep=True)

    rewritten = next(
        item for item in after.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    rewritten.proposed_text = (
        "Led the implementation of SEC Form PF reporting in Axiom for an on-time filing."
    )

    result = summarize_tailoring_changes(
        before,
        after,
        profile,
        analysis,
        reference_title=initial_resume_title(profile),
        current_title=analysis.target_title,
    )

    rewrite_reason = result["bullet_details"]["NAS-01"]["reasons"][0]
    excluded_reason = result["bullet_details"]["NAS-06"]["reasons"][0]

    assert rewrite_reason["label"] == "Reworded to emphasize job-relevant evidence"
    assert "R1: Develop Axiom regulatory reporting solutions" in rewrite_reason["detail"]
    assert excluded_reason["label"] == "Not selected for the tailored Draft"
    assert "No more specific exclusion rationale was returned" in excluded_reason["detail"]
    assert result["title_changed"] is True
    assert result["requirement_rewrite_count"] == 1
    assert result["excluded_bullet_count"] > 0


def test_resume_editor_uses_discreet_tailoring_explanations(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")
    script = (project_root / "static" / "app.js").read_text(encoding="utf-8")
    app_source = (project_root / "app.py").read_text(encoding="utf-8")

    assert "Job Alignment changes" in template
    assert '<details class="change-explanation" data-change-explanation>' in template
    assert "Why this changed" in template
    assert "Observed" in template
    assert "Likely" in template
    assert "The Initial and Job-Aligned Resume Reports are generated automatically" in template
    assert "View supporting details" in template
    assert ".change-explanation" in styles
    assert "opening one closes its nearby peers" in script
    assert "tailoring_report_impacts(" in app_source
    assert "summarize_tailoring_changes(" in app_source
    assert 'bullet_tailoring_details=(' in app_source


def test_tailoring_summary_repairs_missing_proposal_item_before_explaining(profile, analysis, proposal):
    before = build_initial_resume_proposal(profile)
    after = proposal.model_copy(deep=True)
    missing_id = after.bullet_proposals[0].source_bullet_id
    after.bullet_proposals = [
        item for item in after.bullet_proposals if item.source_bullet_id != missing_id
    ]

    result = summarize_tailoring_changes(
        before,
        after,
        profile,
        analysis,
        reference_title=initial_resume_title(profile),
        current_title=analysis.target_title,
    )

    assert missing_id not in result["bullet_details"]
    assert result["excluded_bullet_count"] == sum(
        1 for item in proposal.bullet_proposals if not item.include
    )


def _impact_test_report(*, hard_skill_score: float, evidence_score: float, title_score: float = 0.0):
    from resume_tailor.resume_report import ReportCheck, ReportSection, ReportSubsection, ResumeReport

    def section(name: str, subsection: str, label: str, score: float) -> ReportSection:
        return ReportSection(
            name,
            "",
            [
                ReportSubsection(
                    subsection,
                    [ReportCheck(label, "warning", "", score_value=score)],
                )
            ],
        )

    return ResumeReport(
        searchability=section(
            "Searchability",
            "Job Title Match",
            "The job title matches the resume profile title",
            title_score,
        ),
        hard_skills=section("Hard skills", "Skill comparison", "XML scripting", hard_skill_score),
        soft_skills=section("Soft skills", "Skill comparison", "No soft skill", 100.0),
        content_quality=section("Content Quality", "Semantic Match", "Semantic match", 100.0),
        recruiter_tips=section("Recruiter tips", "Job Level Match", "Job level", 100.0),
        formatting=section("Formatting", "Layout", "Layout", 100.0),
        evidence_gaps=section(
            "Evidence & Gaps",
            "Requirement evidence coverage",
            "XML scripting",
            evidence_score,
        ),
    )


def test_report_impacts_ignore_unchanged_and_negative_metrics():
    from resume_tailor.report_impacts import tailoring_report_impacts
    from resume_tailor.models import JobAnalysis, JobRequirement

    analysis = JobAnalysis(
        target_title="Developer",
        requirements=[
            JobRequirement(
                id="RX",
                category="technical_skill",
                priority="important",
                requirement="XML scripting",
                keywords=["XML scripting"],
            )
        ],
    )
    initial = _impact_test_report(hard_skill_score=0.0, evidence_score=10.0)
    updated = _impact_test_report(hard_skill_score=0.0, evidence_score=0.0)

    impacts = tailoring_report_impacts(initial, updated, analysis)

    assert impacts["requirements"] == {}
    assert impacts["skills"]["Hard Skills"] is None


def test_report_impact_names_the_report_section_for_duplicate_metric_labels():
    from resume_tailor.report_impacts import tailoring_report_impacts
    from resume_tailor.models import JobAnalysis, JobRequirement

    analysis = JobAnalysis(
        target_title="Developer",
        requirements=[
            JobRequirement(
                id="RX",
                category="technical_skill",
                priority="important",
                requirement="XML scripting",
                keywords=["XML scripting"],
            )
        ],
    )
    initial = _impact_test_report(hard_skill_score=0.0, evidence_score=0.0)
    updated = _impact_test_report(hard_skill_score=0.0, evidence_score=100.0)

    impact = tailoring_report_impacts(initial, updated, analysis)["requirements"]["RX"]

    assert impact["label"] == "Evidence & Gaps — XML scripting"
    assert impact["before"] == 0.0
    assert impact["after"] == 100.0


def test_bullet_impact_requires_one_unique_direct_contributor(proposal):
    from resume_tailor.report_impacts import attributable_bullet_report_impacts
    from resume_tailor.models import JobAnalysis, JobRequirement

    analysis = JobAnalysis(
        target_title="Developer",
        requirements=[
            JobRequirement(
                id="RX",
                category="technical_skill",
                priority="important",
                requirement="XML scripting",
                keywords=["XML scripting"],
            )
        ],
    )
    before = proposal.model_copy(deep=True)
    after = proposal.model_copy(deep=True)
    before_item = next(item for item in before.bullet_proposals if item.source_bullet_id == "NAS-02")
    after_item = next(item for item in after.bullet_proposals if item.source_bullet_id == "NAS-02")
    before_item.matched_requirement_ids = []
    after_item.matched_requirement_ids = ["RX"]
    after_item.proposed_text += " Used XML scripting for the implementation."
    impact = {
        "label": "Evidence & Gaps — XML scripting",
        "before": 0.0,
        "after": 100.0,
        "delta": 100.0,
        "certainty": "likely",
    }

    assigned = attributable_bullet_report_impacts(
        before, after, analysis, {"RX": impact}, {"NAS-02"}
    )

    assert assigned["NAS-02"]["attribution"] == "unique_direct_contribution"

    second_before = next(item for item in before.bullet_proposals if item.source_bullet_id == "NAS-03")
    second_after = next(item for item in after.bullet_proposals if item.source_bullet_id == "NAS-03")
    second_before.matched_requirement_ids = []
    second_after.matched_requirement_ids = ["RX"]
    second_after.proposed_text += " Used XML scripting for delivery."

    ambiguous = attributable_bullet_report_impacts(
        before, after, analysis, {"RX": impact}, {"NAS-02", "NAS-03"}
    )

    assert ambiguous == {}


def test_change_explanation_suppresses_unattributed_report_claims(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    impact_source = (project_root / "resume_tailor" / "report_impacts.py").read_text(encoding="utf-8")

    assert "No measurable report improvement could be directly attributed" not in template
    assert "after_score - before_score <= 0.05" in impact_source
    assert "attributable_bullet_report_impacts(" in impact_source
    assert "unique_direct_contribution" in impact_source


def test_user_confirmed_rewrite_is_not_counted_as_exclusion(profile, analysis, proposal):
    before = build_initial_resume_proposal(profile)
    after = proposal.model_copy(deep=True)
    for item in after.bullet_proposals:
        item.include = True
    source_item = after.bullet_proposals[0]
    replacement_item = after.bullet_proposals[1]
    source_item.include = False
    source_item.evidence_note = (
        "User marked this source bullet as rewritten as "
        f"{replacement_item.source_bullet_id}. The replacement remains included in the "
        "tailored resume."
    )

    result = summarize_tailoring_changes(
        before,
        after,
        profile,
        analysis,
        reference_title=initial_resume_title(profile),
        current_title=analysis.target_title,
    )

    reason = result["bullet_details"][source_item.source_bullet_id]["reasons"][0]
    assert reason["code"] == "tailoring_rewritten"
    assert reason["category"] == "rewritten"
    assert reason["label"] == f"Rewritten as {replacement_item.source_bullet_id}"
    assert result["excluded_bullet_count"] == 0


def test_tailoring_changes_distinguish_job_alignment_from_verified_experience(
    profile, analysis, proposal
):
    from resume_tailor.models import SupplementalEvidence

    confirmed_profile = profile.model_copy(deep=True)
    confirmed_profile.supplemental_evidence.append(
        SupplementalEvidence(
            id="CONF-Q1",
            statement="Candidate confirmed additional Axiom implementation experience.",
            requirement_ids=["R1"],
            verified_skills=["Axiom"],
        )
    )
    before = build_initial_resume_proposal(confirmed_profile)
    after = proposal.model_copy(deep=True)
    rewritten = next(
        item for item in after.bullet_proposals if item.source_bullet_id == "NAS-01"
    )
    rewritten.proposed_text = (
        "Led an Axiom regulatory-reporting implementation using confirmed experience."
    )
    rewritten.evidence_note = "Supported by source bullet NAS-01 and CONF-Q1."

    result = summarize_tailoring_changes(
        before,
        after,
        confirmed_profile,
        analysis,
        reference_title=initial_resume_title(confirmed_profile),
        current_title=analysis.target_title,
    )

    reason = result["bullet_details"]["NAS-01"]["reasons"][0]
    assert result["primary_change_category"] == "Job Alignment"
    assert result["verified_experience_available"] is True
    assert result["verified_experience_change_count"] >= 1
    assert reason["change_category"] == "Verified Experience"


def test_tailoring_change_categories_are_visible_in_the_review_ui(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (project_root / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'aria-label="Job Alignment changes"' in template
    assert '>Job Alignment</span>' in template
    assert 'Verified Experience</span>' in template
    assert 'aria-label="Change category"' in template
    assert '.change-category-badge' in styles
    assert '.change-category-badge.verified' in styles


def test_final_comparison_nests_evidence_inside_change_explanation(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    change_macro = template.split("{% macro change_explanation", 1)[1].split(
        "{% endmacro %}", 1
    )[0]
    automatic_macro = template.split(
        "{% macro automatic_change_explanation", 1
    )[1].split("{% endmacro %}", 1)[0]
    bullet_region = template.split(
        "{% for bullet in experience.bullets %}", 1
    )[1].split("{% endfor %}", 1)[0]

    assert "evidence_note=None, matched_requirements=None" in template
    assert '<details class="change-supporting-details">' in change_macro
    assert "<strong>Evidence note:</strong> {{ evidence_note }}" in change_macro
    assert "<strong>Matched requirements:</strong>" in change_macro
    assert "<strong>Evidence note:</strong> {{ evidence_note }}" in automatic_macro
    assert "bullet.evidence_note if version == 'final' else none" in bullet_region
    assert "bullet.matched_requirements if version == 'final' else none" in bullet_region
    assert "version != 'final' or not has_change_explanation" in template
