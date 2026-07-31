from __future__ import annotations

import json

from .models import (
    AuditIssue,
    CandidateAnswer,
    CandidateProfile,
    JobAnalysis,
    NewcomerCareerProfile,
    TailoringProposal,
)
from .skill_rules import (
    SKILL_CATEGORY_RULES,
    SKILL_TOTAL_MAXIMUM,
    SKILL_TOTAL_RECOMMENDED_MINIMUM,
)
from .validation import deterministic_audit_facts


JOB_ANALYSIS_SYSTEM = """You are a job-description analyst for an evidence-based resume tailoring tool.
Extract only requirements that should influence a resume. Ignore compensation, benefits, equal-opportunity language,
telecommuting boilerplate, company slogans, generic values, and duplicated prose. Do not infer candidate experience.
Use concise requirement wording. Copy each keyword verbatim from the job description, preserving spelling,
hyphenation, abbreviations, and capitalization so exact ATS frequency comparisons are reliable. For soft-skill,
leadership, and behavioral requirements, use concise keyword phrases such as "communication skills", "analytical",
"executive leadership", or "coach" rather than copying a full responsibility sentence. Assign unique IDs R1, R2, R3,
and so on. Rank requirements by how central they are to the role, not only by how often they appear."""


PROPOSAL_SYSTEM = """You are an expert resume writer operating under a strict evidence-only policy.
You may reorganize, shorten, and clarify verified candidate information, but you must never invent or upgrade facts.
Never convert a job requirement into candidate experience unless the candidate profile contains explicit evidence.
Preserve employers, titles, dates, technologies, numbers, scope, and outcomes. A rewritten bullet may omit a fact,
but it may not add a new number, technology, responsibility, leadership scope, regulatory framework, or outcome.
When evidence is partial or missing, mark the requirement partial or unsupported and add a candidate question instead
of inserting it into the resume. Prefer natural recruiter-readable language over keyword density. Every visible experience
bullet must be one plain-text, action-led statement of 35 words or fewer. Never put headings, labels, markdown, nested
lists, multiple paragraphs, or a leading bullet symbol inside proposed_text.

Before drafting resume wording, complete the Career Translation Assessment. Its purpose is to make international
experience understandable without overstating it. Treat newcomer onboarding as context for identifying translations
and questions, not as resume evidence by itself. Only the Candidate Profile and later candidate-confirmed evidence may
support resume claims."""


AUDIT_SYSTEM = """You are an independent factual auditor for a resume-tailoring application.
Compare every claim in the proposal against the candidate profile. Treat any unsupported technology, responsibility,
number, scope, outcome, leadership claim, certification, domain claim, employer detail, or job-title change as blocking.
Also flag awkward wording, keyword stuffing, repetition, excessive length, or weak relevance as warnings. Treat the
newcomer career background as context only: flag any resume claim that relies on onboarding data without Candidate
Profile or CONF- evidence. A proposal passes only when it contains no blocking issues. Be conservative and specific.
For every issue, provide a concrete,
actionable suggested_fix that can be applied to the TailoringProposal. Do not repeat a finding whose exact correction
is already present in the proposal. Verify evidence statuses, rationales, evidence IDs, unsupported requirements,
evidence notes, summary wording, skills, and bullets together so the fix is internally consistent. For a Rephrase or
clarify finding, never provide only advisory language such as "focus on relevant experience." Quote the exact current
phrase and the exact replacement phrase. When the complete professional summary must be replaced, provide the entire
replacement summary in quotation marks. The quoted replacement paragraph may be returned by itself; an additional
"Replace the professional summary with" instruction is not required."""




