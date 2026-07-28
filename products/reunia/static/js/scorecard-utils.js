'use strict';

function getScoreClass(score) {
        if (score === null || isNaN(score)) return 'text-score-neutral';
        if (score >= 70) return 'text-score-green';
        if (score >= 40) return 'text-score-orange';
        return 'text-score-red';
    }

function getScoreCardClass(score) {
        if (score === null || isNaN(score)) return 'score-neutral';
        if (score >= 70) return 'score-green';
        if (score >= 40) return 'score-orange';
        return 'score-red';
    }

function getScorecardEvidence(meeting) {
        const evidence = unwrapDynamoDBValue(meeting?.scorecard_evidence || {});
        return evidence && typeof evidence === 'object' && !Array.isArray(evidence) ? evidence : {};
    }

function getScoreDescription(score, meeting = null) {
        const evidence = getScorecardEvidence(meeting);
        const evidenceSummary = String(getValue(evidence.summary, '') || '').trim();
        const status = String(getValue(evidence.overall_grade_status, '') || '').toLowerCase();

        if (status === 'insufficient') {
            return evidenceSummary || 'Not enough meeting content was available to calculate a reliable performance score.';
        }
        if (status === 'preliminary' && evidenceSummary) {
            return evidenceSummary;
        }
        if (score === null || isNaN(score)) {
            return 'No complete score was generated for this meeting.';
        }

        if (score >= 70) {
            return 'Strong meeting performance. The session shows solid communication, useful content, and positive execution signals.';
        }

        if (score >= 40) {
            return 'Moderate meeting performance. The session includes useful material but has clear opportunities for improvement.';
        }

        return 'Low meeting performance. The scorecard indicates significant improvement opportunities in content, communication, or delivery.';
    }

function evidenceLabel(level) {
        const normalized = String(level || '').toLowerCase();
        if (normalized === 'reliable' || normalized === 'high') return 'High evidence';
        if (normalized === 'limited' || normalized === 'medium') return 'Limited evidence';
        if (normalized === 'low') return 'Low evidence';
        return 'Insufficient evidence';
    }

function renderScorecardEvidence(meeting) {
        const evidence = getScorecardEvidence(meeting);
        const hasEvidence = Object.keys(evidence).length > 0;
        const status = String(getValue(evidence.overall_grade_status, '') || '').toLowerCase();
        const overallLevel = getValue(evidence.overall_evidence_level, 'insufficient');
        const wordCount = Number(getValue(evidence.analyzed_word_count, 0)) || 0;
        const responseCount = Number(getValue(evidence.substantive_response_count, 0)) || 0;

        const title = document.getElementById('overall-score-title');
        const badge = document.getElementById('overall-evidence-badge');
        const circleLabel = document.getElementById('score-circle-label');
        const contentStatus = document.getElementById('content-evidence-status');
        const formStatus = document.getElementById('form-evidence-status');
        const contentNote = document.getElementById('content-evidence-note');
        const formNote = document.getElementById('form-evidence-note');

        if (!hasEvidence) {
            if (title) title.textContent = 'Overall Performance Score';
            if (badge) {
                badge.textContent = 'Legacy score';
                badge.className = 'score-evidence-badge evidence-legacy';
            }
            if (circleLabel) circleLabel.textContent = 'out of 100';
            if (contentStatus) contentStatus.textContent = 'Evidence not recorded';
            if (formStatus) formStatus.textContent = 'Evidence not recorded';
            if (contentNote) {
                contentNote.hidden = false;
                contentNote.textContent = 'This meeting was graded before evidence-aware scoring was added. Reprocess it to calculate evidence confidence.';
            }
            if (formNote) {
                formNote.hidden = false;
                formNote.textContent = 'This meeting was graded before word-count and sample-size checks were added.';
            }
            return;
        }

        if (title) {
            title.textContent = status === 'preliminary'
                ? 'Preliminary Performance Score'
                : status === 'insufficient'
                    ? 'Performance Score Unavailable'
                    : 'Overall Performance Score';
        }
        if (badge) {
            badge.textContent = evidenceLabel(overallLevel);
            badge.className = `score-evidence-badge evidence-${String(overallLevel).toLowerCase()}`;
        }
        if (circleLabel) {
            circleLabel.textContent = status === 'preliminary' ? 'preliminary' : 'out of 100';
        }

        const contentLevel = getValue(evidence.content_evidence_level, 'insufficient');
        const formLevel = getValue(evidence.form_evidence_level, 'insufficient');
        if (contentStatus) contentStatus.textContent = evidenceLabel(contentLevel);
        if (formStatus) formStatus.textContent = evidenceLabel(formLevel);

        if (contentNote) {
            contentNote.hidden = false;
            contentNote.textContent = String(contentLevel).toLowerCase() === 'reliable'
                ? `Reliable Content grade based on ${responseCount} substantive responses and ${wordCount} eligible words.`
                : String(contentLevel).toLowerCase() === 'limited'
                    ? `Preliminary Content grade based on ${responseCount} substantive responses and ${wordCount} eligible words. More evidence is needed to judge consistency and depth.`
                    : `Content grade unavailable: ${responseCount} substantive responses and ${wordCount} eligible words do not provide enough evidence.`;
        }
        if (formNote) {
            formNote.hidden = false;
            formNote.textContent = String(formLevel).toLowerCase() === 'reliable'
                ? `Reliable Form grade based on ${wordCount} eligible spoken words.`
                : String(formLevel).toLowerCase() === 'limited'
                    ? `Preliminary Form grade based on ${wordCount} eligible spoken words. Strong raw grades are moderated until approximately 300 words are available.`
                    : `Form grade unavailable: only ${wordCount} eligible spoken words were available; at least 60 are recommended.`;
        }
    }

