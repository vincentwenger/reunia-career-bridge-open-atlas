from resume_tailor.text_diff import build_word_diff


def test_word_diff_marks_removed_and_added_text() -> None:
    original_html, proposed_html = build_word_diff(
        "Built five regulatory reports.",
        "Built seven automated regulatory reports.",
    )

    assert 'class="diff-removed"' in original_html
    assert "five" in original_html
    assert 'class="diff-added"' in proposed_html
    assert "seven automated" in proposed_html


def test_word_diff_leaves_identical_text_unstyled() -> None:
    original_html, proposed_html = build_word_diff("No changes.", "No changes.")

    assert original_html == "No changes."
    assert proposed_html == "No changes."
    assert "diff-added" not in proposed_html
    assert "diff-removed" not in original_html


def test_word_diff_escapes_html() -> None:
    original_html, proposed_html = build_word_diff(
        "Used <script>alert(1)</script>",
        "Used safe output",
    )

    assert "<script>" not in original_html
    assert "&lt;" in original_html
    assert "<script>" not in proposed_html
