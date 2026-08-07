'use strict';

const INTERVIEW_CRITERIA = [
    ['answer_relevance', 'answer-relevance', 'Answer relevance'],
    ['use_of_evidence', 'use-of-evidence', 'Use of evidence'],
    ['star_structure', 'star-structure', 'STAR structure'],
    ['clarity_conciseness', 'clarity-conciseness', 'Clarity and conciseness'],
    ['role_alignment', 'role-alignment', 'Role alignment'],
    ['confidence_of_delivery', 'confidence-of-delivery', 'Confidence of delivery'],
    ['handling_follow_up_questions', 'handling-follow-up-questions', 'Handling of follow-up questions'],
    ['questions_asked_employer', 'questions-asked-employer', 'Questions asked of the employer']
];

function getScoreClass(score) {
    if (score === null || Number.isNaN(score)) return 'text-score-neutral';
    if (score >= 70) return 'text-score-green';
    if (score >= 40) return 'text-score-orange';
    return 'text-score-red';
}

function getScoreCardClass(score) {
    if (score === null || Number.isNaN(score)) return 'score-neutral';
    if (score >= 70) return 'score-green';
    if (score >= 40) return 'score-orange';
    return 'score-red';
}

function asObject(value) {
    const unwrapped = unwrapDynamoDBValue(value || {});
    return unwrapped && typeof unwrapped === 'object' && !Array.isArray(unwrapped) ? unwrapped : {};
}

function asNumber(value) {
    const numeric = Number(getValue(value, value));
    return Number.isFinite(numeric) ? numeric : null;
}

function getInterviewScorecard(meeting) {
    const custom = asObject(meeting?.interview_scorecard);
    if (Object.keys(custom).length) return custom;

    const contentScore = asNumber(meeting?.content_average_score);
    const formScore = asNumber(meeting?.form_average_score);
    const overallScore = getFinalScore(meeting);
    return {
        overall_score: overallScore,
        evidence_level: 'legacy',
        grade_status: 'legacy',
        evidence_summary: 'This older review used the previous generic scorecard. Re-run the mock interview to generate all eight interview-specific criteria.',
        criteria: {
            answer_relevance: {score: contentScore, status: contentScore === null ? 'not_observed' : 'observed', summary: 'Legacy answer-quality score.'},
            use_of_evidence: {score: null, status: 'not_observed', summary: 'Not separately measured in this older review.'},
            star_structure: {score: null, status: 'not_observed', summary: 'Not separately measured in this older review.'},
            clarity_conciseness: {score: formScore, status: formScore === null ? 'not_observed' : 'observed', summary: 'Legacy delivery score.'},
            role_alignment: {score: null, status: 'not_observed', summary: 'Not separately measured in this older review.'},
            confidence_of_delivery: {score: formScore, status: formScore === null ? 'not_observed' : 'observed', summary: 'Legacy delivery score based on observable speech patterns.'},
            handling_follow_up_questions: {score: null, status: 'not_observed', summary: 'Not separately measured in this older review.'},
            questions_asked_employer: {score: null, status: 'not_observed', summary: 'Not separately measured in this older review.'}
        },
        observable_communication: {},
        safety_note: 'This scorecard evaluates observable communication only and does not infer emotions or sensitive traits.'
    };
}

function getScoreDescription(score, meeting = null) {
    const scorecard = getInterviewScorecard(meeting || {});
    const evidenceSummary = String(getValue(scorecard.evidence_summary, '') || '').trim();
    const status = String(getValue(scorecard.grade_status, '') || '').toLowerCase();
    if (status === 'insufficient') return evidenceSummary || 'Not enough interview evidence was available for a reliable score.';
    if (status === 'preliminary' && evidenceSummary) return evidenceSummary;
    if (status === 'legacy' && evidenceSummary) return evidenceSummary;
    if (score === null || Number.isNaN(score)) return 'No complete interview score was generated.';
    if (score >= 80) return 'Strong interview performance across the observed criteria. Use the answer-level coaching to make the strongest examples even more specific.';
    if (score >= 65) return 'Generally effective interview performance with several targeted opportunities to improve evidence, structure, or role alignment.';
    if (score >= 50) return 'Mixed interview performance. Prioritize the lowest-scoring criteria and practice the recommended answer rewrites.';
    return 'The interview needs focused practice. Start with answer relevance, confirmed evidence, and a clear STAR-style structure.';
}

