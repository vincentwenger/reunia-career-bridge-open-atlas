from __future__ import annotations

import unittest

from products.resume_taylor.resume_tailor.models import BulletProposal, TailoringProposal


class TailoringProposalOpenAISchemaTests(unittest.TestCase):
    def test_deterministic_comparison_metadata_is_not_sent_to_openai(self) -> None:
        bullet_schema = BulletProposal.model_json_schema()
        properties = bullet_schema.get("properties", {})
        required = set(bullet_schema.get("required", []))

        self.assertNotIn("selected_instead_ids", properties)
        self.assertNotIn("selection_comparison_reasons", properties)
        self.assertNotIn("selected_instead_ids", required)
        self.assertNotIn("selection_comparison_reasons", required)

        proposal_schema = TailoringProposal.model_json_schema()
        nested_bullet_schema = proposal_schema["$defs"]["BulletProposal"]
        nested_properties = nested_bullet_schema.get("properties", {})
        nested_required = set(nested_bullet_schema.get("required", []))

        self.assertNotIn("selected_instead_ids", nested_properties)
        self.assertNotIn("selection_comparison_reasons", nested_properties)
        self.assertNotIn("selected_instead_ids", nested_required)
        self.assertNotIn("selection_comparison_reasons", nested_required)

    def test_deterministic_comparison_metadata_remains_persistable(self) -> None:
        bullet = BulletProposal(
            source_bullet_id="EXP-001-B01",
            include=False,
            proposed_text="Implemented a regulatory reporting solution.",
            evidence_note="Lower priority within available resume space.",
            selected_instead_ids=["EXP-001-B05"],
            selection_comparison_reasons={
                "EXP-001-B05": ["More unique requirement coverage."]
            },
        )

        payload = bullet.model_dump()
        self.assertEqual(payload["selected_instead_ids"], ["EXP-001-B05"])
        self.assertEqual(
            payload["selection_comparison_reasons"],
            {"EXP-001-B05": ["More unique requirement coverage."]},
        )
        restored = BulletProposal.model_validate(payload)
        self.assertEqual(restored.selected_instead_ids, ["EXP-001-B05"])


if __name__ == "__main__":
    unittest.main()
