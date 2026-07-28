# Réunia

**AI Meeting Coach — Prepare. Connect. Move forward.**

Réunia is a Flask-based meeting assistant that helps people prepare for meetings, capture and transcribe conversations, understand what happened, and follow through afterward. It combines browser and Windows desktop recording, AI-generated meeting reviews, live contextual Q&A, document knowledge, action tracking, analytics, and bilingual support.

> **AI-assisted development disclosure:** Réunia was built through a human-directed development workflow using **OpenAI Codex** and **ChatGPT Plus with GPT-5.6 Sol**. Multiple focused threads were used to design, implement, review, debug, and improve different parts of the application. The project owner defined the product direction, reviewed the generated work, tested the application, and made the final implementation and deployment decisions.

## Main features

- Guided meeting workflow from preparation to recording and review
- Browser-based and Windows desktop meeting capture
- Audio transcription and transcript cleanup
- AI-generated summaries, insights, key wins, improvement areas, and scorecards
- Meeting Review with sharing and download support
- Live Q&A using the active meeting, prepared materials, and reusable context
- Document Library and Knowledge Search across documents and meeting history
- Meeting preparation with meeting-specific materials and reusable AI context
- Action Center for follow-up tasks, priorities, deadlines, and completion status
- User analytics and administrator operational analytics
- English and French interface and recording support

## How Codex and GPT-5.6 were used

Réunia was not created from one prompt or one conversation. Development was divided into many focused threads, usually with one feature, bug, or improvement per thread. This made it easier to provide precise requirements, review the result, test the change, and continue from the latest working version of the project.

### OpenAI Codex

Codex was used as a coding agent working with the application source code. Its contributions included:

- Exploring and understanding the existing Flask application
- Implementing new backend routes, services, repositories, and data flows
- Updating Jinja templates, JavaScript, CSS, and responsive interface behavior
- Adding and improving features such as Meeting Preparation, Live Q&A, Meeting Review, Knowledge Search, the Action Center, analytics, localization, and the guided meeting flow
- Investigating defects in browser recording, desktop recording, authentication, DynamoDB access, Redis-backed jobs, AWS deployment, and user-interface behavior
- Refactoring code to improve separation of concerns, configuration handling, validation, and maintainability
- Creating and updating automated tests, scripts, architecture documentation, and deployment-related files
- Reviewing existing implementations and proposing targeted fixes rather than rebuilding unrelated parts of the application

Codex was especially useful when a task required repository-wide changes or coordinated edits across Python, HTML, JavaScript, CSS, tests, and configuration files.

### ChatGPT Plus with GPT-5.6 Sol

GPT-5.6 Sol in the ChatGPT web application was used as a product, design, and engineering collaborator. Its contributions included:

- Turning product ideas into concrete feature requirements and implementation plans
- Evaluating user flows and recommending simpler navigation and onboarding
- Reviewing interface wording, information architecture, and feature placement
- Designing meeting-analysis prompts, grading behavior, structured outputs, and parsing strategies
- Analyzing bugs and production logs before implementing fixes
- Reviewing tradeoffs involving Flask, DynamoDB, S3, Redis, AWS Lightsail, browser audio capture, and the Windows desktop recorder
- Improving English and French product copy
- Preparing project documentation, submission materials, the project story, the elevator pitch, image-gallery plans, and video-demo guidance

GPT-5.6 Sol was also used to challenge early ideas. Some features were simplified, reorganized, or postponed after reviewing their usefulness and the effect they would have on the user experience.

### Multi-thread development workflow

A typical development cycle was:

1. Start a focused Codex or ChatGPT thread for one feature, defect, or design question.
2. Provide the latest project version and describe the desired behavior.
3. Ask the model to inspect the existing implementation before proposing changes.
4. Review the recommendation and refine the scope when necessary.
5. Apply or receive the targeted code changes.
6. Run the application and tests, then manually verify the affected workflow.
7. Use the updated project as the starting point for the next focused thread.

Separate threads were used for areas such as recording, transcription, Meeting Review, grading, meeting preparation, knowledge search, actions, analytics, localization, branding, deployment, documentation, and submission preparation.

### Human oversight

The AI tools accelerated implementation and helped explore alternatives, but they did not independently determine the final product. The project owner:

- Chose the product concept, priorities, branding, and feature scope
- Supplied requirements and acceptance criteria
- Reviewed proposed designs and code changes
- Tested workflows in the browser and Windows desktop client
- Investigated unexpected behavior and requested corrections
- Managed infrastructure, configuration, deployment, and release decisions
- Decided which AI suggestions to accept, revise, or reject

All AI-assisted changes were subject to human review and testing before being treated as part of the project.

## How OpenAI is used inside Réunia

The use of Codex and GPT-5.6 to **build** Réunia is separate from the OpenAI functionality used **by the running application**.

At runtime, Réunia uses OpenAI services to support capabilities such as:

- Audio transcription
- Transcript cleanup and organization
- Meeting summaries and structured insights
- Content and communication scorecards
- Key wins and improvement recommendations
- Contextual meeting Q&A
- Questions over uploaded documents and meeting history
- Suggested follow-up actions and meeting metadata

Application prompts are stored separately from route and persistence logic so they can be reviewed and improved without tightly coupling them to the user interface.

