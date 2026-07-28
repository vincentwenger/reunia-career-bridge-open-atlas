# Réunia Career Bridge — Shared Technical Foundation

This workspace combines the histories of Réunia and Resume Taylor without prematurely combining their routes, databases, or dependency stacks.

`JobApplication` is now the shared aggregate root. Resume tailoring and mock interview practice are two stages of the same application lifecycle.

## Repository layout

```text
career_bridge/                 Shared job-application domain and adapter contracts
products/reunia/               Original Réunia import
products/resume_taylor/        Original Resume Taylor import
docs/DOMAIN_MODEL.md           Aggregate relationships and consistency rules
docs/ARCHITECTURE.md           Integration boundaries and first vertical slice
docs/MIGRATION_PLAN.md         Incremental adapter and workflow plan
provenance/                    SHA-256 import manifests
PREEXISTING_COMPONENTS.md      Hackathon provenance and reuse inventory
HACKATHON_CHANGES.md           New-work development log
```

## Central model

A `JobApplication` connects:

- candidate profile;
- career background;
- source resume;
- target job description;
- evidence library;
- tailored resume versions;
- interview preparation;
- mock interview sessions;
- improvement actions;
- application status and status history.

The hydrated `JobApplicationBundle` validates that every child belongs to the same candidate and application.


## Career Bridge navigation

The signed-in Réunia shell now presents the product as eight job-application workspaces: Career Profile, Application Builder, Interview Preparation, Mock Interview, Interview Review, Career Action Plan, Progress, and Help & Support. Administration remains separate and administrator-only. Existing Réunia pages are used as delivery adapters while the resume and interview capabilities are migrated behind the shared ports.

## Git provenance

```bash
git tag -n
git log --graph --decorate --oneline --all
```

The original snapshots remain tagged as `reunia-original-import` and `resume-tailor-original-import`. Their independent roots remain available as import branches.

## Run the existing applications

Use separate virtual environments because their OpenAI SDK constraints currently conflict.

### Réunia

```bash
cd products/reunia
python -m venv .venv
# Activate the environment, then:
pip install -r requirements.txt
python app.py
```

### Resume Taylor

```bash
cd products/resume_taylor
python -m venv .venv
# Activate the environment, then:
pip install -r requirements.txt
python app.py
```

## Validate the shared foundation

The shared package uses only the Python standard library.

```bash
python -m unittest discover -s tests/shared -v
python -m compileall -q career_bridge
```

## Integration rule

New Career Bridge features should depend on `career_bridge.ports`, not import Flask routes or persistence implementations directly. Meeting, recording, resume, and tracking records are adapted into a job application; none of those legacy records becomes the aggregate root.
