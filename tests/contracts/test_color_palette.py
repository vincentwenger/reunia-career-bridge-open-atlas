from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "products" / "reunia" / "static" / "css" / "career-theme.css"
CSS_ROOTS = (
    ROOT / "products" / "reunia" / "static" / "css",
    ROOT / "products" / "resume_taylor" / "static",
)


class TrustActionPaletteContractTests(unittest.TestCase):
    def test_shared_palette_tokens_are_present(self):
        content = THEME.read_text(encoding="utf-8").lower()
        expected = {
            "--career-primary: #1d4e78",
            "--career-teal: #0f766e",
            "--career-emerald: #137a58",
            "--career-cta: #c2410c",
            "--career-canvas: #f8fafc",
        }
        for token in expected:
            with self.subTest(token=token):
                self.assertIn(token, content)

    def test_legacy_all_emerald_brand_colors_are_removed(self):
        legacy = {"#176b5a", "#1b826b", "#0f473c", "#35a487"}
        found = []
        for root in CSS_ROOTS:
            for path in root.rglob("*.css"):
                colors = set(re.findall(r"#[0-9a-fA-F]{6}", path.read_text(encoding="utf-8")))
                remaining = {color.lower() for color in colors} & legacy
                if remaining:
                    found.append(f"{path.relative_to(ROOT)}: {sorted(remaining)}")
        self.assertEqual([], found, "Legacy brand colors remain:\n" + "\n".join(found))


    def test_core_text_contrast_meets_wcag_aa(self):
        def relative_luminance(hex_color):
            channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def contrast(first, second):
            high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        for color in ("#1d4e78", "#0f766e", "#c2410c"):
            with self.subTest(color=color):
                self.assertGreaterEqual(contrast(color, "#ffffff"), 4.5)

    def test_primary_cta_is_distinct_from_navigation_anchor(self):
        content = THEME.read_text(encoding="utf-8").lower()
        primary = re.search(r"--career-primary:\s*(#[0-9a-f]{6})", content).group(1)
        cta = re.search(r"--career-cta:\s*(#[0-9a-f]{6})", content).group(1)
        self.assertNotEqual(primary, cta)


if __name__ == "__main__":
    unittest.main()
