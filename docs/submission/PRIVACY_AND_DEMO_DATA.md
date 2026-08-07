# Privacy and Demo Data

## Demo-data rule

The public demonstration, screenshots, repository fixtures, and video must use fictional or synthetic candidate information. Do not upload a real resume, address, immigration document, credential identifier, employer-confidential material, or private interview recording for the submission.

## Prepared fictional candidate

The submission uses **Thomas MARTIN**, a fictional application-production and support candidate.
Use only the public-safe resume at
`docs/submission/demo-data/CV_Thomas_MARTIN_Fictif_Demo.docx`. The document uses fictional employers,
credentials, contact information, and career facts, and its document metadata is scrubbed before
being bundled in the public repository.

## Product boundary

Career Bridge provides career communication and practice support. It does not provide immigration or legal advice, determine work authorization, certify credential equivalency, or submit applications to employers.

## Data handled by the product

Depending on enabled features, the application may store:

- account and profile information;
- imported resumes and candidate-confirmed evidence;
- target job descriptions and application records;
- generated resume and interview artifacts;
- mock-interview answers, reviews, and application-linked actions;
- operational analytics, support records, and AI usage metadata.

Production application and workflow records are owner-scoped. Large documents and snapshots are stored in private object storage. Contact details are excluded from normal AI proposal-generation context unless a specific workflow requires them.

## Submission account

Use a dedicated synthetic demonstration account with:

- a unique password not reused elsewhere;
- no administrator privileges unless the demo specifically requires them;
- no real personal or employer data;
- a prepared fictional application and interview session;
- a documented cleanup date after judging.

## Screenshots and video

Before publishing media, verify that it does not reveal:

- email addresses or passwords;
- AWS account IDs, table names, bucket names, or access keys;
- OpenAI keys, model-budget settings, or internal error details;
- browser bookmarks, notifications, personal tabs, or local file paths;
- real names, phone numbers, addresses, immigration status, or confidential documents.
