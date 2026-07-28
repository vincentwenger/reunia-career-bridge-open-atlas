(function () {
    'use strict';

    const state = {
        meetings: [],
        filteredMeetings: []
    };

    const STOP_WORDS = new Set([
        'about', 'after', 'again', 'against', 'also', 'among', 'another', 'because', 'been', 'before',
        'being', 'between', 'both', 'could', 'does', 'doing', 'during', 'each', 'from', 'further', 'have',
        'having', 'into', 'just', 'more', 'most', 'other', 'over', 'same', 'should', 'some', 'such', 'than',
        'that', 'their', 'them', 'then', 'there', 'these', 'they', 'this', 'those', 'through', 'under', 'until',
        'very', 'what', 'when', 'where', 'which', 'while', 'with', 'would', 'your', 'meeting', 'meetings',
        'summary', 'question', 'questions', 'action', 'actions', 'item', 'items', 'discussed', 'discussion',
        'need', 'needs', 'using', 'used', 'make', 'made', 'will', 'were', 'was', 'are', 'the', 'and', 'for',
        'you', 'our', 'but', 'not', 'all', 'can', 'has', 'had', 'its', 'how', 'why', 'who'
    ]);

    const elements = {};

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheElements();
        bindEvents();
        loadAnalytics();
    }

    function cacheElements() {
        [
            'analytics-period', 'analytics-score-filter', 'analytics-search', 'analytics-refresh',
            'analytics-status', 'analytics-total-meetings', 'analytics-meeting-period',
            'analytics-average-score', 'analytics-score-trend', 'analytics-total-questions',
            'analytics-average-questions', 'analytics-total-actions', 'analytics-average-actions',
            'analytics-trend-chart', 'analytics-trend-empty', 'analytics-trend-count',
            'analytics-average-pace', 'analytics-average-fillers', 'analytics-average-power',
            'analytics-average-negative-tone', 'analytics-average-pauses', 'analytics-average-form-score',
            'analytics-question-list', 'analytics-question-empty', 'analytics-topic-cloud',
            'analytics-topic-empty', 'analytics-improvement-list', 'analytics-improvement-empty',
            'analytics-meeting-table-body', 'analytics-table-empty'
        ].forEach(id => {
            elements[id] = document.getElementById(id);
        });
    }

    function bindEvents() {
        elements['analytics-period']?.addEventListener('change', applyFiltersAndRender);
        elements['analytics-score-filter']?.addEventListener('change', applyFiltersAndRender);
        elements['analytics-search']?.addEventListener(
            'input',
            window.AppUI?.debounce(applyFiltersAndRender, 240) || applyFiltersAndRender
        );
        elements['analytics-refresh']?.addEventListener('click', loadAnalytics);
    }

    async function loadAnalytics() {
        setLoading(true);
        setStatus('Loading your career progress...');

        try {
            const endpoint = window.AppUI?.appUrl('/api/career/interview-reviews') || '/api/career/interview-reviews';
            const response = await fetch(endpoint, {headers: {'Accept': 'application/json'}});
            if (!response.ok) {
                throw new Error(`Unable to load mock interviews (${response.status}).`);
            }

            const payload = await response.json();
            state.meetings = sortMeetingsByDate(ensureArrayPayload(payload));
            applyFiltersAndRender();
        } catch (error) {
            state.meetings = [];
            state.filteredMeetings = [];
            renderDashboard([]);
            setStatus(error.message || 'Unable to load career progress.', true);
            window.AppUI?.showToast(error.message || 'Unable to load career progress.', {type: 'error'});
        } finally {
            setLoading(false);
        }
    }

    function setLoading(isLoading) {
        if (!elements['analytics-refresh']) return;
        elements['analytics-refresh'].disabled = isLoading;
        elements['analytics-refresh'].classList.toggle('is-loading', isLoading);
    }

    function setStatus(message, isError = false) {
        const status = elements['analytics-status'];
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('is-error', isError);
    }

    function applyFiltersAndRender() {
        const periodDays = elements['analytics-period']?.value || 'all';
        const scoreFilter = elements['analytics-score-filter']?.value || 'all';
        const query = (elements['analytics-search']?.value || '').trim().toLowerCase();
        const cutoff = periodDays === 'all'
            ? null
            : new Date(Date.now() - Number(periodDays) * 24 * 60 * 60 * 1000);

        state.filteredMeetings = state.meetings.filter((meeting, index) => {
            const meetingDate = toDate(getMeetingDate(meeting));
            if (cutoff && (!meetingDate || meetingDate < cutoff)) return false;

            const score = getFinalScore(meeting);
            if (scoreFilter !== 'all' && getScoreCategory(score) !== scoreFilter) return false;

            if (query) {
                const searchable = getMeetingSearchableText(meeting, index, {includeTranscript: false});
                if (!searchable.includes(query)) return false;
            }

            return true;
        });

        renderDashboard(state.filteredMeetings);
        const selectedDescription = getSelectedPeriodDescription(periodDays);
        setStatus(`${state.filteredMeetings.length} of ${state.meetings.length} mock interviews shown · ${selectedDescription}`);
    }

    function renderDashboard(meetings) {
        renderKpis(meetings);
        renderTrend(meetings);
        renderDistribution(meetings);
        renderSpeakingMetrics(meetings);
        renderRankedTextList(
            elements['analytics-question-list'],
            elements['analytics-question-empty'],
            getQuestionFrequency(meetings),
            'meeting'
        );
        renderTopics(meetings);
        renderRankedTextList(
            elements['analytics-improvement-list'],
            elements['analytics-improvement-empty'],
            getTextFrequency(meetings.flatMap(meeting => normalizeTextList(meeting?.improvement_areas))),
            'meeting'
        );
        renderMeetingTable(meetings);
    }

    function renderKpis(meetings) {
        const scores = numericValues(meetings.map(getFinalScore));
        const questions = meetings.flatMap(getMeetingQuestions);
        const actionCount = meetings.reduce((total, meeting) => total + normalizeDynamoDBList(meeting?.action_items).length, 0);
        const averageScore = average(scores);
        const trend = getScoreTrend(meetings);

        setText('analytics-total-meetings', String(meetings.length));
        setText('analytics-meeting-period', meetings.length === 1 ? '1 mock interview matches the filters' : `${meetings.length} mock interviews match the filters`);
        setText('analytics-average-score', formatNumber(averageScore, 1));
        setText('analytics-score-trend', trend.label);
        setText('analytics-total-questions', String(questions.length));
        setText('analytics-average-questions', meetings.length ? `${formatNumber(questions.length / meetings.length, 1)} per mock interview` : 'Across answers and open questions');
        setText('analytics-total-actions', String(actionCount));
        setText('analytics-average-actions', meetings.length ? `${formatNumber(actionCount / meetings.length, 1)} per mock interview` : 'Career-plan actions across applications');
    }

    function renderTrend(meetings) {
        const points = meetings
            .map((meeting, index) => ({
                meeting,
                index,
                score: getFinalScore(meeting),
                date: toDate(getMeetingDate(meeting))
            }))
            .filter(item => item.score !== null)
            .sort((a, b) => (a.date?.getTime() || 0) - (b.date?.getTime() || 0))
            .slice(-12);

        setText('analytics-trend-count', `${points.length} scored ${points.length === 1 ? 'mock interview' : 'mock interviews'}`);

        const svg = elements['analytics-trend-chart'];
        const empty = elements['analytics-trend-empty'];
        if (!svg || !empty) return;

        const ns = 'http://www.w3.org/2000/svg';
        function createSvg(tag, attributes = {}) {
            const element = document.createElementNS(ns, tag);
            Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, String(value)));
            return element;
        }

        svg.replaceChildren();
        const chartTitle = createSvg('title', {id: 'analytics-trend-title'});
        chartTitle.textContent = 'Interview performance score trend';
        const chartDescription = createSvg('desc', {id: 'analytics-trend-description'});
        chartDescription.textContent = points.length
            ? 'A chart of mock-interview performance scores over time.'
            : 'No scored mock interviews are available for the selected filters.';
        svg.append(chartTitle, chartDescription);

        empty.hidden = points.length === 0;
        svg.hidden = points.length === 0;
        if (!points.length) return;

        const width = 820;
        const height = 280;
        const margin = {top: 22, right: 26, bottom: 48, left: 48};
        const plotWidth = width - margin.left - margin.right;
        const plotHeight = height - margin.top - margin.bottom;

        const defs = createSvg('defs');
        const gradient = createSvg('linearGradient', {id: 'analytics-area-gradient', x1: '0', y1: '0', x2: '0', y2: '1'});
        gradient.append(
            createSvg('stop', {offset: '0%', 'stop-color': '#1976d2', 'stop-opacity': '0.25'}),
            createSvg('stop', {offset: '100%', 'stop-color': '#1976d2', 'stop-opacity': '0.01'})
        );
        defs.appendChild(gradient);
        svg.appendChild(defs);

        [0, 25, 50, 75, 100].forEach(value => {
            const y = margin.top + plotHeight - (value / 100) * plotHeight;
            svg.appendChild(createSvg('line', {
                x1: margin.left, y1: y, x2: width - margin.right, y2: y,
                class: 'analytics-chart-grid-line'
            }));
            const label = createSvg('text', {
                x: margin.left - 10, y: y + 4, 'text-anchor': 'end', class: 'analytics-chart-axis-label'
            });
            label.textContent = String(value);
            svg.appendChild(label);
        });

        const xForIndex = index => points.length === 1
            ? margin.left + plotWidth / 2
            : margin.left + (index / (points.length - 1)) * plotWidth;
        const yForScore = score => margin.top + plotHeight - (Math.max(0, Math.min(100, score)) / 100) * plotHeight;
        const coordinates = points.map((point, index) => [xForIndex(index), yForScore(point.score)]);
        const linePoints = coordinates.map(([x, y]) => `${x},${y}`).join(' ');
        const areaPoints = `${margin.left},${margin.top + plotHeight} ${linePoints} ${width - margin.right},${margin.top + plotHeight}`;

        svg.appendChild(createSvg('polygon', {points: areaPoints, class: 'analytics-chart-area'}));
        svg.appendChild(createSvg('polyline', {points: linePoints, class: 'analytics-chart-line'}));

        points.forEach((point, index) => {
            const [x, y] = coordinates[index];
            const circle = createSvg('circle', {cx: x, cy: y, r: 5.5, class: 'analytics-chart-point'});
            const title = createSvg('title');
            title.textContent = `${getMeetingName(point.meeting, point.index)}: ${formatNumber(point.score, 1)} on ${formatShortDate(point.date)}`;
            circle.appendChild(title);
            svg.appendChild(circle);

            if (points.length <= 7 || index === 0 || index === points.length - 1 || index % 2 === 0) {
                const dateLabel = createSvg('text', {
                    x, y: height - 18, 'text-anchor': 'middle', class: 'analytics-chart-date-label'
                });
                dateLabel.textContent = formatChartDate(point.date);
                svg.appendChild(dateLabel);
            }
        });

    }

    function renderDistribution(meetings) {
        const counts = {strong: 0, developing: 0, focus: 0, unscored: 0};
        meetings.forEach(meeting => {
            counts[getScoreCategory(getFinalScore(meeting))] += 1;
        });
        const total = meetings.length || 1;

        Object.entries(counts).forEach(([category, count]) => {
            const row = document.querySelector(`[data-distribution="${category}"]`);
            if (!row) return;
            const countElement = row.querySelector('strong');
            const bar = row.querySelector('.analytics-progress-track span');
            if (countElement) countElement.textContent = String(count);
            if (bar) bar.style.width = `${(count / total) * 100}%`;
        });
    }

    function renderSpeakingMetrics(meetings) {
        const metricRows = meetings.map(getMeetingFormMetrics);
        const pace = numericValues(metricRows.map(metrics => numberFrom(metrics?.pace_wpm)));
        const fillers = numericValues(metricRows.map(metrics => numberFrom(metrics?.filler_words_count)));
        const power = numericValues(metricRows.map(metrics => numberFrom(metrics?.power_words_count)));
        const negativeTone = numericValues(metricRows.map(metrics => numberFrom(metrics?.negative_tone_count)));
        const pauses = numericValues(metricRows.map(metrics => numberFrom(metrics?.pauses_count)));
        const formScores = numericValues(meetings.map(meeting => numberFrom(meeting?.form_average_score)));

        setText('analytics-average-pace', formatNumber(average(pace), 0));
        setText('analytics-average-fillers', formatNumber(average(fillers), 1));
        setText('analytics-average-power', formatNumber(average(power), 1));
        setText('analytics-average-negative-tone', formatNumber(average(negativeTone), 1));
        setText('analytics-average-pauses', formatNumber(average(pauses), 1));
        setText('analytics-average-form-score', formatNumber(average(formScores), 1));
    }

    function renderRankedTextList(listElement, emptyElement, entries, unitLabel) {
        if (!listElement || !emptyElement) return;
        listElement.replaceChildren();
        const topEntries = entries.slice(0, 6);
        emptyElement.hidden = topEntries.length > 0;
        listElement.hidden = topEntries.length === 0;

        topEntries.forEach(entry => {
            const item = document.createElement('li');
            item.className = 'analytics-ranked-item';

            const copy = document.createElement('div');
            copy.className = 'analytics-ranked-copy';
            const title = document.createElement('strong');
            title.textContent = entry.label;
            title.title = entry.label;
            const meta = document.createElement('small');
            meta.textContent = entry.count === 1 ? `Found in 1 ${unitLabel}` : `Found in ${entry.count} ${unitLabel}s`;
            copy.append(title, meta);

            const count = document.createElement('span');
            count.className = 'analytics-ranked-count';
            count.textContent = `${entry.count}×`;

            item.append(copy, count);
            listElement.appendChild(item);
        });
    }

    function renderTopics(meetings) {
        const cloud = elements['analytics-topic-cloud'];
        const empty = elements['analytics-topic-empty'];
        if (!cloud || !empty) return;
        cloud.replaceChildren();

        const topics = getTopicFrequency(meetings).slice(0, 12);
        cloud.hidden = topics.length === 0;
        empty.hidden = topics.length > 0;

        topics.forEach(topic => {
            const chip = document.createElement('span');
            chip.className = 'analytics-topic';
            const label = document.createElement('span');
            label.textContent = topic.label;
            const count = document.createElement('strong');
            count.textContent = String(topic.count);
            chip.append(label, count);
            cloud.appendChild(chip);
        });
    }

    function renderMeetingTable(meetings) {
        const body = elements['analytics-meeting-table-body'];
        const empty = elements['analytics-table-empty'];
        if (!body || !empty) return;
        body.replaceChildren();

        const rows = meetings.slice(0, 12);
        empty.hidden = rows.length > 0;
        body.parentElement.parentElement.hidden = rows.length === 0;

        rows.forEach((meeting, index) => {
            const row = document.createElement('tr');
            const name = createCell(getMeetingName(meeting, index), 'analytics-meeting-name');
            const date = createCell(formatUserFriendlyDate(getMeetingDate(meeting)));
            const overall = createScoreCell(getFinalScore(meeting));
            const content = createScoreCell(numberFrom(meeting?.content_average_score));
            const form = createScoreCell(numberFrom(meeting?.form_average_score));
            const actions = createCell(String(normalizeDynamoDBList(meeting?.action_items).length));
            row.append(name, date, overall, content, form, actions);
            body.appendChild(row);
        });
    }

    function createCell(value, className = '') {
        const cell = document.createElement('td');
        if (className) cell.className = className;
        cell.textContent = value;
        return cell;
    }

    function createScoreCell(score) {
        const cell = document.createElement('td');
        const pill = document.createElement('span');
        const category = getScoreCategory(score);
        pill.className = `analytics-score-pill ${category}`;
        pill.textContent = formatNumber(score, 1);
        cell.appendChild(pill);
        return cell;
    }

    function getMeetingQuestions(meeting) {
        const gradedQuestions = normalizeDynamoDBList(meeting?.content_grades)
            .map(item => unwrapDynamoDBValue(item))
            .map(item => String(getValue(item?.question, '') || '').trim())
            .filter(Boolean);
        const openQuestions = normalizeTextList(meeting?.open_questions);
        return [...gradedQuestions, ...openQuestions];
    }

    function getQuestionFrequency(meetings) {
        const records = [];
        meetings.forEach(meeting => {
            const seen = new Set();
            getMeetingQuestions(meeting).forEach(question => {
                const key = normalizeFrequencyKey(question);
                if (!key || seen.has(key)) return;
                seen.add(key);
                records.push({key, label: question});
            });
        });
        return countRecords(records);
    }

    function getTextFrequency(values) {
        return countRecords(values.map(value => ({key: normalizeFrequencyKey(value), label: value})).filter(item => item.key));
    }

    function getTopicFrequency(meetings) {
        const counts = new Map();
        meetings.forEach((meeting, index) => {
            const perMeeting = new Set();
            const source = [
                getMeetingName(meeting, index),
                getMeetingSummary(meeting),
                ...normalizeTextList(meeting?.key_wins),
                ...normalizeTextList(meeting?.improvement_areas),
                ...normalizeTextList(meeting?.action_items),
                ...getMeetingQuestions(meeting)
            ].join(' ');

            const words = source
                .toLowerCase()
                .replace(/[^a-z0-9'-]+/g, ' ')
                .split(/\s+/)
                .map(word => word.replace(/^[-']+|[-']+$/g, ''))
                .filter(word => word.length >= 4 && !STOP_WORDS.has(word) && !/^\d+$/.test(word));

            words.forEach(word => perMeeting.add(word));
            perMeeting.forEach(word => counts.set(word, (counts.get(word) || 0) + 1));
        });

        return [...counts.entries()]
            .map(([label, count]) => ({label: toTitleCase(label), count}))
            .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }

    function countRecords(records) {
        const counts = new Map();
        records.forEach(({key, label}) => {
            if (!counts.has(key)) counts.set(key, {label, count: 0});
            counts.get(key).count += 1;
        });
        return [...counts.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }

    function normalizeTextList(value) {
        return normalizeDynamoDBList(value)
            .map(item => {
                const unwrapped = unwrapDynamoDBValue(item);
                if (typeof unwrapped === 'string' || typeof unwrapped === 'number') return String(unwrapped).trim();
                if (unwrapped && typeof unwrapped === 'object') {
                    return String(unwrapped.text || unwrapped.value || unwrapped.item || '').trim();
                }
                return '';
            })
            .filter(Boolean);
    }

    function getScoreTrend(meetings) {
        const chronologicalScores = meetings
            .map(meeting => ({score: getFinalScore(meeting), date: toDate(getMeetingDate(meeting))}))
            .filter(item => item.score !== null)
            .sort((a, b) => (a.date?.getTime() || 0) - (b.date?.getTime() || 0))
            .map(item => item.score);

        if (chronologicalScores.length < 2) return {value: null, label: 'Add more scored mock interviews to see a trend'};
        const split = Math.max(1, Math.floor(chronologicalScores.length / 2));
        const earlier = average(chronologicalScores.slice(0, split));
        const recent = average(chronologicalScores.slice(split));
        const difference = recent - earlier;
        if (Math.abs(difference) < 0.05) return {value: 0, label: 'Performance is steady across the period'};
        return {
            value: difference,
            label: `${difference > 0 ? 'Up' : 'Down'} ${Math.abs(difference).toFixed(1)} points versus earlier mock interviews`
        };
    }

    function getScoreCategory(score) {
        if (score === null || Number.isNaN(score)) return 'unscored';
        if (score >= 70) return 'strong';
        if (score >= 40) return 'developing';
        return 'focus';
    }

    function numberFrom(value) {
        const parsed = Number.parseFloat(getValue(value, ''));
        return Number.isNaN(parsed) ? null : parsed;
    }

    function numericValues(values) {
        return values.filter(value => value !== null && value !== undefined && !Number.isNaN(value));
    }

    function average(values) {
        return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    }

    function formatNumber(value, decimals = 1) {
        if (value === null || value === undefined || Number.isNaN(value)) return '—';
        return Number(value).toFixed(decimals);
    }

    function normalizeFrequencyKey(value) {
        return String(value || '')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, ' ')
            .trim()
            .replace(/\s+/g, ' ');
    }

    function toDate(value) {
        const date = new Date(getValue(value, ''));
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatShortDate(date) {
        if (!date) return 'Unknown date';
        return date.toLocaleDateString(window.AppI18n?.locale || undefined, {year: 'numeric', month: 'short', day: 'numeric'});
    }

    function formatChartDate(date) {
        if (!date) return 'N/A';
        return date.toLocaleDateString(window.AppI18n?.locale || undefined, {month: 'short', day: 'numeric'});
    }

    function getSelectedPeriodDescription(value) {
        if (value === '30') return 'last 30 days';
        if (value === '90') return 'last 90 days';
        if (value === '365') return 'last 12 months';
        return 'all available history';
    }

    function toTitleCase(value) {
        return String(value).replace(/\b\w/g, character => character.toUpperCase());
    }

    function setText(id, value) {
        if (elements[id]) elements[id].textContent = value;
    }
})();
