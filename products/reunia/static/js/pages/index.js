(function () {
    'use strict';

    const page = document.querySelector('.home-page');
    if (!page || page.dataset.authenticated !== 'true') return;

    const ACTIVE_RECORDING_KEY = 'meetingAssistant.activeBrowserRecording';
    const storageScope = encodeURIComponent(page.dataset.storageScope || 'default');
    const ACTIVE_MOCK_SESSION_KEY = `careerBridge.activeMockInterview.v2.${storageScope}`;
    const ACTIVE_RECORDING_MAX_AGE_MS = 30000;
    const RECENT_MEETING_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
    const guidedUrl = page.dataset.guidedUrl || '/career-profile?guided=1';
    const progressUrl = page.dataset.progressUrl || '/api/career/mvp-progress';
    const recorderUrl = page.dataset.recorderUrl || '/mock-interview';
    const reviewUrl = page.dataset.reviewUrl || '/interview-review';

    const primaryAction = document.getElementById('home-primary-action');
    const primaryActionLabel = document.getElementById('home-primary-action-label');
    const primaryDescription = document.getElementById('home-primary-description');
    const smartStatus = document.getElementById('home-smart-status');
    const smartStatusText = document.getElementById('home-smart-status-text');
    const progressSummary = document.getElementById('home-progress-summary');
    const progressLabel = document.getElementById('home-progress-label');
    const progressValue = document.getElementById('home-progress-value');
    const progressBar = document.getElementById('home-progress-bar');
    const progressTrack = progressSummary?.querySelector('[role="progressbar"]');
    const journeyContext = document.getElementById('home-journey-context');
    const continuePanel = document.getElementById('home-continue');
    const continueTitle = document.getElementById('home-continue-title');
    const continueSummary = document.getElementById('home-continue-summary');
    const continueLink = document.getElementById('home-continue-link');

    initializeHomepage();
    window.addEventListener('storage', function (event) {
        if ([ACTIVE_RECORDING_KEY, ACTIVE_MOCK_SESSION_KEY].includes(event.key)) initializeHomepage();
    });

    async function initializeHomepage() {
        const activeRecording = getActiveRecording();
        initializePrimaryAction(activeRecording);
        await Promise.all([
            loadMvpProgress(activeRecording),
            loadLatestMeeting(activeRecording)
        ]);
    }

    function initializePrimaryAction(activeRecording) {
        if (!primaryAction || !primaryActionLabel || !primaryDescription) return;

        if (activeRecording) {
            const meetingName = String(activeRecording.meetingName || '').trim();
            primaryAction.href = recorderUrl;
            primaryActionLabel.textContent = 'Return to mock interview';
            primaryDescription.textContent = activeRecording.phase === 'processing'
                ? 'Your mock interview scorecard is being generated. Return to follow its progress.'
                : 'An adaptive Mock Interview session is currently active.';
            showSmartStatus(
                activeRecording.phase === 'processing'
                    ? `${meetingName || 'Your mock interview'} is being processed.`
                    : `${meetingName || 'A mock interview'} is ready to continue.`
            );
            return;
        }

        primaryAction.href = guidedUrl;
        primaryActionLabel.textContent = 'Start with Career Profile';
        primaryDescription.textContent = 'Follow one coherent path from Career Profile and international resume translation to interview practice, scorecard review, and generated actions.';
        hideSmartStatus();
    }

    async function loadMvpProgress(activeRecording) {
        try {
            const endpoint = window.AppUI?.appUrl(progressUrl) || progressUrl;
            const response = await fetch(endpoint, {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
            if (!response.ok) throw new Error(`Progress request failed with ${response.status}`);
            const progress = await response.json();
            renderMvpProgress(progress, activeRecording);
        } catch (error) {
            console.warn('The MVP journey progress could not be loaded:', error);
            if (progressLabel) progressLabel.textContent = 'Recommended MVP journey';
            if (progressValue) progressValue.textContent = '10 connected steps';
        }
    }

    function renderMvpProgress(progress, activeRecording) {
        const completedCount = Number(progress?.completed_count || 0);
        const totalCount = Number(progress?.total_count || 10);
        const percent = Math.max(0, Math.min(100, Number(progress?.progress_percent || 0)));
        const steps = Array.isArray(progress?.steps) ? progress.steps : [];
        const currentStep = steps.find((step) => step.status === 'current') || steps.find((step) => !step.complete) || steps[steps.length - 1];

        if (progressLabel) progressLabel.textContent = completedCount >= totalCount ? 'MVP journey complete' : `Current focus · Step ${currentStep?.number || 1}`;
        if (progressValue) progressValue.textContent = `${completedCount} of ${totalCount} steps`;
        if (progressBar) progressBar.style.width = `${percent}%`;
        if (progressTrack) progressTrack.setAttribute('aria-valuenow', String(percent));

        const application = progress?.application || {};
        if (journeyContext && (application.company || application.role)) {
            journeyContext.textContent = `Active application: ${[application.role, application.company].filter(Boolean).join(' at ')}.`;
        }

        const stepByKey = new Map(steps.map((step) => [step.key, step]));
        document.querySelectorAll('[data-mvp-step]').forEach((card) => {
            const step = stepByKey.get(card.dataset.stepKey);
            if (!step) return;
            card.dataset.status = step.status || 'upcoming';
            card.href = step.url || card.href;
            const status = card.querySelector('.home-step-status');
            if (status) {
                status.textContent = step.complete
                    ? 'Complete'
                    : (step.status === 'current' ? 'Continue' : 'Upcoming');
            }
            const number = card.querySelector('.home-step-number');
            if (number) number.textContent = step.complete ? '✓' : String(step.number);
            card.setAttribute('aria-label', `${step.title}: ${step.complete ? 'complete' : (step.status === 'current' ? 'current step' : 'upcoming')}`);
        });

        if (!activeRecording && primaryAction && primaryActionLabel && currentStep) {
            primaryAction.href = currentStep.url || guidedUrl;
            primaryActionLabel.textContent = completedCount >= totalCount
                ? 'Review completed journey'
                : `Continue step ${currentStep.number}`;
            if (primaryDescription && completedCount < totalCount) {
                primaryDescription.textContent = currentStep.title
                    ? `${currentStep.title}. Complete this next step to keep the evidence-backed demonstration moving.`
                    : primaryDescription.textContent;
            }
            showSmartStatus(
                completedCount >= totalCount
                    ? 'All ten hackathon demonstration steps are complete.'
                    : `${completedCount} of ${totalCount} steps complete.`
            );
        }
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
                summary ? truncateText(summary, 120) : 'Open the Interview Scorecard, transcript, improved answers, and generated practice actions.'
            ].filter(Boolean).join(' · ');
            continueLink.href = latestMeetingUrl;
            continuePanel.hidden = false;

            if (activeRecording) {
                showSmartStatus(`${latestMeetingName} is available in Interview Review.`);
            }
        } catch (error) {
            console.warn('The latest mock interview could not be loaded on the homepage:', error);
        }
    }

    function getActiveRecording() {
        try {
            const adaptiveSessionId = window.localStorage.getItem(ACTIVE_MOCK_SESSION_KEY);
            if (adaptiveSessionId) {
                return {phase: 'active', meetingName: 'Adaptive mock interview', sessionId: adaptiveSessionId};
            }
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
