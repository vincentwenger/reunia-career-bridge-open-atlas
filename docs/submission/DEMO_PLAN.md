# Open Atlas Demo Plan

## Demo objective

Show one continuous, evidence-grounded career journey rather than a tour of every page.

Use the dedicated fictional candidate **Thomas MARTIN** and the public-safe French resume in
[`demo-data/CV_Thomas_MARTIN_Fictif_Demo.docx`](demo-data/CV_Thomas_MARTIN_Fictif_Demo.docx).
Keep all expensive AI results prepared before recording, then perform one short live AI interaction
to demonstrate that the product is functioning.

The resume supports the following facts: Thomas has more than 10 years of experience in banking
application production and support. He currently works under the French title **Responsable de
Production Applicative** and previously worked as an **Analyste Support Applicatif**. His stated
work includes support for six reporting and payment applications, incident coordination, SQL and
Oracle troubleshooting, Linux and Shell operations, release preparation, rollback procedures,
Oracle upgrades, scheduled-processing migrations, service metrics, operational documentation, and
business-user support. Do not add outage severity, team size, formal on-call duties, named U.S.
tools, certifications, cloud platforms, or quantified improvements that are not present in the resume.

## Three-minute recording script

### 0:00-0:25 - The problem

Open the public homepage and introduce the fictional candidate **Thomas MARTIN**, a French-market
application-production professional preparing for the U.S. job market.

Suggested narration:

> Internationally experienced candidates often have strong accomplishments that are difficult for U.S. employers to interpret. Reunia Career Bridge helps immigrants and newcomers translate their international experience into applications that U.S. employers can understand and evaluate - without inventing qualifications.

### 0:25-0:55 - Translation and verified evidence

Open **Career Translation** and show the French title **Responsable de Production Applicative**.
Explain that Career Bridge does not automatically replace it with an impressive American title.
Instead, it asks Thomas to confirm a factual target-market explanation such as coordinating
production support, incident resolution, releases, and service continuity for banking applications.
Show the confirmed explanation saved to the **Career Evidence Library** with its source and
confirmation state.

Show one additional source-backed item, such as Thomas's coordination of production incidents
across development, infrastructure, and database teams, or his preparation of deployment and
rollback procedures.

Key message: generated wording can improve clarity, but it cannot change the underlying fact.

### 0:55-1:35 - Target job and tailored resume

Open the prepared fictional application for **Senior Application Support Engineer**. Show:

- explicit requirements extracted from the job description;
- supported requirements such as production support, incident investigation, SQL, Oracle, Linux, release readiness, rollback planning, and cross-functional coordination;
- one partial, high-value leadership-scope requirement covering regulatory-reporting application support and whether Thomas directly managed employees;
- lower-priority gaps such as ServiceNow, observability platforms, ITIL certification, cloud support, formal 24/7 on-call work, or quantified service improvements.

Scroll to **Additional Experience Confirmation**. The target job is designed to produce one consolidated question. Select **Yes** when the form requires a Yes/No choice, then paste:

> I monitored regulatory-reporting applications used by banking clients, investigated production incidents, coordinated releases with development and infrastructure teams, and supported Oracle database upgrades. I did not directly manage employees or make hiring decisions.

Click **Save confirmed answers to Career Evidence Library**, wait for the visible success confirmation, and briefly open **Career Evidence Library** to show the saved record.

Suggested narration:

> Career Bridge asks Thomas what he actually did. He confirms that he monitored regulatory-reporting applications, investigated incidents, coordinated releases, and supported Oracle database upgrades. He also explicitly confirms that he did not manage employees. I'll save that answer now. The verified evidence is stored in Thomas's Career Evidence Library, so it can support this application and future applications without asking him to confirm the same facts repeatedly.

Then show one job-aligned resume bullet derived only from the confirmed technical scope and its evidence reference. The tailored resume must not claim employee supervision or hiring authority.

Key message: missing metrics, tools, or responsibilities are identified as evidence gaps rather than invented.

### 1:35-2:15 - Adaptive interview practice

Open a prepared mock interview and answer this question live:

> Tell me about a production incident you coordinated and how you worked with the technical teams.

Keep the answer under 20 seconds and stay within the resume facts: Thomas coordinated development,
infrastructure, and database teams and used SQL, technical logs, monitoring, and reproducible
escalation details to support resolution. Show the adaptive follow-up.

Then open the scorecard and briefly show relevance, evidence, clarity, and one improved answer
constrained to confirmed facts. Do not introduce incident severity, outage duration, business impact,
team size, or a percentage reduction in resolution time unless Thomas has separately confirmed it
in the Evidence Library.

### 2:15-2:45 - Action and continuity

Show the application-linked **Career Action Plan** with an action generated from a real evidence
gap, such as:

> Add a verified example of a significant production incident, including the service impact, Thomas's specific actions, and the measurable result if he can confirm them.

Show **Progress & Outcomes** only long enough to demonstrate that the system continues beyond
document generation.

### 2:45-3:00 - Closing

Suggested narration:

> Career Bridge connects career translation, verified evidence, job-specific applications, interview practice, and next actions in one workflow. It helps internationally experienced professionals communicate what they have actually accomplished without losing their experience in translation or inventing qualifications.

## Recording safeguards

- Use a fresh browser profile with notifications disabled.
- Use only the Thomas MARTIN synthetic account and the public-safe demo resume.
- Do not display the synthetic account email or password.
- Preload the target application, final resume, interview preparation, and scorecard.
- Confirm the background worker is healthy before recording.
- Keep a second browser tab on `/health` for troubleshooting, but do not show secrets or infrastructure identifiers.
- Avoid Admin Analytics, source management, AI configuration, and deployment screens in the three-minute video.
- Export at 1080p and verify that all text is readable at normal playback speed.

## Capture actual screenshots

After deploying and preparing the Thomas MARTIN synthetic account, run:

```bash
python scripts/submission/capture_demo_screenshots.py \
  --base-url https://career.reunia.app \
  --email "$DEPLOYMENT_VALIDATION_EMAIL" \
  --password "$DEPLOYMENT_VALIDATION_PASSWORD" \
  --application-id "<prepared-application-id>"
```

The script writes real browser captures to `docs/submission/screenshots/`. Do not replace them with conceptual mockups.
