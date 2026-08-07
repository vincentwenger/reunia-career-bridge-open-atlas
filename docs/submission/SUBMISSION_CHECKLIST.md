# Submission Checklist

## Repository gate

- [ ] `python -m pytest -q` finishes with zero failures.
- [ ] `python tests/run_final_integration_checks.py --require-runtime` passes in CI.
- [ ] Both GitHub Actions workflows are green on the final commit.
- [ ] `python scripts/submission/check_submission_readiness.py --full` passes.
- [ ] No secrets, generated caches, local databases, or real candidate records are committed.
- [ ] The disclosure describes only artifacts and capabilities that reviewers can verify.
- [ ] README, Devpost copy, `PREEXISTING_COMPONENTS.md`, and `project-history/` consistently classify Resume Tailor as pre-hackathon and Réunia as submission-period work.
- [ ] Hosted Git tags/branches, if referenced publicly, point to genuine historical commits or snapshots; no history was backdated or manufactured.

## Deployment gate

- [ ] `https://career.reunia.app` resolves with a valid certificate.
- [ ] The homepage and sign-in page show current Career Bridge branding.
- [ ] `/health` reports `status=ok`, persistent storage, and a healthy external worker heartbeat.
- [ ] `scripts/deployment/validate_lightsail_deployment.bat` passes with the synthetic validation account.
- [ ] A prepared application survives logout and login.
- [ ] A retained validation application survives one container redeployment, then is deleted.
- [ ] Resume generation, Interview Preparation, and one mock-interview review complete successfully.

## Submission media

- [ ] Demo video is public or unlisted and no longer than three minutes.
- [ ] The video uses only synthetic data.
- [ ] At least three real browser screenshots are present in `docs/submission/screenshots/`.
- [ ] `docs/submission/pitch-deck.pdf` opens correctly.
- [ ] All text is legible at normal playback and viewing size.

## Devpost fields

- [ ] Project name.
- [ ] Two-to-three-sentence elevator pitch.
- [ ] Full description.
- [ ] Demo video URL.
- [ ] Public repository URL.
- [ ] Team members.
- [ ] Track selection.
- [ ] Pre-existing component and AI-assisted development disclosures.
