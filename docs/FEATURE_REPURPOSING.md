# Réunia Feature Repurposing

The imported Réunia runtime remains an adapter. Candidate-facing concepts now map to the `JobApplication` aggregate instead of a generic meeting.

| Imported capability | Career Bridge capability | MVP behavior |
|---|---|---|
| Meeting Preparation | Interview Preparation | Company, role, interviewer, likely questions, and verified evidence |
| Meeting Materials | Application Materials | Resume, job posting, company notes, and recruiter messages |
| AI Context | Career Profile | Background, accomplishments, preferences, and constraints |
| Knowledge Search | Career Evidence Library | Search verified projects, achievements, and experience |
| Meeting Package | Application Workspace | One workspace per target position |
| Browser Meeting Recorder | Mock Interview Recorder | Audio recording and transcription for practice |
| Windows Desktop Recorder | Removed from MVP | Legacy endpoint returns HTTP 410 and no UI is shown |
| Live Q&A | Removed from real interviews | Candidate UI is removed and legacy live endpoints return HTTP 410 |
| Meeting Review | Interview Review | Analyze mock-interview answers |
| Meeting Scorecard | Interview Scorecard | Relevance, evidence, structure, clarity, and delivery |
| Action Center | Career Action Plan | Resume work, practice needs, applications, and follow-ups |
| Analytics | Career Progress | Improvement across applications and mock interviews |
| Upcoming Meetings | Upcoming Interviews | Optional application/interview scheduling |
| Admin Analytics / Incidents | Unchanged | Administrator-only product operations |

## Compatibility boundary

Clean career URLs and `/api/career/*` aliases are now preferred. Existing `/knowledge.html`, `/meeting-recorder`, `/meeting-review.html`, `/action-center.html`, `/analytics.html`, `/api/knowledge/*`, `/api/transcripts`, and `/api/actions` routes remain available during migration so existing saved data and clients do not break.

Preferred adapter endpoints now include:

- `/api/career/profile`
- `/api/career/evidence` and `/api/career/evidence/search`
- `/api/career/application-workspaces`
- `/api/career/application-materials`
- `/api/career/mock-interviews/*`
- `/api/career/interview-reviews`
- `/api/career/actions`

The legacy meeting-shaped persistence fields remain behind the adapter boundary. Candidate-facing code reads the Career Bridge response aliases first and falls back to the imported field names only for compatibility.
