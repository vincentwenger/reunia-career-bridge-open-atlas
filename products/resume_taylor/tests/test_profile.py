from __future__ import annotations


def test_profile_has_unique_source_ids(profile):
    ids = list(profile.bullet_lookup())
    assert len(ids) == len(set(ids))
    assert len(ids) == 17


def test_profile_contains_verified_axiom_and_sql(profile):
    skills = profile.skills.all_non_language_skills()
    assert "Axiom regulatory reporting platform" in skills
    assert "SQL" in skills