function evidenceLabel(level) {
    const normalized = String(level || '').toLowerCase();
    if (normalized === 'reliable' || normalized === 'high') return 'Reliable evidence';
    if (normalized === 'limited' || normalized === 'medium') return 'Limited evidence';
    if (normalized === 'legacy') return 'Legacy review';
    return 'Insufficient evidence';
}

function renderScorecardEvidence(meeting) {
    const scorecard = getInterviewScorecard(meeting);
    const status = String(getValue(scorecard.grade_status, '') || '').toLowerCase();
    const level = getValue(scorecard.evidence_level, 'insufficient');
    const title = document.getElementById('overall-score-title');
    const badge = document.getElementById('overall-evidence-badge');
    const circleLabel = document.getElementById('score-circle-label');

    if (title) {
        title.textContent = status === 'preliminary'
            ? 'Preliminary Interview Score'
            : status === 'insufficient'
                ? 'Interview Score Unavailable'
                : 'Overall Interview Score';
    }
    if (badge) {
        badge.textContent = evidenceLabel(level);
        badge.className = `score-evidence-badge evidence-${String(level).toLowerCase()}`;
    }
    if (circleLabel) circleLabel.textContent = status === 'preliminary' ? 'preliminary' : 'out of 100';
}

function updateScoreCircle(score) {
    const scoreCircle = document.getElementById('score-circle');
    if (!scoreCircle) return;
    const degrees = score === null || Number.isNaN(score) ? 0 : Math.max(0, Math.min(100, score)) * 3.6;
    scoreCircle.style.background = `
        radial-gradient(circle at center, white 0 56%, transparent 57%),
        conic-gradient(rgba(255,255,255,0.95) ${degrees}deg, rgba(255,255,255,0.22) ${degrees}deg)
    `;
}

function scoreStatus(score) {
    if (score === null) return 'Not observed';
    if (score >= 80) return 'Strong';
    if (score >= 65) return 'Effective';
    if (score >= 50) return 'Developing';
    return 'Priority area';
}

function renderInterviewCriteria(meeting) {
    const scorecard = getInterviewScorecard(meeting);
    const criteria = asObject(scorecard.criteria);

    INTERVIEW_CRITERIA.forEach(([key, domKey, label]) => {
        const item = asObject(criteria[key]);
        const score = asNumber(item.score);
        const scoreElement = document.getElementById(`criterion-${domKey}-score`);
        const statusElement = document.getElementById(`criterion-${domKey}-status`);
        const summaryElement = document.getElementById(`criterion-${domKey}-summary`);
        const card = document.getElementById(`criterion-${domKey}`);

        if (scoreElement) scoreElement.textContent = score === null ? 'N/A' : Math.round(score).toString();
        if (statusElement) statusElement.textContent = scoreStatus(score);
        if (summaryElement) summaryElement.textContent = String(getValue(item.summary, '') || `${label} was not observed in this session.`);
        if (card) {
            card.dataset.scoreState = score === null ? 'not-observed' : score >= 80 ? 'strong' : score >= 65 ? 'effective' : score >= 50 ? 'developing' : 'priority';
        }
    });
}

function renderObservableCommunication(meeting) {
    const scorecard = getInterviewScorecard(meeting);
    const observable = asObject(scorecard.observable_communication);
    const values = {
        'observable-answer-count': asNumber(observable.answer_count),
        'observable-word-count': asNumber(observable.word_count),
        'observable-average-words': asNumber(observable.average_answer_words),
        'observable-pace': asNumber(observable.pace_wpm)
    };
    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (!element) return;
        element.textContent = value === null ? 'N/A' : (id === 'observable-answer-count' || id === 'observable-word-count' ? Math.round(value).toString() : value.toFixed(1));
    });
    const safety = document.getElementById('interview-safety-note');
    if (safety) safety.textContent = String(getValue(scorecard.safety_note, '') || 'This scorecard evaluates observable communication only and does not infer emotions or sensitive traits.');
}

