from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products" / "resume_taylor" / "templates" / "application_builder" / "index.html"
STYLES = ROOT / "products" / "resume_taylor" / "static" / "styles.css"


class ReviewTailoredResumeStatusColorContractTests(unittest.TestCase):
    def test_retained_count_uses_included_green_treatment(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn('class="retained-count{% if experience.retained_count > 0 %} has-changes{% endif %}"', template)
        self.assertIn('.experience-change-summary .retained-count.has-changes,', styles)
        self.assertIn('background: rgba(34, 197, 94, 0.10);', styles)
        self.assertIn('color: var(--cb-p-166534);', styles)


    def test_restored_count_and_badge_use_distinct_teal_treatment(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn(
            '.experience-change-summary .restored-count.has-changes {\n'
            '  border-color: rgba(13, 148, 136, 0.38);\n'
            '  background: var(--cb-p-ccfbf1);\n'
            '  color: var(--cb-color-teal);',
            styles,
        )
        self.assertIn(
            '.bullet-status-badge.restored_missing_included { background: var(--cb-p-ccfbf1); color: var(--cb-color-teal); }',
            styles,
        )
        self.assertIn(
            '.bullet-status-restored_missing_included { border-color: rgba(13, 148, 136, 0.38); background: var(--cb-p-f0fdfa); }',
            styles,
        )
        self.assertNotIn(
            '.bullet-status-badge.restored_missing_included { background: #dcfce7; color: var(--cb-p-166534); }',
            styles,
        )

    def test_lower_priority_status_uses_excluded_red_treatment(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        self.assertIn(
            '.bullet-status-badge.auto_reconciled_excluded { background: var(--cb-p-fee2e2); color: var(--cb-p-991b1b); }',
            styles,
        )


if __name__ == "__main__":
    unittest.main()
