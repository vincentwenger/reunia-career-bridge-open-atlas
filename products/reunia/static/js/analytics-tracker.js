(() => {
    'use strict';

    const body = document.body;
    if (!body) return;

    const appRoot = body.dataset.appRoot || '';
    const endpoint = `${appRoot}/api/analytics/track`;
    const eventEndpoint = `${appRoot}/api/analytics/event`;
    const heartbeatSeconds = Math.max(10, Number(body.dataset.analyticsHeartbeat || 30));
    const authenticated = body.dataset.authenticated === 'true';
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    const createId = () => {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID().replaceAll('-', '');
        }
        const random = Math.random().toString(36).slice(2);
        return `${Date.now().toString(36)}${random}${random}`.slice(0, 32).padEnd(24, '0');
    };

    // This ID is used only to make browser-generated product events idempotent.
    // Visitor/session identity and timestamps are assigned and signed by the server.
    const pageSessionId = createId();
    let lastInteractionAt = Date.now();
    let lastHeartbeatAt = Date.now();
    let pageViewPending = true;

    const markActive = () => { lastInteractionAt = Date.now(); };
    ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach((eventName) => {
        window.addEventListener(eventName, markActive, { passive: true });
    });

    const TRACKED_FEATURES = new Set([
        'career_bridge_overview',
        'career_profile',
        'baseline_resume',
        'career_evidence_library',
        'job_discovery',
        'job_applications',
        'resume_workflow',
        'resume_reports',
        'application_materials',
        'ai_configuration',
        'interview_preparation',
        'mock_interview',
        'interview_review',
        'career_action_plan',
        'progress',
        'admin_analytics',
        'help_support',
    ]);

    const featureForPage = () => {
        const explicitFeature = String(body.dataset.feature || '').trim().toLowerCase();
        if (!explicitFeature) return '';
        const normalizedFeature = explicitFeature.replaceAll('-', '_');
        return TRACKED_FEATURES.has(normalizedFeature) ? normalizedFeature : '';
    };

    const currentFeature = featureForPage();

    const payloadFor = (activeSeconds) => ({
        page_path: window.location.pathname || '/',
        feature: currentFeature,
        active_seconds: activeSeconds,
        page_view: pageViewPending,
        csrf_token: csrfToken,
    });

    const transmit = (payload, useBeacon = false) => {
        const bodyText = JSON.stringify(payload);
        if (useBeacon && navigator.sendBeacon) {
            const blob = new Blob([bodyText], { type: 'application/json' });
            navigator.sendBeacon(endpoint, blob);
            return;
        }
        fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(csrfToken ? {'X-CSRFToken': csrfToken} : {}),
            },
            body: bodyText,
            credentials: 'same-origin',
            keepalive: true,
        }).catch(() => { /* Analytics must never interrupt the application. */ });
    };

    const trackProductEvent = (metric, metadata = {}, eventId = '') => {
        if (!authenticated || !metric) return;
        const payload = {
            metric,
            event_id: eventId || createId(),
            metadata,
            csrf_token: csrfToken,
        };
        fetch(eventEndpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(csrfToken ? {'X-CSRFToken': csrfToken} : {}),
            },
            body: JSON.stringify(payload),
            credentials: 'same-origin',
            keepalive: true,
        }).catch(() => { /* Product analytics must never interrupt the application. */ });
    };

    window.ReuniaAnalytics = {
        track: trackProductEvent,
        createId,
    };

    if (currentFeature) {
        trackProductEvent('feature_used', { feature: currentFeature }, `page-${pageSessionId}-${currentFeature}`);
        if (currentFeature === 'interview_review') {
            trackProductEvent('meeting_review_opened', { feature: currentFeature }, `review-${pageSessionId}`);
        }
    }

    const heartbeat = (useBeacon = false) => {
        const now = Date.now();
        const elapsedSeconds = Math.max(0, Math.floor((now - lastHeartbeatAt) / 1000));
        const recentlyActive = now - lastInteractionAt <= heartbeatSeconds * 2000;
        const countVisibleInterval = document.visibilityState === 'visible' || useBeacon;
        const activeSeconds = countVisibleInterval && recentlyActive
            ? Math.min(elapsedSeconds, heartbeatSeconds)
            : 0;

        if (pageViewPending || activeSeconds > 0) {
            transmit(payloadFor(activeSeconds), useBeacon);
            pageViewPending = false;
        }
        lastHeartbeatAt = now;
    };

    heartbeat(false);
    const timer = window.setInterval(() => heartbeat(false), heartbeatSeconds * 1000);

    document.addEventListener('visibilitychange', () => {
        heartbeat(document.visibilityState === 'hidden');
        if (document.visibilityState === 'visible') markActive();
    });
    window.addEventListener('pagehide', () => {
        heartbeat(true);
        window.clearInterval(timer);
    });
})();
