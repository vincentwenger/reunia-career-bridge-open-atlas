from __future__ import annotations

from typing import Any

from career_bridge.module_composition import activate_module, module_exports

"""Top-level Resume Report assembly."""

def build_resume_report(
    profile: CandidateProfile,
    analysis: JobAnalysis,
    proposal: TailoringProposal,
    *,
    generated_filename: str,
    template_path: str | Path | None = None,
    job_description: str = "",
    resume_title: str | None = None,
    candidate_answers: list[CandidateAnswer] | None = None,
    page_limit: int = 2,
    generated_document_bytes: bytes | None = None,
    exact_page_count: bool = False,
    career_stage: str | None = None,
    resume_format: str | None = None,
    visual_design: str | None = None,
    resume_language: str | None = None,
) -> ResumeReport:
    effective_resume_title = (resume_title or analysis.target_title).strip()
    selected_bullets = _selected_bullets(proposal)
    selected_ids_by_experience = _selected_bullet_ids_by_experience(profile, proposal)
    page_limit = max(1, int(page_limit))
    report_document, formatting_note = _document_for_report(
        template_path,
        profile,
        proposal,
        effective_resume_title,
        generated_document_bytes,
        career_stage=career_stage,
        resume_format=resume_format,
        visual_design=visual_design,
        resume_language=resume_language,
    )

    contact_checks = [
        ReportCheck(
            "You provided your full name",
            "pass" if len(_words(profile.name)) >= 2 else "warning" if profile.name.strip() else "fail",
            "A complete candidate name is available for the resume header."
            if len(_words(profile.name)) >= 2
            else "Provide the candidate's full professional name for the resume header.",
        ),
        ReportCheck(
            "You provided your physical address or location",
            "pass" if profile.contact.location.strip() else "fail",
            "Recruiters use your address or location to validate your location for job matches."
            if profile.contact.location.strip()
            else "Add a city and state, or another appropriate location, so recruiters can validate location-based matches.",
        ),
        ReportCheck(
            "You provided your email",
            "pass" if _EMAIL_PATTERN.match(profile.contact.email.strip()) else "fail",
            "Recruiters can use the email shown on the resume to contact you."
            if _EMAIL_PATTERN.match(profile.contact.email.strip())
            else "Add a valid professional email address.",
        ),
        ReportCheck(
            "You provided your phone number",
            "pass" if len(re.sub(r"\D", "", profile.contact.phone)) >= 10 else "fail",
            "A recruiter-ready phone number is present."
            if len(re.sub(r"\D", "", profile.contact.phone)) >= 10
            else "Add a complete phone number, including area code.",
        ),
    ]

    summary_checks = [
        ReportCheck(
            "We found a summary section on your resume",
            "pass" if proposal.professional_summary.strip() else "fail",
            "The summary provides a quick overview of the candidate's qualifications and value."
            if proposal.professional_summary.strip()
            else "Add a concise professional summary tailored to the target role.",
        )
    ]

    work_history_complete = bool(
        profile.experiences
        and selected_bullets
        and all(experience.employer and experience.title and experience.dates for experience in profile.experiences)
    )
    education_complete = bool(
        profile.education
        and all(item.credential.strip() and item.institution.strip() and item.date.strip() for item in profile.education)
    )
    heading_checks = [
        ReportCheck(
            'We found an "Education" section in your resume',
            "pass" if education_complete else "fail",
            'The resume includes complete education entries under an ATS-recognizable Education heading.'
            if education_complete
            else 'Add an Education section and ensure every entry includes a credential, institution, and date.',
        ),
        ReportCheck(
            "We found the work experience section in your resume",
            "pass" if profile.experiences else "fail",
            "A work experience section is present."
            if profile.experiences
            else "Add a Work Experience or Professional Experience section.",
        ),
        ReportCheck(
            "We found work history in your resume",
            "pass" if work_history_complete else "fail",
            "Employer names, job titles, dates, and selected accomplishments are present."
            if work_history_complete
            else "Include employer names, titles, dates, and at least one accomplishment bullet.",
        ),
    ]

    target_title = analysis.target_title.strip()
    normalized_target_title = _normalize(target_title)
    normalized_resume_title = _normalize(effective_resume_title)
    if target_title and effective_resume_title and normalized_target_title == normalized_resume_title:
        title_status: ReportStatus = "pass"
        title_detail = (
            f'The resume profile title "{effective_resume_title}" exactly matches the analyzed job title.'
        )
    elif target_title and effective_resume_title and (
        normalized_target_title in normalized_resume_title
        or normalized_resume_title in normalized_target_title
    ):
        title_status = "warning"
        title_detail = (
            f'The resume profile title "{effective_resume_title}" is related to the target title '
            f'"{target_title}", but it is not an exact match. Use the exact target title only when it accurately describes the candidate.'
        )
    else:
        title_status = "fail"
        title_detail = (
            f'The resume profile title "{effective_resume_title or "not provided"}" does not match the target title '
            f'"{target_title or "not identified"}". Recruiter searches commonly use exact job titles.'
        )
    title_checks = [
        ReportCheck(
            "The job title matches the resume profile title",
            title_status,
            title_detail,
        )
    ]

    invalid_dates = [
        experience.dates
        for experience in profile.experiences
        if not _DATE_RANGE_PATTERN.match(experience.dates.strip())
    ]
    date_checks = [
        ReportCheck(
            "Work-experience dates are properly formatted",
            "pass" if not invalid_dates else "fail",
            "All work dates use a consistent MM/YYYY - MM/YYYY format."
            if not invalid_dates
            else "Reformat these date ranges consistently: " + "; ".join(invalid_dates),
        )
    ]

    filename_without_extension = Path(generated_filename).stem
    filename_has_specials = bool(re.search(r"[^A-Za-z0-9 _.-]", generated_filename))
    readable_filename = 8 <= len(filename_without_extension) <= 80 and not re.fullmatch(r"[A-Za-z0-9]{20,}", filename_without_extension)
    file_checks = [
        ReportCheck(
            "You are using a .docx resume",
            "warning" if generated_filename.casefold().endswith(".docx") else "fail",
            "The application generates a .docx resume. Most ATS can process .docx files, but a PDF copy can preserve appearance more consistently; use the format requested by the employer.",
        ),
        ReportCheck(
            "The file name does not contain problematic special characters",
            "pass" if not filename_has_specials else "fail",
            "The proposed file name uses ATS-safe characters."
            if not filename_has_specials
            else "Remove special characters that could cause an upload or ATS parsing error.",
        ),
        ReportCheck(
            "The file name is concise and readable",
            "pass" if readable_filename else "warning",
            f'The proposed file name is "{generated_filename}".'
            if readable_filename
            else "Use a clear name such as Firstname_Lastname_TargetRole_Resume.docx.",
        ),
    ]

    searchability = ReportSection(
        "Searchability",
        "An ATS (Applicant Tracking System) is a software used by 90% of companies and recruiters to search for resumes and manage the hiring process. Below is how well your resume appears in an ATS and a recruiter search. Tip: Fix the red Xs to ensure your resume is easily searchable by recruiters and parsed correctly by the ATS.",
        [
            ReportSubsection("Contact Information", contact_checks),
            ReportSubsection("Summary", summary_checks),
            ReportSubsection("Section Headings", heading_checks),
            ReportSubsection("Job Title Match", title_checks),
            ReportSubsection("Date Formatting", date_checks),
            ReportSubsection("Education Match", [_education_match(profile, analysis)]),
            ReportSubsection("File Type", file_checks),
        ],
    )

    hard_skills = ReportSection(
        "Hard skills",
        "Hard skills enable you to perform job-specific duties and responsibilities. You can learn hard skills in the classroom, training courses, and on the job. These skills are typically focused on teachable tasks and measurable abilities, such as the use of tools, equipment, or software. Hard skills have a high impact on your match score. Tip: Match the skills in your resume to the exact spelling in the job description. Prioritize skills that appear most frequently in the job description, while adding only skills supported by verified experience.",
        [_hard_skill_comparison_subsection(profile, analysis, proposal, job_description, effective_resume_title)],
    )

    soft_skills = ReportSection(
        "Soft skills",
        "Soft skills are your traits and abilities that are not unique to any one job. They are part of your professional behavior and can also be learned. These skills typically help you succeed at any company, such as time management and communication. Soft skills have a medium impact on your match score. Tip: Prioritize hard skills in your resume to get interviews, and then showcase your soft skills in the interview to get jobs. The comparison below uses the exact spelling found in the job description and counts each occurrence in the current proposed resume.",
        [_soft_skill_comparison_subsection(profile, analysis, proposal, job_description, effective_resume_title)],
    )
    content_quality = _content_quality_section(
        profile,
        analysis,
        proposal,
        selected_bullets,
        report_document,
        formatting_note,
        candidate_answers,
        career_stage=career_stage,
        resume_format=resume_format,
    )

    summary_word_count = len(_words(proposal.professional_summary))
    measurable_mentions = _NUMBER_PATTERN.findall(" ".join(selected_bullets))
    measurable_count = len(measurable_mentions)
    action_count = 0
    opening_words: list[str] = []
    for bullet in selected_bullets:
        words = _words(bullet)
        if words:
            opener = words[0].casefold()
            opening_words.append(opener)
            if opener in _ACTION_VERBS:
                action_count += 1
    action_ratio = action_count / len(selected_bullets) if selected_bullets else 0.0
    repeated_openers = sorted({word for word in opening_words if opening_words.count(word) > 2})
    unsupported_critical = [
        requirement.requirement
        for requirement in analysis.requirements
        if requirement.priority == "critical"
        and any(
            match.requirement_id == requirement.id and match.status == "unsupported"
            for match in proposal.evidence_matches
        )
    ]

    bullet_count_warnings = []
    for experience in profile.experiences:
        count = len(selected_ids_by_experience.get(experience.id, []))
        if count < 2 or count > 7:
            bullet_count_warnings.append(f"{experience.employer}: {count}")

    resume_text = " ".join([proposal.professional_summary, *selected_bullets])
    normalized_resume_text = _normalize(resume_text)
    found_cliches = sorted(
        phrase for phrase in _CLICHES_AND_BUZZWORDS if _normalize(phrase) in normalized_resume_text
    )
    found_negative_phrases = sorted(
        phrase for phrase in _NEGATIVE_RESUME_PHRASES if _normalize(phrase) in normalized_resume_text
    )
    if found_negative_phrases:
        tone_status: ReportStatus = "fail"
        tone_detail = "Potentially negative wording was found: " + ", ".join(found_negative_phrases) + ". Reframe it around actions, learning, and positive outcomes."
    elif found_cliches:
        tone_status = "warning"
        tone_detail = "The overall tone is positive, but these common clichés or buzzwords were found: " + ", ".join(found_cliches) + ". Replace them with specific evidence."
    elif action_ratio < 0.6:
        tone_status = "warning"
        tone_detail = f"No common clichés were found, but only {action_count} of {len(selected_bullets)} selected bullets begin with a recognized action verb. Use more direct, positive accomplishment language."
    else:
        tone_status = "pass"
        tone_detail = "The resume uses generally positive, evidence-based language, and no common clichés or buzzwords were found."

    professional_domains, web_error = _professional_web_links(template_path, profile)
    if web_error:
        web_status: ReportStatus = "warning"
        web_detail = web_error
    elif professional_domains:
        web_status = "pass"
        web_detail = "Professional web links were found for: " + ", ".join(professional_domains) + ". Recruiters appreciate the convenience and credibility of a professional website or profile."
    else:
        web_status = "warning"
        web_detail = "Add a working LinkedIn, GitHub, portfolio, or professional website link to build web credibility and make verification easier for recruiters."

    total_resume_words = _estimated_resume_word_count(profile, analysis, proposal, effective_resume_title)

    grounding_issues = [
        issue
        for issue in validate_proposal(profile, analysis, proposal)
        if issue.issue.startswith("Generated candidate claim")
    ]
    job_level_checks = [
        _job_level_match(profile, analysis),
        ReportCheck(
            "Critical gaps are disclosed instead of invented",
            "pass" if not unsupported_critical else "warning",
            "No unsupported critical requirement was converted into a resume claim."
            if not unsupported_critical
            else "The following critical requirements remain gaps: " + "; ".join(unsupported_critical),
        ),
        ReportCheck(
            "Generated candidate claims are traceable to verified evidence",
            "pass" if not grounding_issues else "fail",
            "The professional summary and selected experience bullets passed deterministic grounding checks."
            if not grounding_issues
            else "Unsupported generated claim(s) were detected: "
            + " | ".join(issue.issue for issue in grounding_issues[:3]),
        ),
    ]
    measurable_checks = [
        ReportCheck(
            "There are five or more mentions of measurable results",
            "pass" if measurable_count >= 5 else "warning" if measurable_count >= 3 else "fail",
            f"The selected accomplishment bullets contain {measurable_count} measurable mention(s). Employers like to see the impact, scale, and results you delivered on the job.",
        )
    ]
    tone_checks = [
        ReportCheck(
            "The resume tone is positive and avoids common clichés and buzzwords",
            tone_status,
            tone_detail,
        ),
        ReportCheck(
            "Bullet openings are varied",
            "pass" if not repeated_openers else "warning",
            "Selected bullets use varied opening verbs."
            if not repeated_openers
            else "These opening words are repeated more than twice: " + ", ".join(repeated_openers),
        ),
    ]
    web_presence_checks = [
        ReportCheck(
            "You linked to a website that builds your web credibility",
            web_status,
            web_detail,
        )
    ]
    word_count_checks = [
        ReportCheck(
            "The resume contains fewer than 1,000 words",
            "pass" if total_resume_words < 1000 else "fail",
            f"The proposed complete resume contains approximately {total_resume_words} words. Keeping it under 1,000 words improves relevance and ease of reading.",
        ),
        ReportCheck(
            "The professional summary is concise",
            "pass" if 50 <= summary_word_count <= 80 else "warning",
            f"The proposed summary contains {summary_word_count} words; 50-80 words is a useful target for this template.",
        ),
        ReportCheck(
            "The number of bullets is recruiter-friendly",
            "pass" if not bullet_count_warnings else "warning",
            "Each role has between 2 and 7 selected bullets."
            if not bullet_count_warnings
            else "Review bullet counts for " + "; ".join(bullet_count_warnings) + ".",
        ),
    ]
    recruiter_tips = ReportSection(
        "Recruiter tips",
        "These checks review job-level fit, quantified impact, professional tone, web credibility, and overall resume length from a recruiter's perspective.",
        [
            ReportSubsection("Job Level Match", job_level_checks),
            ReportSubsection("Measurable Results", measurable_checks),
            ReportSubsection("Resume Tone", tone_checks),
            ReportSubsection("Web Presence", web_presence_checks),
            ReportSubsection("Word Count", word_count_checks),
        ],
    )

    formatting = ReportSection(
        "Formatting",
        "These checks inspect the proposed Word resume for ATS-friendly layout, readable and consistent typography, and standard page setup.",
        _formatting_sections(
            report_document,
            formatting_note,
            page_limit,
            exact_page_count=exact_page_count,
        ),
    )
    evidence_gaps = _evidence_gaps_section(
        profile,
        analysis,
        proposal,
        candidate_answers,
    )

    return ResumeReport(
        searchability=searchability,
        hard_skills=hard_skills,
        soft_skills=soft_skills,
        content_quality=content_quality,
        recruiter_tips=recruiter_tips,
        formatting=formatting,
        evidence_gaps=evidence_gaps,
    )

_EXPORT_NAMES = (
    'build_resume_report',
)

def exports() -> dict[str, Any]:
    return module_exports(globals(), _EXPORT_NAMES)


def activate(namespace: dict[str, Any]) -> None:
    activate_module(globals(), namespace)
