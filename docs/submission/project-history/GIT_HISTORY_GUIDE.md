# Git History Review Guide

## Why the source ZIP is not enough

A GitHub-generated source ZIP contains the working-tree files, but not the repository's `.git` database. As a result, this archive cannot create, verify, or display hosted Git branches and tags.

The public GitHub repository should remain the authoritative place for commit-level review.

## Recommended review points

Preserve clearly named import/integration review points and do not rewrite or backdate history to alter development chronology. The published repository may contain privacy-only sanitization of personal candidate data, with commit messages, authorship, dates, and ordering preserved. Earlier integration work used names such as:

- `reunia-original-import`
- `resume-tailor-original-import`

Do **not** recreate these names on arbitrary commits merely to make the history look complete. A tag should point only to the commit or imported snapshot it actually describes.

Useful additional review tags may be created in the full Git clone **only when the corresponding commit can be identified from the real history**, for example:

- `career-bridge-integration-start` — the commit/snapshot representing the July 28, 2026 integration start;
- `open-atlas-submission` — the final submitted commit.

## Branches and predecessor snapshots

If the full repository already contains preserved predecessor branches or snapshots, keep them. If it does not, do not manufacture reconstructed branches from the current code.

The documentation in this folder is intentionally sufficient to explain provenance even when predecessor Git histories were not imported commit-for-commit.

## What reviewers should inspect on GitHub

1. **Commits** — verify the development sequence from the integration point forward.
2. **Tags/Releases** — inspect only tags that map to genuine historical commits/snapshots.
3. **Branches** — inspect predecessor/archive branches only if they actually exist.
4. **README disclosure** — compare the Git history with the stated Resume Tailor / Réunia / Career Bridge timeline.
5. **`docs/submission/PREEXISTING_COMPONENTS.md`** — review the conservative capability inventory.

The goal is transparency, not a cosmetically perfect history.
