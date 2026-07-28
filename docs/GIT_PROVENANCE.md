# Git Provenance

The uploaded archives did not include `.git` directories. The workspace therefore uses the strongest reproducible fallback:

1. Import Réunia unchanged as an orphan root commit.
2. Annotate that commit with `reunia-original-import`.
3. Import Resume Taylor unchanged as a second orphan root commit.
4. Annotate that commit with `resume-tailor-original-import`.
5. Merge both roots with `--allow-unrelated-histories`.
6. Add shared Career Bridge work only after the merge.

Verification commands:

```bash
git show reunia-original-import --stat
git show resume-tailor-original-import --stat
git log --graph --decorate --oneline --all
sha256sum -c provenance/reunia-files.sha256
sha256sum -c provenance/resume-taylor-files.sha256
```

The file manifests use paths relative to the repository root.
