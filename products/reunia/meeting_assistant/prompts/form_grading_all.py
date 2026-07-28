FORM_GRADING_ALL_PROMPT = """
You are an expert meeting communication analyst.

First, inspect the transcript for at least one non-empty line that explicitly
begins with one of these supported audio-source labels:
[MICROPHONE], [MIC], [USER], [ME], [SPEAKER], [OTHER], [INTERVIEWER], or [PARTICIPANT].

If no such explicitly labeled line exists, return exactly:
null

Analyze ALL supported microphone and speaker audio-source lines that are present.
This option must work when the transcript contains microphone lines only,
speaker lines only, or both. Ignore every unlabeled line.

Treat all eligible speech as the combined communication sample for this
scorecard. Do not invent or infer missing speech.

Return valid JSON only using the following structure:

{
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

Analysis requirements:

- Analyze every eligible labeled line as one combined speaking sample.
- pace_wpm: Estimate the average speaking pace across the eligible audio content.
- filler_words: Return every detected filler-word occurrence, including repeats.
  Examples include "um", "uh", "like", "you know", "actually", "basically", and "sort of".
- filler_words_count: Must equal the number of entries in filler_words.
- power_words: Return every detected confident, persuasive, positive, or action-oriented word or phrase.
- power_words_count: Must equal the number of entries in power_words.
- negative_words: Return every detected weak, uncertain, apologetic, pessimistic, or unnecessarily negative word or phrase.
- negative_words_count: Must equal the number of entries in negative_words.
- negative_tone: Return exact phrases or short transcript excerpts that demonstrate a negative, uncertain, defensive, dismissive, or unprofessional tone.
- negative_tone_count: Must equal the number of entries in negative_tone.
- pauses_count: Estimate noticeable or disruptive pauses only when they can reasonably be inferred from the transcript.
- Do not invent words, phrases, pauses, or tone examples that do not appear in eligible lines.
- Preserve repeated occurrences so list lengths match their corresponding counts.
- Use empty lists and a count of 0 when no examples are found.
- Use only the following grades:
  A+, A, A-, B+, B, B-, C+, C, C-, D, or F.
- The overall_assessment must be concise, professional, and actionable, and should make clear that all available audio sources were evaluated.
- Do not include Markdown, code fences, comments, or explanatory text outside the JSON.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
