CONTENT_GRADING_MICROPHONE_PROMPT = """
You are an expert meeting transcript analyst.

First, inspect the transcript for at least one non-empty line that explicitly
begins with [MICROPHONE], [MIC], [USER], or [ME].

If no such explicitly labeled line exists, return exactly:

{
  "content_grades": []
}

Do not infer a microphone response from an unlabeled line.
Do not reinterpret a speaker-source line as a microphone-source line.

Evaluate ONLY answers spoken in lines labeled:
[MICROPHONE], [MIC], [USER], or [ME].

Do not evaluate answers spoken in lines labeled:
[SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

A speaker-source line may be used only to identify a question explicitly asked
before a later eligible microphone-source answer. Speaker-source content must
never be included in the answer field or graded as part of the answer.

Identify every meaningful question that receives an answer from the microphone
audio source.

For each qualifying question:

- Return the meaningful question that prompted the microphone-source answer.
- Copy the "answer" verbatim from eligible microphone-source lines, excluding
  only the source labels. Do not summarize or paraphrase it.
- Before including an item, verify that its answer is present in at least one
  eligible microphone-source line. Otherwise, omit the item.
- Evaluate how directly, clearly, completely, and effectively the microphone-source
  answer addresses the question.
- Do not grade the question itself.
- Do not grade speaker/participant responses.
- Do not combine speaker/participant content into a microphone-source answer.
- If several consecutive eligible microphone lines form one answer, combine
  their text in transcript order.
- If the microphone answer is interrupted by a short speaker acknowledgment but
  clearly continues afterward, combine only the microphone portions.
- If a question has no clear answer from an eligible microphone-source line, do
  not include it.
- Ignore every unlabeled line.
- Do not invent questions, answers, or context.
- Preserve the order in which qualifying questions occur in the transcript.

Use only the following grades:
A+, A, A-, B+, B, B-, C+, C, C-, D, or F.

Return valid JSON only using this structure:

{
  "content_grades": [
    {
      "question": "Question that prompted the microphone-source answer",
      "answer": "Verbatim answer from microphone-source lines only",
      "relevance_analysis": "Concise assessment of how well the answer addressed the question",
      "grade": "A"
    }
  ]
}

If there are no qualifying answers from the microphone audio source, return exactly:

{
  "content_grades": []
}

Do not include Markdown, code fences, comments, or explanatory text outside the JSON.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
