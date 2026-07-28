'use strict';

function escapeHtml(value) {
    if (value === null || value === undefined) return '';

    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function unwrapDynamoDBValue(value) {
    if (value === null || value === undefined) return value;
    if (Array.isArray(value)) return value.map(unwrapDynamoDBValue);
    if (typeof value !== 'object') return value;

    if (Object.prototype.hasOwnProperty.call(value, 'S')) return value.S;
    if (Object.prototype.hasOwnProperty.call(value, 'N')) {
        const numberValue = Number(value.N);
        return Number.isNaN(numberValue) ? value.N : numberValue;
    }
    if (Object.prototype.hasOwnProperty.call(value, 'BOOL')) return Boolean(value.BOOL);
    if (Object.prototype.hasOwnProperty.call(value, 'NULL')) return null;
    if (Array.isArray(value.L)) return value.L.map(unwrapDynamoDBValue);
    if (Array.isArray(value.SS)) return [...value.SS];
    if (Array.isArray(value.NS)) return value.NS.map(item => Number(item));
    if (value.M && typeof value.M === 'object') return unwrapDynamoDBValue(value.M);

    return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [key, unwrapDynamoDBValue(item)])
    );
}

function getValue(value, fallback = 'N/A') {
    const unwrapped = unwrapDynamoDBValue(value);
    if (unwrapped === null || unwrapped === undefined || unwrapped === '') return fallback;
    return unwrapped;
}

function normalizeDynamoDBList(field) {
    const value = unwrapDynamoDBValue(field);
    if (value === null || value === undefined || value === '') return [];
    return Array.isArray(value) ? value : [value];
}

function formatUserFriendlyDate(rawString) {
    const rawValue = getValue(rawString, 'N/A');
    if (!rawValue || rawValue === 'N/A') return 'N/A';

    try {
        const dateObj = new Date(rawValue);
        if (Number.isNaN(dateObj.getTime())) return String(rawValue);

        return dateObj.toLocaleDateString(window.AppI18n?.locale || undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    } catch (error) {
        return String(rawValue);
    }
}

function truncateText(value, maxLength) {
    if (!value) return '';
    const text = String(value).trim();
    if (text.length <= maxLength) return text;
    return `${text.substring(0, maxLength)}...`;
}

function getMeetingName(meeting, index = 0) {
    return String(
        getValue(meeting?.meeting_name, '') ||
        getValue(meeting?.name, '') ||
        getValue(meeting?.title, '') ||
        `Mock Interview #${index + 1}`
    );
}

function getMeetingDate(meeting) {
    return getValue(meeting?.timestamp, '') || getValue(meeting?.date, 'N/A');
}

function getMeetingSummary(meeting) {
    return String(getValue(meeting?.summary, '') || '');
}

function getMeetingTranscript(meeting) {
    return String(getValue(meeting?.transcript, '') || getValue(meeting?.text, '') || '');
}

function getFinalScore(meeting) {
    const rawScore = [
        meeting?.final_grade,
        meeting?.final_weighted_grade,
        meeting?.overall_score
    ]
        .map(value => getValue(value, null))
        .find(value => value !== null && value !== undefined && value !== '');

    const score = Number.parseFloat(rawScore);
    return Number.isNaN(score) ? null : score;
}

function getScoreText(score) {
    return score === null || Number.isNaN(score) ? 'N/A' : score.toFixed(2);
}

function getMeetingFormMetrics(meeting) {
    const value = unwrapDynamoDBValue(meeting?.form_metrics || {});
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function getListText(field) {
    return normalizeDynamoDBList(field)
        .map(item => typeof item === 'object' ? JSON.stringify(item) : String(item))
        .join(' ');
}

function getMeetingSearchableText(meeting, index = 0, options = {}) {
    const {includeTranscript = true} = options;
    const metrics = getMeetingFormMetrics(meeting);
    const values = [
        getMeetingName(meeting, index),
        getMeetingDate(meeting),
        getScoreText(getFinalScore(meeting)),
        getMeetingSummary(meeting),
        getListText(meeting?.key_wins),
        getListText(meeting?.improvement_areas),
        getListText(meeting?.action_items),
        getListText(meeting?.open_questions),
        getValue(metrics.overall_assessment, '')
    ];

    if (includeTranscript) values.push(getMeetingTranscript(meeting));
    return values.join(' ').toLowerCase();
}

function ensureArrayPayload(payload) {
    const unwrapped = unwrapDynamoDBValue(payload);
    if (Array.isArray(unwrapped)) return unwrapped;
    if (Array.isArray(unwrapped?.items)) return unwrapped.items;
    if (Array.isArray(unwrapped?.meetings)) return unwrapped.meetings;
    if (Array.isArray(unwrapped?.data)) return unwrapped.data;
    throw new TypeError('The server returned an invalid mock-interview list.');
}

function sortMeetingsByDate(meetings) {
    return [...meetings].sort((a, b) => {
        const dateA = new Date(getMeetingDate(a));
        const dateB = new Date(getMeetingDate(b));
        const timeA = Number.isNaN(dateA.getTime()) ? 0 : dateA.getTime();
        const timeB = Number.isNaN(dateB.getTime()) ? 0 : dateB.getTime();
        return timeB - timeA;
    });
}
