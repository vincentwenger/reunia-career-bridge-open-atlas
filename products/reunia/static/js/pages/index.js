(function () {
    'use strict';

    const page = document.querySelector('.home-page');
    if (!page || page.dataset.authenticated !== 'true') return;

    const ACTIVE_RECORDING_KEY = 'meetingAssistant.activeBrowserRecording';
    const ACTIVE_RECORDING_MAX_AGE_MS = 30000;
    const RECENT_MEETING_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
    const guidedUrl = page.dataset.guidedUrl || '/knowledge.html?view=materials&guided=1';
    const recorderUrl = page.dataset.recorderUrl || '/mock-interview';
    const reviewUrl = page.dataset.reviewUrl || '/interview-review';

    const primaryAction = document.getElementById('home-primary-action');
    const primaryActionLabel = document.getElementById('home-primary-action-label');
    const primaryDescription = document.getElementById('home-primary-description');
    const smartStatus = document.getElementById('home-smart-status');
    const smartStatusText = document.getElementById('home-smart-status-text');
    const continuePanel = document.getElementById('home-continue');
    const continueTitle = document.getElementById('home-continue-title');
    const continueSummary = document.getElementById('home-continue-summary');
    const continueLink = document.getElementById('home-continue-link');

    initializeHomepage();
    window.addEventListener('storage', function (event) {
        if (event.key === ACTIVE_RECORDING_KEY) initializeHomepage();
    });

    async function initializeHomepage() {
        const activeRecording = getActiveRecording();
        initializePrimaryAction(activeRecording);
        await loadLatestMeeting(activeRecording);
    }

    function initializePrimaryAction(activeRecording = getActiveRecording()) {
        if (!primaryAction || !primaryActionLabel || !primaryDescription) return;

        if (activeRecording) {
            const meetingName = String(activeRecording.meetingName || '').trim();
            primaryAction.href = recorderUrl;
            primaryActionLabel.textContent = 'Return to recording';
            primaryDescription.textContent = activeRecording.phase === 'processing'
                ? 'Your mock interview is being processed. Return to the recorder to follow its progress.'
                : 'A Mock Interview Recorder session is currently active.';
            showSmartStatus(
                activeRecording.phase === 'processing'
                    ? `${meetingName || 'Your mock interview'} is being processed.`
                    : `${meetingName || 'A mock interview'} is currently recording.`
            );
            return;
        }

        primaryAction.href = guidedUrl;
        primaryActionLabel.textContent = 'Get started';
        primaryDescription.textContent = 'Build an application workspace, prepare evidence, then record a mock interview.';
        hideSmartStatus();
    }

    async function loadLatestMeeting(activeRecording) {
        try {
            const endpoint = window.AppUI?.appUrl('/api/career/interview-reviews') || '/api/career/interview-reviews';
            const response = await fetch(endpoint, {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
            if (!response.ok) return;

            const meetings = sortMeetingsByDate(ensureArrayPayload(await response.json()));
            if (!meetings.length) return;

            const latestMeeting = meetings[0];
            if (!isRecentMeeting(getMeetingDate(latestMeeting))) return;
            const latestMeetingName = getMeetingName(latestMeeting, 0);
            const latestMeetingDate = getMeetingDate(latestMeeting);
            const latestMeetingId = getMeetingReference(latestMeeting, 0);
            const latestMeetingUrl = `${reviewUrl}?meeting=${encodeURIComponent(latestMeetingId)}`;
            const summary = getMeetingSummary(latestMeeting);

            continueTitle.textContent = latestMeetingName;
            continueSummary.textContent = [
                formatUserFriendlyDate(latestMeetingDate),
                summary ? truncateText(summary, 120) : 'Open the summary, scorecard, transcript, Ask AI, and follow-up items.'
            ].filter(Boolean).join(' · ');
            continueLink.href = latestMeetingUrl;
            continuePanel.hidden = false;

            if (!activeRecording) {
                showSmartStatus(`${latestMeetingName} is available in Interview Review.`);
            }
        } catch (error) {
            console.warn('The latest mock interview could not be loaded on the homepage:', error);
        }
    }

    function getActiveRecording() {
        try {
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

    function isRecentMeeting(rawDate) {
        const date = new Date(rawDate);
        if (Number.isNaN(date.getTime())) return false;
        const age = Date.now() - date.getTime();
        return age >= 0 && age <= RECENT_MEETING_MAX_AGE_MS;
    }

    function getMeetingReference(meeting, index) {
        return String(
            getValue(meeting?.meeting_id, '') ||
            getValue(meeting?.transcript_id, '') ||
            getValue(meeting?.id, '') ||
            getMeetingDate(meeting) ||
            `meeting-${index}`
        );
    }

    function showSmartStatus(message) {
        if (!smartStatus || !smartStatusText) return;
        smartStatusText.textContent = message;
        smartStatus.hidden = false;
    }

    function hideSmartStatus() {
        if (!smartStatus) return;
        smartStatus.hidden = true;
    }
})();
