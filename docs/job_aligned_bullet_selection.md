# Job-Aligned Resume Bullet Selection

Career Bridge separates evidence analysis from resume selection.

## AI responsibility

For every Application Baseline bullet, the model returns:

- grounded proposed wording;
- directly supported job requirement IDs;
- an evidence-basis note.

The model does not decide whether the bullet is included. The compatibility `include` field is set to `false` and ignored by the selector.

## Deterministic selection

Each bullet receives three explainable scores:

- **Job relevance (0–3):** secondary, important, or critical requirement match.
- **Evidence strength (0–2):** specificity, measurable scope or outcomes, and candidate-confirmed evidence.
- **Unique coverage (0–2):** whether the bullet adds job-requirement coverage not already represented.

Selection uses two passes per experience:

1. Cover critical and important requirements with the strongest available evidence.
2. Fill the normal bullet allowance with the highest-scoring non-duplicative accomplishments.

A bullet with one or more matched requirements always ranks ahead of an unmatched transferable bullet while matched evidence remains available. Duplicate checks decide between matched bullets; they do not allow unmatched evidence to displace a matched accomplishment.

## Resume space

- Most recent experience: normally 6 bullets, up to 7 when additional important requirement coverage is needed.
- Second experience: normally 3 bullets, up to 4.
- Third and older experiences: normally 2 bullets, up to 3.

## User-facing outcomes

- `Included — strong job match`
- `Included — strong transferable evidence`
- `Not included — lower priority`
- `Not included — similar evidence already selected`

The Application Baseline and Verified Resume Evidence are never changed by selection. Candidates may manually override any result.

## Exclusion transparency

When a bullet is not selected, the selector records up to two included bullets that most directly competed for the same role-level resume space. The Finalize Resume view shows:

- the selected bullet ID and full wording;
- shared or additional job requirements;
- whether the selected bullet had stronger evidence or more unique coverage;
- both bullets' relevance, evidence-strength, unique-coverage, and total scores.

If the numeric scores are tied, the explanation states that the deterministic source-order tie-break decided the result rather than claiming that one accomplishment was stronger.
