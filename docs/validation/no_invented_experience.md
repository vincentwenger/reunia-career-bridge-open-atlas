# No Invented Experience Verification

## Result

The product now has enforceable post-generation grounding across the resume, Career Translation, Interview Preparation, Mock Interview, scorecard, Career Action Plan, report, and export paths.

The implementation does not rely on prompts alone. Generated candidate claims are checked against permitted evidence and are rejected, downgraded, sanitized, restored to source text, or blocked from export when unsupported.

## Primary controls

- Shared validator: `products/resume_taylor/resume_tailor/grounding.py`
- Resume claim validation: `products/resume_taylor/resume_tailor/validation.py`
- Generation-time repair: `products/resume_taylor/resume_tailor/deterministic_fixes.py`
- Central AI boundary enforcement: `products/resume_taylor/resume_tailor/ai.py`
- Career Translation evidence verification: `products/resume_taylor/resume_tailor/career_translation.py`
- Interview Preparation post-validation: `products/resume_taylor/resume_tailor/interview_preparation.py`
- Mock Interview scorecard sanitization: `products/reunia/meeting_assistant/services/mock_interview_service.py`
- Career Action Plan sanitization: `products/reunia/meeting_assistant/services/action_service.py`
- Resume report grounding check: `products/resume_taylor/resume_tailor/resume_report.py`
- Export-time blocking: `products/resume_taylor/app.py`

## Regression coverage

- `tests/regression/test_no_invented_experience.py`
- `tests/regression/test_international_career_profiles.py`
- `tests/fixtures/international_profiles/`
- `tests/validators/validate_no_invented_experience.py`

The suite covers internationally trained professionals, career changers, limited U.S. experience, poorly translated titles, unsupported target requirements, and multilingual resume content.

## Validation command

```bash
python tests/validators/validate_no_invented_experience.py
```

## Honest assurance boundary

The controls provide high-signal deterministic protection and broad regression coverage. Natural-language semantic equivalence cannot be proven perfectly by lexical rules or by another language model. Therefore the system uses conservative behavior: questionable positive claims are removed or downgraded, and final exports are blocked rather than silently accepting unsupported wording.

Historical binary files created before these controls should be regenerated from a workflow that still contains its Verified Resume Evidence, job analysis, proposal, and evidence metadata.
