(function () {
    'use strict';

    const page = document.querySelector('.home-page');
    if (!page || page.dataset.authenticated !== 'true') return;

    const ACTIVE_RECORDING_KEY = 'meetingAssistant.activeBrowserRecording';
    const storageScope = encodeURIComponent(page.dataset.storageScope || 'default');
    const ACTIVE_MOCK_SESSION_KEY = `careerBridge.activeMockInterview.v2.${storageScope}`;
    const ACTIVE_RECORDING_MAX_AGE_MS = 30000;
    const summaryUrl = page.dataset.summaryUrl || '/api/career/dashboard-summary';
    const recorderUrl = page.dataset.recorderUrl || '/mock-interview';

    const primaryAction = document.getElementById('home-primary-action');
    const primaryActionLabel = document.getElementById('home-primary-action-label');
    const primaryDescription = document.getElementById('home-primary-description');
    const smartStatus = document.getElementById('home-smart-status');
    const smartStatusText = document.getElementById('home-smart-status-text');
    const currentContent = document.getElementById('home-current-content');
    const loadingState = document.getElementById('home-loading-state');
    const emptyState = document.getElementById('home-empty-state');
    const errorState = document.getElementById('home-error-state');
    const retryButton = document.getElementById('home-retry-button');

    retryButton?.addEventListener('click', initializeDashboard);
    window.addEventListener('storage', function (event) {
        if ([ACTIVE_RECORDING_KEY, ACTIVE_MOCK_SESSION_KEY].includes(event.key)) {
            initializeDashboard();
        }
    });

    initializeDashboard();

    async function initializeDashboard() {
        showState('loading');
        hideSmartStatus();
        const activeSession = getActiveSession();
        if (activeSession) renderActiveSession(activeSession);

        try {
            const endpoint = window.AppUI?.appUrl(summaryUrl) || summaryUrl;
            const response = await fetch(endpoint, {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
            if (!response.ok) throw new Error(`Dashboard request failed with ${response.status}`);
            const summary = await response.json();
            renderSummary(summary, activeSession);
            const isEmpty = Number(summary?.applications?.count || 0) === 0
                && !summary?.foundation?.profile?.complete
                && summary?.foundation?.translation?.state === 'not_started';
            showState(isEmpty ? 'empty' : 'ready');
        } catch (error) {
            console.warn('The Career Bridge dashboard summary could not be loaded:', error);
            renderUnavailableCurrentWork();
            showState('error');
        }
    }

    function renderSummary(summary, activeSession) {
        const applications = summary?.applications || {};
        const foundation = summary?.foundation || {};
        const recommended = summary?.recommended_action || {};

        setBadge(
            'home-profile-badge',
            foundation.profile?.complete ? 'Ready' : 'Needs setup',
            foundation.profile?.complete
        );
        setBadge(
            'home-translation-badge',
            translationBadge(foundation.translation?.state),
            foundation.translation?.state === 'ready'
        );
        setBadge(
            'home-evidence-badge',
            evidenceBadge(foundation.evidence_library),
            Boolean(foundation.evidence_library?.ready)
        );
        renderCurrentApplication(applications.active);

        if (!activeSession && primaryAction && primaryActionLabel) {
            primaryAction.href = appUrl(recommended.url || '/applications/job-discovery');
            primaryActionLabel.textContent = recommended.label || 'Discover jobs';
            if (primaryDescription && recommended.description) {
                primaryDescription.textContent = recommended.description;
            }
            showSmartStatus(buildSummaryStatus(summary));
        }
    }

    function renderCurrentApplication(application) {
        if (!currentContent) return;
        if (!application) {
            currentContent.innerHTML = `
                <div class="home-current-empty">
                    <strong>No job application yet</strong>
                    <p>Create an application from a job you already have, or use Job Discovery to find matching opportunities.</p>
                </div>`;
            return;
        }

        const title = [application.role, application.company].filter(Boolean).join(' at ') || 'Current application';
        const status = humanize(application.status || 'Active');
        const resumeStatus = application.resume_ready ? 'Resume ready' : 'Resume not ready';
        const preparationStatus = application.preparation_ready ? 'Interview preparation ready' : 'Interview preparation not ready';
        const readinessValue = application.interview_readiness == null ? null : Number(application.interview_readiness);
        const readinessStatus = readinessValue == null
            ? 'Readiness not calculated'
            : `Interview readiness ${Math.round(readinessValue)}%`;

        currentContent.innerHTML = `
            <div class="home-current-application">
                <div class="home-current-title-row">
                    <div>
                        <span>${escapeHtml(status)}</span>
                        <h3>${escapeHtml(title)}</h3>
                    </div>
                </div>
                <ul class="home-current-progress" aria-label="Application progress">
                    <li>${escapeHtml(resumeStatus)}</li>
                    <li>${escapeHtml(preparationStatus)}</li>
                    <li>${escapeHtml(readinessStatus)}</li>
                </ul>
                <div class="home-current-actions">
                    <a class="home-button home-button-primary" href="${escapeAttribute(appUrl(application.workspace_url || '/applications/?tab=applications'))}">Open application</a>
                </div>
            </div>`;
    }

    function renderUnavailableCurrentWork() {
        if (!currentContent) return;
        currentContent.innerHTML = `
            <div class="home-current-placeholder">
                <strong>Application summary unavailable</strong>
                <p>Open Job Applications to continue your work directly.</p>
                <a class="home-button home-button-secondary" href="${escapeAttribute(appUrl('/applications/?tab=applications'))}">Open Job Applications</a>
            </div>`;
    }

    function renderActiveSession(session) {
        if (!primaryAction || !primaryActionLabel) return;
        primaryAction.href = appUrl(recorderUrl);
        primaryActionLabel.textContent = session.phase === 'processing'
            ? 'View mock interview progress'
            : 'Return to mock interview';
        if (primaryDescription) {
            primaryDescription.textContent = session.phase === 'processing'
                ? 'Your mock interview is being processed. Return to follow its progress.'
                : 'An adaptive Mock Interview session is currently active.';
        }
        showSmartStatus(session.phase === 'processing'
            ? 'Your latest mock interview is being processed.'
            : 'You have an active mock interview session.');
    }

    function buildSummaryStatus(summary) {
        const applicationCount = Number(summary?.applications?.count || 0);
        const actionCount = Number(summary?.actions?.count || 0);
        const parts = [`${applicationCount} application${applicationCount === 1 ? '' : 's'}`];
        if (actionCount) parts.push(`${actionCount} action${actionCount === 1 ? '' : 's'} to review`);
        return parts.join(' · ');
    }

    function getActiveSession() {
        try {
            const adaptiveSessionId = window.localStorage.getItem(ACTIVE_MOCK_SESSION_KEY);
            if (adaptiveSessionId) return {phase: 'active', sessionId: adaptiveSessionId};
            const raw = window.localStorage.getItem(ACTIVE_RECORDING_KEY);
            if (!raw) return null;
            const status = JSON.parse(raw);
            const heartbeatAt = Number(status?.heartbeatAt || 0);
            const isFresh = heartbeatAt > 0 && Date.now() - heartbeatAt <= ACTIVE_RECORDING_MAX_AGE_MS;
            const isActivePhase = ['recording', 'stopping', 'processing'].includes(status?.phase);
            if (isFresh && isActivePhase) return status;
            window.localStorage.removeItem(ACTIVE_RECORDING_KEY);
        } catch (error) {
            window.localStorage.removeItem(ACTIVE_RECORDING_KEY);
        }
        return null;
    }

    function showState(state) {
        if (loadingState) loadingState.hidden = state !== 'loading';
        if (emptyState) emptyState.hidden = state !== 'empty';
        if (errorState) errorState.hidden = state !== 'error';
    }

    function showSmartStatus(message) {
        if (!smartStatus || !smartStatusText || !message) return;
        smartStatusText.textContent = message;
        smartStatus.hidden = false;
    }

    function hideSmartStatus() {
        if (!smartStatus) return;
        smartStatus.hidden = true;
        if (smartStatusText) smartStatusText.textContent = '';
    }

    function translationBadge(state) {
        if (state === 'ready') return 'Ready';
        if (state === 'needs_review') return 'Needs review';
        return 'Needs setup';
    }

    function evidenceBadge(evidenceLibrary) {
        if (!evidenceLibrary?.ready) return 'Needs setup';
        const itemCount = Math.max(0, Number(evidenceLibrary.item_count || 0));
        return `Ready · ${itemCount} item${itemCount === 1 ? '' : 's'}`;
    }

    function setBadge(id, label, ready) {
        const element = document.getElementById(id);
        if (!element) return;
        element.textContent = label;
        element.dataset.ready = ready ? 'true' : 'false';
    }

    function humanize(value) {
        return String(value || '')
            .replace(/^ApplicationStatus\./i, '')
            .replace(/[_-]+/g, ' ')
            .replace(/\b\w/g, character => character.toUpperCase());
    }

    function appUrl(path) {
        return window.AppUI?.appUrl(path) || path;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function escapeAttribute(value) {
        return escapeHtml(value);
    }
})();
