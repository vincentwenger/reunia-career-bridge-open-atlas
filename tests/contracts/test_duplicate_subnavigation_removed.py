from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "products" / "reunia" / "templates" / "knowledge.html"
STYLESHEET = ROOT / "products" / "reunia" / "static" / "css" / "pages" / "knowledge.css"


def test_shared_knowledge_pages_do_not_render_duplicate_subnavigation():
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'class="preparation-subnav"' not in template
    assert 'class="preparation-subnav-link' not in template
    assert 'aria-label="Career preparation sections"' not in template


def test_duplicate_subnavigation_styles_are_removed():
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert ".preparation-subnav" not in stylesheet
    assert ".preparation-subnav-link" not in stylesheet
    assert ".preparation-subnav-icon" not in stylesheet