AUDIT_FIX_SYSTEM = """You correct a resume-tailoring proposal by applying explicit findings from an independent factual audit.
Return a complete TailoringProposal. Apply only the supplied audit findings and suggested fixes; preserve unrelated
resume wording, selections, IDs, requirement matches, skills, and evidence decisions. Never invent evidence or make a
claim stronger. A suggested fix may update proposed resume text, a bullet evidence note, an evidence-match status,
evidence IDs, rationale, unsupported-requirement wording, or candidate-question wording. When an audit says provenance
is unsupported, replace claims such as 'candidate confirmed' everywhere they occur with neutral evidence language such
as 'not documented in the profile' unless a cited CONF- supplemental-evidence ID actually supports the confirmation.
When a status is downgraded or upgraded, synchronize its rationale, evidence IDs, and unsupported_requirements entry.
When a grouped finding references several requirement IDs or source IDs, correct every referenced item. Keep
candidate_questions empty after the confirmation stage. Preserve every source_bullet_id and requirement_id. Do not
remove an audit finding by hiding the requirement or deleting required proposal content. Preserve the Career
Translation Assessment unless an applied fix changes its evidence status; never upgrade a finding from onboarding
context alone. Before returning, verify that each supplied finding is no longer true and that the complete proposal
remains internally consistent. Use the smallest
conservative change that fully addresses each supplied finding. Every visible experience bullet must remain one
plain-text, action-led statement of 35 words or fewer. Never return headings such as "Overview" or "Specific Tools",
markdown emphasis, nested lists, multiple paragraphs, or a leading bullet symbol inside proposed_text. When a finding
asks for more detail, integrate only the most relevant supported detail into the existing single bullet; do not expand
one bullet into an outline or several labeled sections."""


def build_job_analysis_prompt(job_description: str, stated_title: str = "") -> str:
    return f"""Analyze the job description below.

User-provided target title (may be blank): {stated_title.strip() or '[not provided]'}

Return:
- target title and company when identifiable
- 8 to 24 resume-relevant requirements
- category: technical_skill, domain_knowledge, methodology, responsibility, leadership, or qualification
- priority: critical, important, or secondary
- concise keywords for each requirement
- ignored boilerplate summarized as short phrases

JOB DESCRIPTION
{job_description.strip()}
"""


