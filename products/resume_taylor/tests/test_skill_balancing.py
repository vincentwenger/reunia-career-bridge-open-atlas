from __future__ import annotations

from resume_tailor.models import SkillSet
from resume_tailor.skill_rules import (
    SKILL_CATEGORY_RULES,
    SKILL_TOTAL_MAXIMUM,
    balance_skill_categories,
)


def test_balance_reclassifies_and_fills_recommended_categories(profile, analysis):
    unbalanced = SkillSet(
        hard_skills=[
            "ETL Workflows",
            "Data Transformation",
            "Software Testing",
            "SQL",
            "Python",
            "Axiom regulatory reporting platform",
            "Regulatory Reporting",
            "Financial Services",
        ],
        soft_skills=["Stakeholder Communication", "Cross-functional Collaboration"],
        tools_software=[],
        industry_knowledge=[],
    )

    balanced = balance_skill_categories(profile, analysis, unbalanced)

    assert "SQL" not in balanced.hard_skills
    assert "SQL" in balanced.tools_software
    assert "Regulatory Reporting" not in balanced.hard_skills
    assert "Regulatory Reporting" in balanced.industry_knowledge
    assert len(balanced.hard_skills) >= SKILL_CATEGORY_RULES["hard_skills"]["minimum"]
    assert len(balanced.soft_skills) >= SKILL_CATEGORY_RULES["soft_skills"]["minimum"]
    assert len(balanced.tools_software) >= SKILL_CATEGORY_RULES["tools_software"]["minimum"]
    assert len(balanced.industry_knowledge) >= SKILL_CATEGORY_RULES["industry_knowledge"]["minimum"]
    assert balanced.total_count() <= SKILL_TOTAL_MAXIMUM


def test_balance_never_invents_skills_or_exceeds_available_category(profile, analysis):
    limited_profile = profile.model_copy(deep=True)
    limited_profile.skills.soft_skills = ["Stakeholder Communication"]
    limited_profile.skills.industry_knowledge = ["Regulatory Reporting", "Financial Services"]

    balanced = balance_skill_categories(limited_profile, analysis, SkillSet())

    assert balanced.soft_skills == ["Stakeholder Communication"]
    assert balanced.industry_knowledge == ["Regulatory Reporting", "Financial Services"]
    assert set(balanced.hard_skills).issubset(set(limited_profile.skills.hard_skills))
    assert set(balanced.tools_software).issubset(set(limited_profile.skills.tools_software))
