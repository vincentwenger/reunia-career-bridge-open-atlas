from __future__ import annotations


def test_confirmation_form_has_bulk_controls_for_yes_no_and_text_questions(project_root):
    template = (project_root / "templates" / "index.html").read_text(encoding="utf-8")

    assert "data-confirmation-form" in template
    assert 'data-confirmation-bulk="yes"' not in template
    assert 'data-confirmation-bulk="no"' in template
    assert "Answer Yes to all Yes/No questions" not in template
    assert "Mark all as no experience" in template
    assert "mark every question as no relevant experience" in template
    assert "data-text-question-choice" in template
    assert "data-mark-question-no-experience" in template
    assert "data-answer-question-instead" in template
    assert "Marked as no relevant experience" in template


def test_bulk_confirmation_javascript_marks_every_question_and_allows_reopening(project_root):
    javascript = (project_root / "static" / "app.js").read_text(encoding="utf-8")

    assert "[data-confirmation-form]" in javascript
    assert '[data-confirmation-bulk="no"]' in javascript
    assert "setTextQuestionNoExperience" in javascript
    assert "choiceField.value = markedNo ? 'no' : ''" in javascript
    assert "answerField.disabled = markedNo" in javascript
    assert "answerField.required = !markedNo" in javascript
    assert "[data-mark-question-no-experience]" in javascript
    assert "[data-answer-question-instead]" in javascript
    assert "noRadio.checked = true" in javascript
    assert "dispatchEvent(new Event('change'" in javascript
    assert "reopen any individual question" in javascript