function normalizeReviewList(value) {
    return normalizeDynamoDBList(value).map(item => String(getValue(item, item) || '').trim()).filter(Boolean);
}

function appendReviewList(parent, title, values, emptyText) {
    const section = document.createElement('section');
    section.className = 'answer-coaching-section';
    const heading = document.createElement('h4');
    heading.textContent = title;
    section.appendChild(heading);
    const items = normalizeReviewList(values);
    if (!items.length) {
        const empty = document.createElement('p');
        empty.className = 'answer-coaching-empty';
        empty.textContent = emptyText;
        section.appendChild(empty);
    } else {
        const list = document.createElement('ul');
        items.forEach(value => {
            const item = document.createElement('li');
            item.textContent = value;
            list.appendChild(item);
        });
        section.appendChild(list);
    }
    parent.appendChild(section);
}

function appendAnswerMetricChips(parent, metricsValue) {
    const metrics = asObject(metricsValue);
    const row = document.createElement('div');
    row.className = 'answer-metric-chips';
    INTERVIEW_CRITERIA.forEach(([key, , label]) => {
        const score = asNumber(metrics[key]);
        if (score === null) return;
        const chip = document.createElement('span');
        chip.className = 'answer-metric-chip';
        chip.textContent = `${label}: ${Math.round(score)}`;
        row.appendChild(chip);
    });
    if (row.children.length) parent.appendChild(row);
}

function legacyAnswerReviews(meeting) {
    return normalizeDynamoDBList(meeting?.content_grades).map((raw, index) => {
        const item = asObject(raw);
        return {
            question_number: index + 1,
            question: getValue(item.question, 'Interview question'),
            answer: getValue(item.answer, ''),
            score: null,
            metrics: {},
            what_worked: [],
            what_was_unclear: [getValue(item.relevance_analysis, 'Legacy relevance analysis unavailable.')],
            evidence_to_strengthen: ['Re-run this mock interview to generate evidence-specific coaching.'],
            better_answer_structure: ['Situation or context', 'Your responsibility', 'Your actions', 'Confirmed result'],
            sample_improved_answer: getValue(item.answer, ''),
            recommended_practice_action: 'Practice this answer again in the current Mock Interview workspace.',
            observable_delivery: {}
        };
    });
}

