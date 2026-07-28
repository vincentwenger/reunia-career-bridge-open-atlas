CONTENT_GRADING_ALL_PROMPT = """
You are an expert meeting transcript analyst.

First, inspect the transcript for at least one non-empty line that explicitly
begins with one of these supported audio-source labels:
[MICROPHONE], [MIC], [USER], [ME], [SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

If no such explicitly labeled line exists, return exactly:

{
  "content_grades": []
}

Evaluate answers spoken in ANY supported microphone or speaker audio-source line.
This option represents all available audio sources, so it must work when the
transcript contains microphone lines only, speaker lines only, or both.

Do not infer speech from an unlabeled line. Ignore every unlabeled line.

Identify every meaningful question that receives an answer from any supported
audio source.

For each qualifying question:

- Return the meaningful question that prompted the answer.
- Copy the "answer" verbatim from eligible labeled lines, excluding only the
  source labels. Do not summarize or paraphrase it.
- Before including an item, verify that its answer appears in at least one
  eligible labeled line. Otherwise, omit the item.
- Evaluate how directly, clearly, completely, and effectively the answer
  addresses the question.
- Do not grade the question itself.
- Do not combine unrelated answers from different participants into one answer.
- Consecutive lines from the same answering participant may be combined in
  transcript order when they clearly form one answer.
- A short acknowledgment from another participant may be skipped when the same
  participant clearly continues the answer afterward.
- If a question has no clear answer from an eligible labeled line, do not include it.
- Do not invent questions, answers, or context.
- Preserve the order in which qualifying questions occur in the transcript.

Use only the following grades:
A+, A, A-, B+, B, B-, C+, C, C-, D, or F.

Return valid JSON only using this structure:

{
  "content_grades": [
    {
      "question": "Question that prompted the answer",
      "answer": "Verbatim answer from eligible labeled lines",
      "relevance_analysis": "Concise assessment of how well the answer addressed the question",
      "grade": "A"
    }
  ]
}

If there are no qualifying answers, return exactly:

{
  "content_grades": []
}

Do not include Markdown, code fences, comments, or explanatory text outside the JSON.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
