# Réunia Career Bridge — Technical Foundation

This workspace combines the histories of two imported applications without prematurely combining their routes, databases, or dependency stacks.

## Repository layout

```text
career_bridge/                 New shared domain and adapter contracts
products/reunia/               Original Réunia import
products/resume_taylor/        Original Resume Taylor import
docs/                          Architecture and migration guidance
provenance/                    SHA-256 import manifests
PREEXISTING_COMPONENTS.md      Hackathon provenance and reuse inventory
HACKATHON_CHANGES.md           New-work development log
```

## Git provenance

```bash
git tag -n
git log --graph --decorate --oneline --all
```

The original snapshots are tagged as `reunia-original-import` and `resume-tailor-original-import`. Their independent roots remain available as import branches.

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

New Career Bridge features should depend on `career_bridge.ports`, not import Flask routes or persistence implementations directly. Adapters can wrap existing services one capability at a time.