## Architecture

The production application runs a Gunicorn-served Flask web application and a dedicated recorder worker inside an AWS Lightsail container.

- **Flask and Gunicorn** serve the web application and APIs.
- **DynamoDB** stores users, meetings, actions, shares, settings, support requests, knowledge metadata, and analytics-related records.
- **Amazon S3** stores uploaded documents and durable recorder jobs.
- **Redis** coordinates temporary state and background recorder processing.
- **OpenAI** provides transcription and generative meeting intelligence.
- **A Windows desktop client** provides advanced recording capabilities in addition to the browser recorder.

See the [technical architecture documentation](docs/technical-architecture.md) for the complete flowchart and explanation.

![Réunia technical architecture](docs/technical-architecture.png)

## Local development

### Requirements

- Python 3.11 or newer
- `pip`
- An OpenAI API key for AI features
- AWS credentials and development DynamoDB tables for features configured to use DynamoDB
- Access to the required S3 buckets when using S3-backed storage
- Redis when using Redis-backed development services or recorder jobs

The repository's `.env.example` file is the authoritative reference for supported environment variables and development configuration.

### Windows setup

1. Clone the repository and open PowerShell in the repository root.

2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the dependencies:

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Create the local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

5. Edit `.env` and configure the required OpenAI, AWS, DynamoDB, S3, Redis, storage-backend, and model settings for the services you plan to use.

6. Start the development server:

   ```powershell
   python app.py
   ```

7. Open `http://localhost:5000`.

### macOS or Linux setup

1. Clone the repository and open a terminal in the repository root.

2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install the dependencies:

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

5. Edit `.env` for the services and storage backends you plan to use.

6. Start the development server:

   ```bash
   python app.py
   ```

7. Open `http://localhost:5000`.

> Never commit `.env`, API keys, passwords, AWS credentials, or other secrets to source control.

## Testing

Run the complete test suite from the repository root:

```powershell
python -m pytest -q
```

Windows users can also run:

```powershell
.\tests\run_tests.bat
```

See the [testing guide](tests/TESTING_README.md) for targeted runs, reports, and troubleshooting.

## Cleaning up test accounts

Preview every DynamoDB record associated with an exact `user_id`:

```powershell
.\scripts\delete_dynamodb_user_records.bat test-user@example.com
```

After reviewing the table names and record keys, run destructive mode and enter the requested confirmation:

```powershell
.\scripts\delete_dynamodb_user_records.bat test-user@example.com --delete
```

The utility reads `.env`, requires every DynamoDB table name to be configured explicitly, uses the configured AWS Region, and skips tables that do not exist. For unattended test cleanup, `--delete --yes` bypasses the confirmation.

This command removes DynamoDB records only. It does not delete files or attachments stored in S3.

## Production deployment

The Docker image starts two supervised processes:

- Gunicorn serves the Flask application on port `5000`.
- The recorder worker processes durable recording jobs coordinated through Redis.

DynamoDB table names are never generated from `APP_ENV` or any other environment prefix. Every non-test deployment must explicitly provide `USERS_TABLE_NAME` and `TRANSCRIPTS_TABLE_NAME`, plus the table variable for each optional feature whose storage backend is `dynamodb`.

Production configuration is intentionally strict. Required secrets, DynamoDB table names, Redis connection details, S3 bucket names, storage backends, and related settings must be supplied through the AWS Lightsail container environment.

The application fails during startup when required production configuration is missing or unsafe. This fail-fast behavior helps prevent an apparently successful deployment from running with incomplete persistence or insecure defaults.

See [Admin Analytics and production operations](docs/admin-analytics.md) for infrastructure requirements, production safety settings, table upgrades, analytics behavior, and deployment validation.

## Project structure

```text
meeting_assistant/
  blueprints/       HTTP pages and API routes
  services/         Business logic and OpenAI integrations
  repositories/     DynamoDB, S3, local, and in-memory persistence
  prompts/          Meeting-analysis prompt templates
  utils/            Authentication, CSRF, errors, and shared helpers
static/              Browser JavaScript, styles, images, and downloads
templates/           Jinja HTML templates
scripts/             Infrastructure and maintenance scripts
tests/               Automated tests and test documentation
docs/                Architecture and operational documentation
app.py               Local development entry point
wsgi.py              Production WSGI entry point
```

Additional application entry points:

- `meeting_assistant/run_production.py` — production process supervisor
- `meeting_assistant/recorder_worker.py` — background recorder worker

## Documentation

- [Technical architecture](docs/technical-architecture.md)
- [Architecture PNG](docs/technical-architecture.png)
- [Architecture SVG](docs/technical-architecture.svg)
- [Admin Analytics and production operations](docs/admin-analytics.md)
- [Testing guide](tests/TESTING_README.md)

## Security notes

- Keep all credentials and secrets outside the repository.
- Use separate development and production resources.
- Use private S3 buckets and least-privilege IAM permissions.
- Do not expose Redis directly to the public internet.
- Validate production configuration before accepting traffic.
- Review AI-generated code and dependency changes before deployment.
- Avoid placing sensitive meeting content in logs.

## Project status

Réunia is an actively developed project. Features and setup details may continue to evolve as recording, meeting intelligence, knowledge retrieval, analytics, and deployment workflows are refined.
