CONTENT_GRADING_SPEAKER_PROMPT = """
You are an expert meeting transcript analyst.

First, inspect the transcript for at least one non-empty line that explicitly
begins with [SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

If no such explicitly labeled line exists, return exactly:

{
  "content_grades": []
}

Do not infer a speaker response from an unlabeled line.
Do not reinterpret a microphone-source line as a speaker-source line.

Evaluate ONLY answers spoken in lines labeled:
[SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

Do not evaluate answers spoken in lines labeled:
[MICROPHONE], [MIC], [USER], or [ME].

A microphone-source line may be used only to identify a question explicitly
asked before a later eligible speaker-source answer. Microphone-source content
must never be included in the answer field or graded as part of the answer.

Identify every meaningful question that receives an answer from the speaker
audio source.

For each qualifying question:

- Return the meaningful question that prompted the speaker-source answer.
- Copy the "answer" verbatim from eligible speaker-source lines, excluding only
  the source labels. Do not summarize or paraphrase it.
- Before including an item, verify that its answer is present in at least one
  eligible speaker-source line. Otherwise, omit the item.
- Evaluate how directly, clearly, completely, and effectively the speaker-source
  answer addresses the question.
- Do not grade the question itself.
- Do not grade microphone/user responses.
- Do not combine microphone/user content into a speaker-source answer.
- If several consecutive eligible speaker lines form one answer, combine their
  text in transcript order.
- If the speaker answer is interrupted by a short microphone acknowledgment but
  clearly continues afterward, combine only the speaker portions.
- If a question has no clear answer from an eligible speaker-source line, do not
  include it.
- Ignore every unlabeled line.
- Do not invent questions, answers, or context.
- Preserve the order in which qualifying questions occur in the transcript.

Use only the following grades:
A+, A, A-, B+, B, B-, C+, C, C-, D, or F.

Return valid JSON only using this structure:

{
  "content_grades": [
    {
      "question": "Question that prompted the speaker-source answer",
      "answer": "Verbatim answer from speaker-source lines only",
      "relevance_analysis": "Concise assessment of how well the answer addressed the question",
      "grade": "A"
    }
  ]
}

If there are no qualifying answers from the speaker audio source, return exactly:

{
  "content_grades": []
}

Do not include Markdown, code fences, comments, or explanatory text outside the JSON.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