function updateScoreCircle(score) {
        const scoreCircle = document.getElementById('score-circle');
        const degrees = score === null || isNaN(score)
            ? 0
            : Math.max(0, Math.min(100, score)) * 3.6;

        scoreCircle.style.background = `
            radial-gradient(circle at center, white 0 56%, transparent 57%),
            conic-gradient(rgba(255,255,255,0.95) ${degrees}deg, rgba(255,255,255,0.22) ${degrees}deg)
        `;
    }

function renderContentGrades(meeting) {
        const contentContainer = document.getElementById('content-grading-container');
        contentContainer.innerHTML = '';

        const evaluations = normalizeDynamoDBList(meeting.content_grades);
        const evidence = getScorecardEvidence(meeting);
        const contentStatus = String(getValue(evidence.content_grade_status, 'final') || '').toLowerCase();

        if (evaluations.length === 0) {
            contentContainer.innerHTML = `
                <div class="no-records-state">
                    <strong>No content grading available</strong>
                    No question-and-answer pairs were evaluated for this session.
                </div>
            `;
            return;
        }

        evaluations.forEach(evalItem => {
            const data = evalItem.M ? evalItem.M : evalItem;

            const question = getValue(data.question, 'N/A');
            const answer = getValue(data.answer, 'N/A');
            const relevance =
                getValue(data.relevance_analysis, '') ||
                getValue(data.relevance, 'No analysis generated.');
            const grade = getValue(data.grade, 'N/A');
            const gradeLabel = contentStatus === 'insufficient'
                ? 'Insufficient evidence'
                : contentStatus === 'preliminary'
                    ? `Preliminary: ${grade}`
                    : `Grade: ${grade}`;

            const itemDiv = document.createElement('div');
            itemDiv.className = 'qa-item';

            itemDiv.innerHTML = `
                <div class="qa-header">
                    <div class="qa-question">Question: ${escapeHtml(question)}</div>
                    <span class="badge badge-grade">${escapeHtml(gradeLabel)}</span>
                </div>

                <div class="qa-text">
                    <strong>Answer Provided:</strong> ${escapeHtml(answer)}
                </div>

                <div class="qa-analysis">
                    <strong>Relevance Analysis:</strong> ${escapeHtml(relevance)}
                </div>
            `;

            contentContainer.appendChild(itemDiv);
        });
    }

const FORM_METRIC_DETAIL_CONFIG = {
        filler_words: {
            title: 'Filler Words',
            description: 'Every filler-word occurrence detected in the selected meeting.',
            emptyMessage: 'No filler words were detected for this meeting.',
            listClass: 'word-detail-list'
        },
        power_words: {
            title: 'Power Words',
            description: 'Confident, positive, persuasive, or action-oriented words and phrases detected in the selected meeting.',
            emptyMessage: 'No power words were detected for this meeting.',
            listClass: 'word-detail-list'
        },
        negative_words: {
            title: 'Negative Words',
            description: 'Weak, uncertain, apologetic, pessimistic, or unnecessarily negative words and phrases detected in the selected meeting.',
            emptyMessage: 'No negative words were detected for this meeting.',
            listClass: 'word-detail-list'
        },
        negative_tone: {
            title: 'Negative Tone',
            description: 'Transcript excerpts that indicate a negative, uncertain, defensive, dismissive, or unprofessional tone.',
            emptyMessage: 'No negative-tone excerpts were detected for this meeting.',
            listClass: 'tone-detail-list'
        }
    };

function getFormMetrics(meeting) {
        return getMeetingFormMetrics(meeting);
    }

function normalizeFormMetricDetailList(value) {
        return normalizeDynamoDBList(value)
            .map(item => {
                if (item === null || item === undefined) return '';
                if (item.S !== undefined) return String(item.S).trim();
                if (typeof item === 'object') {
                    try {
                        return JSON.stringify(item);
                    } catch (error) {
                        return String(item).trim();
                    }
                }
                return String(item).trim();
            })
            .filter(Boolean);
    }

function resetFormMetricDetails() {
        const detailsPanel = document.getElementById('form-metric-details');

        document.querySelectorAll('[data-form-metric-detail]').forEach(button => {
            button.classList.remove('active');
            button.setAttribute('aria-expanded', 'false');
        });

        if (!detailsPanel) return;

        detailsPanel.hidden = true;
        detailsPanel.removeAttribute('data-detail-key');

        const detailsList = document.getElementById('form-metric-details-list');
        if (detailsList) detailsList.replaceChildren();
    }