function renderAnswerReviews(meeting) {
    const container = document.getElementById('interview-answer-reviews');
    if (!container) return;
    let reviews = normalizeDynamoDBList(meeting?.interview_answer_reviews).map(asObject);
    if (!reviews.length) reviews = legacyAnswerReviews(meeting);
    container.replaceChildren();

    if (!reviews.length) {
        const empty = document.createElement('div');
        empty.className = 'no-records-state';
        const strong = document.createElement('strong');
        strong.textContent = 'No answer-level review available';
        empty.append(strong, document.createTextNode(' This session does not contain answer-by-answer coaching.'));
        container.appendChild(empty);
        return;
    }

    reviews.forEach((review, index) => {
        const card = document.createElement('article');
        card.className = 'answer-review-card';

        const header = document.createElement('div');
        header.className = 'answer-review-header';
        const titleWrap = document.createElement('div');
        const eyebrow = document.createElement('span');
        eyebrow.className = 'answer-review-eyebrow';
        eyebrow.textContent = `Answer ${Number(getValue(review.question_number, index + 1))}`;
        const title = document.createElement('h3');
        title.textContent = String(getValue(review.question, 'Interview question'));
        titleWrap.append(eyebrow, title);
        const score = asNumber(review.score);
        const badge = document.createElement('span');
        badge.className = 'answer-review-score';
        badge.textContent = score === null ? 'Evaluated' : `${Math.round(score)}/100`;
        header.append(titleWrap, badge);
        card.appendChild(header);

        const answer = document.createElement('div');
        answer.className = 'answer-transcript-box';
        const answerLabel = document.createElement('strong');
        answerLabel.textContent = 'Answer provided';
        const answerText = document.createElement('p');
        answerText.textContent = String(getValue(review.answer, '') || 'No answer transcript available.');
        answer.append(answerLabel, answerText);
        card.appendChild(answer);

        const observable = asObject(review.observable_delivery);
        const meta = document.createElement('div');
        meta.className = 'answer-observable-meta';
        const wordCount = asNumber(observable.word_count);
        const duration = asNumber(observable.duration_seconds);
        const pace = asNumber(observable.pace_wpm);
        [
            wordCount === null ? null : `${Math.round(wordCount)} words`,
            duration === null ? null : `${duration.toFixed(1)} sec`,
            pace === null ? null : `${pace.toFixed(1)} wpm`
        ].filter(Boolean).forEach(text => {
            const chip = document.createElement('span');
            chip.textContent = text;
            meta.appendChild(chip);
        });
        if (meta.children.length) card.appendChild(meta);
        appendAnswerMetricChips(card, review.metrics);

        const grid = document.createElement('div');
        grid.className = 'answer-coaching-grid';
        appendReviewList(grid, 'What worked', review.what_worked, 'No specific strength was recorded.');
        appendReviewList(grid, 'What was unclear', review.what_was_unclear, 'No unclear point was recorded.');
        appendReviewList(grid, 'Evidence that could strengthen it', review.evidence_to_strengthen, 'No additional evidence suggestion was recorded.');
        appendReviewList(grid, 'A better answer structure', review.better_answer_structure, 'No structure recommendation was recorded.');
        card.appendChild(grid);

        const sample = document.createElement('section');
        sample.className = 'answer-sample-section';
        const sampleHeading = document.createElement('h4');
        sampleHeading.textContent = 'Sample improved answer — confirmed facts only';
        const sampleText = document.createElement('p');
        sampleText.textContent = String(getValue(review.sample_improved_answer, '') || 'No evidence-safe sample answer was generated.');
        sample.append(sampleHeading, sampleText);
        card.appendChild(sample);

        const action = document.createElement('section');
        action.className = 'answer-practice-action';
        const actionHeading = document.createElement('h4');
        actionHeading.textContent = 'Recommended practice action';
        const actionText = document.createElement('p');
        actionText.textContent = String(getValue(review.recommended_practice_action, '') || 'Practice this answer again with a clearer structure and confirmed evidence.');
        action.append(actionHeading, actionText);
        card.appendChild(action);

        container.appendChild(card);
    });
}

function renderInterviewScorecard(meeting) {
    renderScorecardEvidence(meeting);
    renderInterviewCriteria(meeting);
    renderObservableCommunication(meeting);
    renderAnswerReviews(meeting);
}

function clearInterviewScorecard() {
    INTERVIEW_CRITERIA.forEach(([, domKey, label]) => {
        const score = document.getElementById(`criterion-${domKey}-score`);
        const status = document.getElementById(`criterion-${domKey}-status`);
        const summary = document.getElementById(`criterion-${domKey}-summary`);
        const card = document.getElementById(`criterion-${domKey}`);
        if (score) score.textContent = '—';
        if (status) status.textContent = 'Not observed';
        if (summary) summary.textContent = `Select a mock interview to view ${label.toLowerCase()}.`;
        if (card) card.dataset.scoreState = 'not-observed';
    });
    ['observable-answer-count', 'observable-word-count', 'observable-average-words', 'observable-pace'].forEach(id => {
        const element = document.getElementById(id);
        if (element) element.textContent = '-';
    });
    const container = document.getElementById('interview-answer-reviews');
    if (container) {
        container.innerHTML = '<div class="no-records-state"><strong>No mock interview selected</strong> Select a mock interview to review each answer.</div>';
    }
    const safety = document.getElementById('interview-safety-note');
    if (safety) safety.textContent = 'This scorecard evaluates only observable communication characteristics and confirmed content. It does not infer emotions, personality, health, protected traits, or other sensitive characteristics from voice or appearance.';
}
