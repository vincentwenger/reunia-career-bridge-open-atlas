from __future__ import annotations


_OUTPUT_STRUCTURE = """
{
  "content_grades": [
    {
      "question": "Question that prompted the eligible answer",
      "answer": "Verbatim answer from eligible source lines only",
      "relevance_analysis": "Concise assessment of how well the answer addressed the question",
      "grade": "A"
    }
  ],
  "form_metrics": {
    "pace_wpm": 0,
    "pace_grade": "A",
    "filler_words_count": 0,
    "filler_words": [],
    "filler_words_grade": "A",
    "power_words_count": 0,
    "power_words": [],
    "power_words_grade": "A",
    "negative_words_count": 0,
    "negative_words": [],
    "negative_words_grade": "A",
    "negative_tone_count": 0,
    "negative_tone": [],
    "negative_tone_grade": "A",
    "pauses_count": 0,
    "pauses_grade": "A",
    "overall_assessment": ""
  }
}
""".strip()


_COMMON_REQUIREMENTS = """
Perform both parts of the scorecard in this single response.

CONTENT GRADING REQUIREMENTS:

- Identify every meaningful question that receives an answer from an eligible source line.
- Return the meaningful question that prompted each eligible answer.
- Copy each answer verbatim from eligible source lines, excluding only source labels.
  Do not summarize or paraphrase it.
- Before including an item, verify that its answer appears in at least one eligible source line.
- Evaluate how directly, clearly, completely, and effectively the answer addresses the question.
- Grade only demonstrated quality. The absence of visible weaknesses is not evidence of excellent performance.
- Do not award A-range grades merely because an answer or meeting is short. A-range grades require clear,
  affirmative evidence of relevance, depth, completeness, specificity, and effective reasoning in the answer itself.
- When evidence is thin, state that limitation in relevance_analysis and use a conservative observed-quality grade.
- Do not grade the question itself.
- If several consecutive eligible lines clearly form one answer, combine them in transcript order.
- A short acknowledgment from another participant may be skipped when the same eligible
  participant clearly continues the answer afterward.
- If a question has no clear eligible answer, omit it.
- Do not invent questions, answers, or context.
- Preserve transcript order.

FORM GRADING REQUIREMENTS:

- Analyze the eligible speech as one communication sample.
- Treat sample size separately from observed quality. Few detected problems in a short sample do not justify an A-range grade.
- A-range form grades require enough repeated evidence to demonstrate consistency, not merely zero detected issues.
- When the eligible sample is short, keep the assessment conservative and explicitly mention the evidence limitation.
- pace_wpm: Estimate the average speaking pace in words per minute.
- filler_words: Return every detected filler-word occurrence, including repeated occurrences.
  Examples include "um", "uh", "like", "you know", "actually", "basically", and "sort of".
- filler_words_count: Must equal the number of entries in filler_words.
- power_words: Return every detected confident, persuasive, positive, or action-oriented word or phrase.
- power_words_count: Must equal the number of entries in power_words.
- negative_words: Return every detected weak, uncertain, apologetic, pessimistic, or unnecessarily
  negative word or phrase.
- negative_words_count: Must equal the number of entries in negative_words.
- negative_tone: Return exact phrases or short transcript excerpts that demonstrate a negative,
  uncertain, defensive, dismissive, or unprofessional tone.
- negative_tone_count: Must equal the number of entries in negative_tone.
- pauses_count: Estimate noticeable or disruptive pauses only when they can reasonably be inferred
  from the transcript.
- Do not invent words, phrases, pauses, or tone examples.
- Preserve repeated occurrences so list lengths match their corresponding counts.
- Use empty lists and a count of 0 when no examples are found.
- overall_assessment must be concise, professional, actionable, and identify when the available sample is too short
  to support a confident communication assessment.

Use only these grades in both sections:
A+, A, A-, B+, B, B-, C+, C, C-, D+, D, D-, or F.

Return valid JSON only using exactly this top-level structure:

{output_structure}

Do not include Markdown, code fences, comments, or explanatory text outside the JSON.
""".strip()


def _build_prompt(source_rules: str) -> str:
    return f"""
You are an expert meeting scorecard analyst.

{source_rules.strip()}

{_COMMON_REQUIREMENTS.format(output_structure=_OUTPUT_STRUCTURE)}

Meeting transcript:
{{{{MEETING_TRANSCRIPT}}}}
""".strip()


SCORECARD_GRADING_MICROPHONE_PROMPT = _build_prompt(
    """
Use the complete labeled transcript for conversational context, but grade only the
microphone/user source.

Eligible answer and form-analysis labels:
[MICROPHONE], [MIC], [USER], or [ME].

Ineligible answer and form-analysis labels:
[SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

A speaker-source line may be used only to identify a question asked before a later
eligible microphone-source answer. Never include speaker-source content in the
answer field or form metrics. Ignore every unlabeled line. Do not infer microphone
speech from an unlabeled or speaker-source line.
"""
)


SCORECARD_GRADING_SPEAKER_PROMPT = _build_prompt(
    """
Use the complete labeled transcript for conversational context, but grade only the
speaker/participant source.

Eligible answer and form-analysis labels:
[SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

Ineligible answer and form-analysis labels:
[MICROPHONE], [MIC], [USER], or [ME].

A microphone-source line may be used only to identify a question asked before a later
eligible speaker-source answer. Never include microphone-source content in the
answer field or form metrics. Ignore every unlabeled line. Do not infer speaker
speech from an unlabeled or microphone-source line.
"""
)


SCORECARD_GRADING_ALL_PROMPT = _build_prompt(
    """
Grade ANY supported microphone or speaker audio-source line that is present:
[MICROPHONE], [MIC], [USER], [ME], [SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

This option represents all available audio sources and must work when the transcript
contains microphone lines only, speaker lines only, or both. Analyze ALL supported
microphone and speaker audio-source lines for the form metrics. Do not combine
unrelated answers from different participants into one content answer. Ignore every
unlabeled line and do not infer missing speech.
"""
)