function showFormMetricDetails(metrics, detailKey, triggerButton) {
        const config = FORM_METRIC_DETAIL_CONFIG[detailKey];
        const detailsPanel = document.getElementById('form-metric-details');
        const titleElement = document.getElementById('form-metric-details-title');
        const countElement = document.getElementById('form-metric-details-count');
        const descriptionElement = document.getElementById('form-metric-details-description');
        const listElement = document.getElementById('form-metric-details-list');

        if (!config || !detailsPanel || !titleElement || !countElement || !descriptionElement || !listElement) {
            return;
        }

        const items = normalizeFormMetricDetailList(metrics[detailKey]);

        document.querySelectorAll('[data-form-metric-detail]').forEach(button => {
            const isActive = button === triggerButton;
            button.classList.toggle('active', isActive);
            button.setAttribute('aria-expanded', String(isActive));
        });

        titleElement.textContent = config.title;
        countElement.textContent = `${items.length} ${items.length === 1 ? 'item' : 'items'}`;
        descriptionElement.textContent = config.description;
        listElement.className = `form-metric-details-list ${config.listClass}`;
        listElement.replaceChildren();

        if (items.length === 0) {
            const emptyItem = document.createElement('li');
            emptyItem.className = 'form-metric-details-empty';
            emptyItem.textContent = config.emptyMessage;
            listElement.appendChild(emptyItem);
        } else {
            items.forEach((item, index) => {
                const listItem = document.createElement('li');
                listItem.textContent = item;
                listItem.setAttribute('aria-label', `${config.title} item ${index + 1}: ${item}`);
                listElement.appendChild(listItem);
            });
        }

        detailsPanel.dataset.detailKey = detailKey;
        detailsPanel.hidden = false;
        detailsPanel.focus({ preventScroll: true });
        detailsPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

function bindFormMetricDetailButtons(metrics) {
        document.querySelectorAll('[data-form-metric-detail]').forEach(button => {
            button.onclick = () => {
                const detailKey = button.dataset.formMetricDetail;
                const detailsPanel = document.getElementById('form-metric-details');
                const isAlreadyOpen =
                    button.classList.contains('active') &&
                    detailsPanel &&
                    !detailsPanel.hidden;

                if (isAlreadyOpen) {
                    resetFormMetricDetails();
                    button.focus();
                    return;
                }

                showFormMetricDetails(metrics, detailKey, button);
            };
        });
    }

function formatRate(metrics, key) {
        const value = Number(getValue(metrics[key], NaN));
        return Number.isNaN(value) ? 'Rate unavailable' : `${value.toFixed(2)} per 100 words`;
    }

function formatMetricGrade(metrics, key) {
        const grade = getValue(metrics[key], 'N/A');
        const status = String(getValue(metrics.grade_status, 'final') || '').toLowerCase();
        if (status === 'insufficient') return 'N/A';
        if (status === 'preliminary' && grade !== 'N/A') return `${grade} · prelim.`;
        return grade;
    }

function renderFormMetrics(meeting) {
        const metrics = getFormMetrics(meeting);

        document.getElementById('form-pace-value').textContent = getValue(metrics.pace_wpm, 'N/A');
        document.getElementById('form-pace-grade').textContent = formatMetricGrade(metrics, 'pace_grade');

        document.getElementById('form-filler-value').textContent = getValue(metrics.filler_words_count, 'N/A');
        document.getElementById('form-filler-grade').textContent = formatMetricGrade(metrics, 'filler_words_grade');
        document.getElementById('form-filler-rate').textContent = formatRate(metrics, 'filler_words_rate_per_100');

        document.getElementById('form-power-value').textContent = getValue(metrics.power_words_count, 'N/A');
        document.getElementById('form-power-grade').textContent = formatMetricGrade(metrics, 'power_words_grade');
        document.getElementById('form-power-rate').textContent = formatRate(metrics, 'power_words_rate_per_100');

        document.getElementById('form-negative-words-value').textContent = getValue(metrics.negative_words_count, 'N/A');
        document.getElementById('form-negative-words-grade').textContent = formatMetricGrade(metrics, 'negative_words_grade');
        document.getElementById('form-negative-words-rate').textContent = formatRate(metrics, 'negative_words_rate_per_100');

        document.getElementById('form-negative-tone-value').textContent = getValue(metrics.negative_tone_count, 'N/A');
        document.getElementById('form-negative-tone-grade').textContent = formatMetricGrade(metrics, 'negative_tone_grade');
        document.getElementById('form-negative-tone-rate').textContent = formatRate(metrics, 'negative_tone_rate_per_100');

        document.getElementById('form-pauses-value').textContent = getValue(metrics.pauses_count, 'N/A');
        document.getElementById('form-pauses-grade').textContent = formatMetricGrade(metrics, 'pauses_grade');
        document.getElementById('form-pauses-rate').textContent = formatRate(metrics, 'pauses_rate_per_100');

        document.getElementById('form-overall-assessment').textContent =
            getValue(metrics.overall_assessment, 'N/A');

        resetFormMetricDetails();
        bindFormMetricDetailButtons(metrics);
    }
