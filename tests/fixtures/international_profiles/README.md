# International career profile fixtures

These six offline fixtures cover the international scenarios required for Career Bridge product validation:

1. Internationally trained accountant with a French credential and unfamiliar title.
2. Educator changing careers into customer success.
3. Brazilian software engineer with limited U.S. experience.
4. Kenyan procurement professional whose official title needs target-market explanation.
5. Philippine nurse missing a mandatory Oregon license.
6. Moroccan multilingual project coordinator with French and Arabic resume content.

Each fixture contains a validated `CandidateProfile`, international career context, structured job analysis, evidence-grounded proposal, and expected outcomes. The tests deliberately avoid OpenAI calls so CI can run deterministically.

Run:

```bash
python -m unittest -v tests.regression.test_international_career_profiles
```

The suite checks schema validity, Unicode preservation, safe title and credential translation, transferable skills, unsupported requirements, hard eligibility blockers, proposal integrity, and interview-preparation evidence grounding.
