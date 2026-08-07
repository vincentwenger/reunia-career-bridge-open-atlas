(function () {
    'use strict';

    const $ = id => document.getElementById(id);
    document.addEventListener('DOMContentLoaded', init);

    function init() {
        $('impact-refresh')?.addEventListener('click', loadImpact);
        $('impact-retry-button')?.addEventListener('click', loadImpact);
        loadImpact();
    }

    async function loadImpact() {
        setLoading(true);
        showDashboardState('loading');
        setStatus('Loading measurable outcomes...');
        try {
            const endpoint = window.AppUI?.appUrl('/api/career/impact') || '/api/career/impact';
            const response = await fetch(endpoint, {headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error(`Unable to load progress outcomes (${response.status}).`);
            const payload = await response.json();
            render(payload);
            const measured = Number(payload?.summary?.applications_measured || 0);
            const total = Number(payload?.summary?.applications_total || 0);
            setStatus(`${measured} of ${total} job applications currently have measurable outcome data.`);
            showDashboardState(measured > 0 ? 'content' : 'empty');
        } catch (error) {
            setStatus(error.message || 'Unable to load progress and outcomes.', true);
            showDashboardState('error', error.message || 'Unable to load progress and outcomes.');
        } finally {
            setLoading(false);
        }
    }

    function showDashboardState(state, message = '') {
        const loading = $('impact-loading-state');
        const empty = $('impact-empty-state');
        const error = $('impact-error-state');
        const content = $('impact-dashboard-content');
        [loading, empty, error].forEach(element => { if (element) element.hidden = true; });
        if (content) content.hidden = state !== 'content';
        if (state === 'loading' && loading) loading.hidden = false;
        if (state === 'empty' && empty) empty.hidden = false;
        if (state === 'error' && error) {
            window.AppUI?.showWorkspaceState(error, {state: 'error', message});
        }
    }

    function render(payload) {
        const summary = payload.summary || {};
        setText('impact-credentials', integer(summary.credentials_identified));
        setText('impact-terminology', integer(summary.terminology_clarified));
        setText('impact-claims', integer(summary.unsupported_claims_prevented));
        setText('impact-recovered', integer(summary.relevant_experience_recovered));
        setText('impact-alignment', signedPoints(summary.alignment_improvement));
        setText('impact-interview', signedPoints(summary.mock_interview_score_improvement));
        setText('impact-ready', integer(summary.interview_ready_applications));
        setText('impact-answers', integer(summary.weak_answers_improved));
        setText('impact-actions', integer(summary.actions_completed));
        setText('impact-measurement-note', payload.measurement_note || 'Only saved workflow evidence is counted.');

        renderList('impact-before-list', payload?.before_after?.before || []);
        renderList('impact-after-list', payload?.before_after?.after || []);
        renderAlignment(payload.applications || []);
        renderInterview(payload.interview_progress || {});
        renderApplications(payload.applications || [], summary);
        renderWarnings(payload.warnings || []);
    }

    function renderAlignment(applications) {
        const list = $('impact-alignment-list');
        const empty = $('impact-alignment-empty');
        if (!list || !empty) return;
        const measured = applications.filter(item => item.baseline_alignment_score != null && item.current_alignment_score != null);
        list.replaceChildren();
        empty.hidden = measured.length > 0;
        if (!measured.length) return;

        measured.sort((a, b) => Number(b.alignment_improvement || 0) - Number(a.alignment_improvement || 0));
        measured.forEach(item => {
            const row = document.createElement('div');
            row.className = 'impact-alignment-row';
            const baseline = clamp(Number(item.baseline_alignment_score));
            const current = clamp(Number(item.current_alignment_score));
            row.innerHTML = `
                <div class="impact-alignment-label"><strong>${escapeHtml(item.role || 'Role')}</strong><span>${escapeHtml(item.company || 'Company')}</span></div>
                <div class="impact-score-track" aria-label="Alignment ${baseline} to ${current}">
                    <span class="impact-score-baseline" style="width:${baseline}%"></span>
                    <span class="impact-score-current" style="width:${current}%"></span>
                </div>
                <div class="impact-score-values"><span>${formatScore(baseline)} → ${formatScore(current)}</span><strong>${signedPoints(item.alignment_improvement)}</strong></div>`;
            list.appendChild(row);
        });
    }

    function renderInterview(progress) {
        setText('impact-first-score', scoreOrDash(progress.first_score));
        setText('impact-latest-score', scoreOrDash(progress.latest_score));
        setText('impact-score-change', signedPoints(progress.improvement));
        const trend = $('impact-trend');
        const empty = $('impact-trend-empty');
        if (!trend || !empty) return;
        const points = Array.isArray(progress.trend) ? progress.trend : [];
        trend.replaceChildren();
        empty.hidden = points.length > 0;
        if (!points.length) return;
        points.slice(-10).forEach((point, index) => {
            const item = document.createElement('div');
            item.className = 'impact-trend-point';
            item.title = `${point.meeting_name || 'Mock interview'}: ${formatScore(point.score)}`;
            item.innerHTML = `<span style="height:${Math.max(8, clamp(Number(point.score)))}%"></span><small>${index + 1}</small>`;
            trend.appendChild(item);
        });
    }

    function renderApplications(applications, summary) {
        const body = $('impact-application-body');
        const empty = $('impact-application-empty');
        if (!body || !empty) return;
        body.replaceChildren();
        empty.hidden = applications.length > 0;
        setText('impact-application-count', `${Number(summary.applications_measured || 0)} measured`);

        applications.forEach(item => {
            const translation = Number(item.credentials_identified || 0) + Number(item.terminology_clarified || 0);
            const evidence = Number(item.unsupported_claims_prevented || 0) + Number(item.relevant_experience_recovered || 0);
            const mock = item.mock_interview || {};
            const resultTags = [];
            if (item.verified_resume_ready) resultTags.push('<span class="impact-tag success">Verified resume</span>');
            if (item.interview_preparation_ready) resultTags.push('<span class="impact-tag">Interview prep</span>');
            if (item.live) resultTags.push('<span class="impact-tag live">Live workflow</span>');
            if (!resultTags.length) resultTags.push('<span class="impact-tag muted">In progress</span>');

            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${escapeHtml(item.role || 'Role')}</strong><span>${escapeHtml(item.company || 'Company')}</span></td>
                <td><strong>${translation}</strong><span>${Number(item.credentials_identified || 0)} credentials · ${Number(item.terminology_clarified || 0)} terms</span></td>
                <td><strong>${evidence}</strong><span>${Number(item.unsupported_claims_prevented || 0)} prevented · ${Number(item.relevant_experience_recovered || 0)} recovered</span></td>
                <td><strong>${signedPoints(item.alignment_improvement)}</strong><span>${scorePair(item.baseline_alignment_score, item.current_alignment_score)}</span></td>
                <td><strong>${scoreOrDash(item.interview_readiness)}</strong><span>${escapeHtml(item.interview_readiness_status || 'Not started')}</span></td>
                <td><strong>${Number(mock.sessions || 0)} session${Number(mock.sessions || 0) === 1 ? '' : 's'}</strong><span>${signedPoints(mock.improvement)} · ${Number(item.weak_answers_improved || 0)} answers improved</span></td>
                <td><strong>${Number(item.actions_completed || 0)} / ${Number(item.actions_total || 0)}</strong><span>completed</span></td>
                <td><div class="impact-tags">${resultTags.join('')}</div></td>`;
            body.appendChild(tr);
        });
    }

    function renderWarnings(warnings) {
        const list = $('impact-warnings');
        if (!list) return;
        list.replaceChildren();
        warnings.forEach(warning => {
            const li = document.createElement('li');
            li.textContent = warning;
            list.appendChild(li);
        });
        list.hidden = warnings.length === 0;
    }

    function renderList(id, values) {
        const list = $(id);
        if (!list) return;
        list.replaceChildren();
        values.forEach(value => {
            const li = document.createElement('li');
            li.textContent = value;
            list.appendChild(li);
        });
    }

    function setLoading(value) {
        const button = $('impact-refresh');
        if (!button) return;
        button.disabled = value;
        button.classList.toggle('is-loading', value);
    }

    function setStatus(message, error = false) {
        const status = $('impact-status');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('is-error', error);
    }

    function setText(id, value) { const el = $(id); if (el) el.textContent = value; }
    function integer(value) { return String(Number(value || 0)); }
    function clamp(value) { return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0)); }
    function formatScore(value) { return Number(value).toFixed(1).replace('.0', ''); }
    function scoreOrDash(value) { return value == null ? '—' : `${formatScore(value)}/100`; }
    function signedPoints(value) { return value == null ? '—' : `${Number(value) >= 0 ? '+' : ''}${formatScore(value)} pts`; }
    function scorePair(before, after) { return before == null || after == null ? 'Baseline not complete' : `${formatScore(before)} → ${formatScore(after)}`; }
    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"]/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[char]));
    }
})();
