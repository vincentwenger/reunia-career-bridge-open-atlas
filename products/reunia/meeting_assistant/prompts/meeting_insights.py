MEETING_INSIGHTS_PROMPT = """
Analyze the meeting transcript and return valid JSON only in this exact shape:
{
  "meeting_name": "Concise meeting title",
  "summary": "Meeting summary",
  "topics": ["Topic 1", "Topic 2"],
  "action_items": ["Action item"],
  "open_questions": ["Open question"]
}
Return 1 to 3 concise, reusable topics. Prefer established project, client, function, or recurring subject names over generic labels such as “Meeting” or “Discussion”.
Do not use markdown and do not add text outside the JSON.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
