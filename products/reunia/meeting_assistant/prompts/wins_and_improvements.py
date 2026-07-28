WINS_AND_IMPROVEMENTS_PROMPT = """
Identify concise, actionable key wins and improvement areas from the meeting.
Return valid JSON only:
{
  "key_wins": ["Key win"],
  "improvement_areas": ["Improvement area"]
}
Use an empty list when none are found.

Meeting transcript:
{{MEETING_TRANSCRIPT}}
"""