def build_proposal_prompt(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    career_background: NewcomerCareerProfile | None = None,
) -> str:
    profile_json = json.dumps(profile.model_dump(exclude={"contact"}), ensure_ascii=False, indent=2)
    analysis_json = json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)
    background_json = json.dumps(
        (career_background or NewcomerCareerProfile()).model_dump(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""Create a conservative tailoring proposal for the candidate and analyzed job.

Hard constraints:
1. Professional summary: 50 to 80 words, 3 or 4 sentences.
2. Skills: aim for {SKILL_TOTAL_RECOMMENDED_MINIMUM}-{SKILL_TOTAL_MAXIMUM} total across the four categories, using only skills that are relevant to the target role and copied verbatim from the candidate profile. Keep each selected skill in its original Candidate Profile category. Aim for Hard Skills {SKILL_CATEGORY_RULES['hard_skills']['minimum']}-{SKILL_CATEGORY_RULES['hard_skills']['maximum']}, Soft Skills {SKILL_CATEGORY_RULES['soft_skills']['minimum']}-{SKILL_CATEGORY_RULES['soft_skills']['maximum']}, Tools & Software {SKILL_CATEGORY_RULES['tools_software']['minimum']}-{SKILL_CATEGORY_RULES['tools_software']['maximum']}, and Industry Knowledge {SKILL_CATEGORY_RULES['industry_knowledge']['minimum']}-{SKILL_CATEGORY_RULES['industry_knowledge']['maximum']}. If the profile does not contain enough relevant verified skills for a range, use the available relevant skills rather than inventing or forcing unrelated ones.
3. Return exactly one bullet proposal for every source bullet, using its exact source_bullet_id. Never omit the
   structured record. If you cannot justify an exclusion, include the original source wording by default.
4. For the most recent experience, include 6 or 7 bullets. For the second experience, include 3 or 4. For the third,
   include 2 or 3. Keep other experiences at 2 or 3 if any exist.
   Each included bullet must contain 35 words or fewer, use one sentence or concise clause, begin with an action verb,
   and contain plain text only. Do not include a bullet glyph, markdown, bold markers, headings, labels, line breaks,
   sub-bullets, or explanatory sections inside proposed_text.
5. Each proposed bullet must be supported by its source bullet. It may use a verified skill from elsewhere in the
   profile only when the evidence note explicitly identifies that supporting profile evidence.
6. Do not add job-posting terminology such as hardware upgrades, employee coaching, user-defined functions, report
   types, platforms, or regulatory processes unless the profile explicitly supports them.
7. Produce an evidence match for every job requirement.
8. Unsupported or partial requirements must remain outside the resume and appear in unsupported_requirements and/or
   candidate_questions.
9. Do not change the candidate's employer names, titles, dates, credentials, or numerical achievements.
10. Candidate questions must be structured, tied to a requirement_id, and limited to information that could materially
    improve the proposal. Ask at most six questions, only for critical or important requirements that are partial or
    unsupported. Do not ask about secondary requirements, minor keywords, preferences, or facts already supported by
    the profile. Prefer one concise yes_no_with_details question over several overlapping prompts. A yes/no question
    must not be used for a new detailed claim unless answer_type is yes_no_with_details and details_prompt explains
    what evidence is needed.
11. Complete career_translation_assessment before drafting the resume. Include only meaningful findings from these
    categories: job_title_translation, credential_explanation, regional_terminology, hidden_accomplishment,
    transferable_skill, unsupported_requirement, and missing_evidence. Classify every finding as exactly one of:
    confirmed_experience, reasonable_rephrasing, user_clarification_required, unsupported_claim, or
    recommended_learning_or_future_action.
    - confirmed_experience requires exact Candidate Profile evidence IDs.
    - reasonable_rephrasing may explain an existing title, credential, term, or accomplishment without changing facts.
    - user_clarification_required means the context may be useful but cannot be safely claimed yet. Create a candidate
      question when the clarification could materially strengthen this application.
    - unsupported_claim must remain outside the resume and align with unsupported_requirements.
    - recommended_learning_or_future_action is advice for a genuine gap and must never appear as current experience.
    Do not treat reusable-profile headlines, roles, year counts, skills, accomplishments, countries, languages,
    credentials, certifications, unfamiliar titles, transitions, work authorization, or target-country experience as
    claim evidence unless the uploaded or candidate-confirmed Candidate Profile independently supports them.

REUSABLE CAREER PROFILE AND INTERNATIONAL BACKGROUND — CONTEXT ONLY, NOT CLAIM EVIDENCE
{background_json}

CANDIDATE PROFILE
{profile_json}

JOB ANALYSIS
{analysis_json}
"""


def build_audit_prompt(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    career_background: NewcomerCareerProfile | None = None,
) -> str:
    deterministic_facts = deterministic_audit_facts(profile, analysis, proposal)
    background_json = json.dumps(
        (career_background or NewcomerCareerProfile()).model_dump(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""Audit the proposed resume content against the source evidence.

Your responsibility is semantic evidence quality and writing quality:
- Flag invented or strengthened facts.
- Flag unsupported technologies, reports, responsibilities, leadership scope, outcomes, certifications, domain claims,
  employer details, title changes, or summary claims.
- Check that evidence statuses, evidence IDs, rationales, unsupported requirements, evidence notes, visible summary
  wording, skills, and bullets are semantically consistent with one another.
- Flag wording that is awkward, repetitive, keyword-stuffed, misleading, or materially weak for the target role.
- A bullet_proposals finding must use the exact source_bullet_id of the visible resume bullet it targets. Never use a
  job requirement ID such as R7 as the source_id of a bullet_proposals finding.
- A job requirement that lacks candidate evidence is not itself a resume defect. Keep it unsupported in the evidence
  matrix. Do not recommend removing or reducing that requirement unless the visible professional summary, selected
  skills, or an included resume bullet actually makes the unsupported claim.
- Before recommending removal, confirm that the named claim or wording is present in the visible Final resume content.
- For every wording-only finding, make suggested_fix mechanically actionable: quote the exact replacement wording.
  For a phrase edit, the issue must quote the exact current phrase and suggested_fix must quote the exact replacement.
  For a selected bullet edit, use its exact source_bullet_id and quote both the exact current wording and exact
  replacement wording. When only a starting phrase changes and the rest of the bullet stays identical, matching
  trailing ellipses may mark the unchanged suffix in both quoted values.
  For a complete professional-summary rewrite, suggested_fix must contain the complete replacement summary in quotes.
  The quoted paragraph may be the entire suggested_fix; no "Replace X with Y" wrapper is required.
  Do not use vague instructions such as "focus on direct experience", "improve relevance", or "make it more concise"
  without supplying the exact final wording.

Do NOT report objective constraints that the application has already checked deterministically. In particular, do not
recount or challenge summary words or sentences, the total or duplicate count of skills, exact selected-skill membership
in the verified profile, source-bullet record completeness or uniqueness, exact requirement IDs, selected bullet counts
by role, exact bullet word counts, numeric-token presence, or evidence-matrix record completeness. The exact current
results are provided below and are the source of truth. Do not combine one of these objective rules with a semantic
finding. Report only the semantic finding.

DETERMINISTIC RESULTS — SOURCE OF TRUTH
{json.dumps(deterministic_facts, ensure_ascii=False, indent=2)}

REUSABLE CAREER PROFILE AND INTERNATIONAL BACKGROUND — CONTEXT ONLY, NOT CLAIM EVIDENCE
{background_json}

CANDIDATE PROFILE
{json.dumps(profile.model_dump(exclude={"contact"}), ensure_ascii=False, indent=2)}

JOB ANALYSIS
{json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)}

PROPOSAL
{json.dumps(proposal.model_dump(), ensure_ascii=False, indent=2)}
"""


def build_refinement_prompt(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    provisional: TailoringProposal,
    answers: list[CandidateAnswer],
    career_background: NewcomerCareerProfile | None = None,
) -> str:
    background_json = json.dumps(
        (career_background or NewcomerCareerProfile()).model_dump(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""Revise the provisional proposal using the candidate-confirmed evidence and answers below.

Rules:
- Return a complete TailoringProposal, not a patch.
- Treat No answers as acknowledged gaps. Never use them as evidence.
- Treat affirmative answers as evidence only to the specificity actually provided. A bare Yes can confirm the exact
  requirement but cannot support invented scope, outcomes, tools, dates, numbers, or responsibilities.
- Supplemental evidence in the candidate profile has IDs beginning with CONF-. Cite those IDs in evidence_matches and
  evidence notes when used.
- Every affirmative answer is attached to a selected experience and has a candidate-confirmed source bullet such as
  NAS-CONF-01. Return exactly one bullet proposal for every source bullet, including these candidate-confirmed bullets.
- Honor each answer's placement preference:
  * new_bullet: include the candidate-confirmed source bullet as a distinct bullet.
  * update_existing: strengthen the closest existing bullet in the same experience, cite both the CONF evidence ID and
    candidate-confirmed source bullet ID in that existing bullet's evidence_note, then exclude the candidate-confirmed
    source bullet with an evidence_note naming the bullet that represents it.
  * auto: choose the stronger recruiter-readable option between those two approaches.
- Every affirmative answer must produce visible resume content. It may not remain only in evidence metadata.
- Preserve exactly one bullet proposal for every source bullet ID and all evidence-only constraints from the first
  proposal. Never omit a bullet record; if an exclusion is not clearly justified, include the original wording.
- Recalculate evidence_matches and unsupported_requirements after applying the answers.
- Candidate questions with IDs beginning FQ are targeted follow-ups created after reviewing the transformed resume.
  For an FQ No answer, remove or reduce the unsupported claim described by that question. For an FQ Yes answer, use
  only the newly supplied factual detail and keep it attached to the selected experience.
- Return candidate_questions empty. The application performs the post-transformation evidence review and creates any
  additional targeted follow-up round itself.
- Do not add a confirmed skill to the resume unless the confirmed evidence is specific enough to support it.
- Recalculate career_translation_assessment after applying the answers. An affirmative answer may upgrade a related
  finding to confirmed_experience only when the updated profile contains a traceable CONF- evidence ID. A negative
  answer must remain an unsupported_claim or recommended_learning_or_future_action, never a resume claim.

REUSABLE CAREER PROFILE AND INTERNATIONAL BACKGROUND — CONTEXT ONLY, NOT CLAIM EVIDENCE
{background_json}

CANDIDATE PROFILE WITH CONFIRMED EVIDENCE
{json.dumps(profile.model_dump(exclude={"contact"}), ensure_ascii=False, indent=2)}

JOB ANALYSIS
{json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)}

PROVISIONAL PROPOSAL
{json.dumps(provisional.model_dump(), ensure_ascii=False, indent=2)}

CANDIDATE ANSWERS
{json.dumps([answer.model_dump() for answer in answers], ensure_ascii=False, indent=2)}
"""


def build_audit_fix_prompt(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    issues: list[AuditIssue],
    career_background: NewcomerCareerProfile | None = None,
) -> str:
    actionable = [issue for issue in issues if issue.suggested_fix.strip()]
    background_json = json.dumps(
        (career_background or NewcomerCareerProfile()).model_dump(),
        ensure_ascii=False,
        indent=2,
    )
    return f"""Apply the listed audit findings and suggested fixes to the proposal.

Rules:
- Return the complete corrected TailoringProposal, not a patch or explanation.
- Apply every supplied suggested fix exactly in substance, using conservative evidence-based wording.
- Do not change unrelated professional-summary text, skills, bullet wording, inclusion choices, requirement matches,
  evidence notes, evidence statuses, or rationales.
- Keep every visible experience bullet as one plain-text, action-led statement of 35 words or fewer. Never place a
  bullet symbol, heading, label, markdown, line break, nested list, or multiple explanatory sections in proposed_text.
- If the supplied finding asks for richer detail, select the most relevant supported detail and integrate it concisely
  into the existing bullet instead of creating an outline.
- If a finding concerns only evidence metadata, do not rewrite visible resume content.
- If a finding references one or more requirement IDs, update those exact evidence_matches.
- If a finding references a source bullet ID, update that exact bullet proposal or evidence note.
- Preserve all source_bullet_id and requirement_id values and keep one evidence match per analyzed requirement.
- Never create new candidate confirmations or cite a CONF- ID that is not present in the profile.
- Keep candidate_questions empty.
- Before returning, check every supplied finding against the corrected proposal and ensure the finding is no longer true.
- Apply grouped fixes to every referenced requirement ID and source ID, not only the first one.
- Keep evidence status, evidence IDs, rationale, unsupported_requirements, bullet evidence notes, and visible wording synchronized.
- Preserve career_translation_assessment unless a supplied fix directly affects a finding. When it does, synchronize the
  disposition and evidence IDs, but never treat newcomer onboarding as evidence.

REUSABLE CAREER PROFILE AND INTERNATIONAL BACKGROUND — CONTEXT ONLY, NOT CLAIM EVIDENCE
{background_json}

CANDIDATE PROFILE
{json.dumps(profile.model_dump(exclude={"contact"}), ensure_ascii=False, indent=2)}

JOB ANALYSIS
{json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2)}

CURRENT PROPOSAL
{json.dumps(proposal.model_dump(), ensure_ascii=False, indent=2)}

AUDIT FINDINGS TO APPLY
{json.dumps([issue.model_dump() for issue in actionable], ensure_ascii=False, indent=2)}
"""


