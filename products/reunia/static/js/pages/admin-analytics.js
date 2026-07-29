(() => {
    'use strict';

    const root = document.body.dataset.appRoot || '';
    const periodSelect = document.getElementById('admin-period');
    const refreshButton = document.getElementById('admin-refresh');
    const exportLink = document.getElementById('admin-export');
    const status = document.getElementById('admin-status');
    const searchInput = document.getElementById('admin-user-search');
    const activationFilter = document.getElementById('admin-user-activation-filter');
    const activityFilter = document.getElementById('admin-user-activity-filter');
    const tableBody = document.getElementById('admin-user-table-body');
    const emptyUsers = document.getElementById('admin-user-empty');
    const chart = document.getElementById('admin-activity-chart');
    const chartEmpty = document.getElementById('admin-chart-empty');
    const usageSourceWarning = document.getElementById('admin-usage-source-warning');
    const guestGeographyTrigger = document.getElementById('admin-guest-geography-trigger');
    const guestGeographyModal = document.getElementById('admin-guest-geography-modal');
    const guestGeographyDescription = document.getElementById('admin-guest-geography-description');
    const guestGeographyCoverage = document.getElementById('admin-guest-geography-coverage');
    const guestGeographyChart = document.getElementById('admin-guest-geography-chart');
    const guestGeographyEmpty = document.getElementById('admin-guest-geography-empty');
    const guestGeographyEmptyMessage = document.getElementById('admin-guest-geography-empty-message');
    const alertList = document.getElementById('admin-alert-list');
    const operationsTabButtons = Array.from(document.querySelectorAll('[data-admin-operations-tab]'));
    const systemHealthCard = document.getElementById('admin-system-health');
    const liveAiCard = document.getElementById('admin-live-ai-card');
    const incidentsCard = document.getElementById('admin-incidents-card');
    const incidentsRefreshButton = document.getElementById('admin-incidents-refresh');
    const incidentsStatus = document.getElementById('admin-incidents-status');
    const incidentList = document.getElementById('admin-incident-list');
    const incidentEmpty = document.getElementById('admin-incident-empty');
    const incidentUserFilter = document.getElementById('admin-incident-user-filter');
    const incidentFeatureFilter = document.getElementById('admin-incident-feature-filter');
    const incidentDateFilter = document.getElementById('admin-incident-date-filter');
    const incidentStatusFilter = document.getElementById('admin-incident-status-filter');
    const incidentTypeFilter = document.getElementById('admin-incident-type-filter');
    const incidentRepeatedFilter = document.getElementById('admin-incident-repeated-filter');
    const incidentsTabBadge = document.getElementById('admin-incidents-tab-badge');
    const usageModal = document.getElementById('admin-usage-modal');
    const usageTitle = document.getElementById('admin-usage-title');
    const usageEmail = document.getElementById('admin-usage-email');
    const usageDialogStatus = document.getElementById('admin-usage-dialog-status');
    const usageDialogContent = document.getElementById('admin-usage-dialog-content');
    const detailDocumentList = document.getElementById('admin-detail-document-list');
    const detailMeetingList = document.getElementById('admin-detail-meeting-list');
    const liveQaTrackingNote = document.getElementById('admin-live-qa-tracking-note');
    const desktopTrackingNote = document.getElementById('admin-desktop-tracking-note');
    const recordingDurationTrackingNote = document.getElementById('admin-recording-duration-tracking-note');

    const supportRefreshButton = document.getElementById('admin-support-refresh');
    const supportFilter = document.getElementById('admin-support-filter');
    const supportStatus = document.getElementById('admin-support-status');
    const supportList = document.getElementById('admin-support-list');
    const supportEmpty = document.getElementById('admin-support-empty');
    const supportDetailPlaceholder = document.getElementById('admin-support-detail-placeholder');
    const supportDetailContent = document.getElementById('admin-support-detail-content');
    const newSupportCount = document.getElementById('admin-new-support-count');
    const supportCountNote = document.getElementById('admin-support-count-note');
    const supportNewBadge = document.getElementById('admin-support-new-badge');
    const supportTabBadge = document.getElementById('admin-support-tab-badge');
    const tabButtons = Array.from(document.querySelectorAll('[data-admin-tab]'));
    const tabPanels = Array.from(document.querySelectorAll('[data-admin-panel]'));

    if (!periodSelect || !tableBody) return;

    const moveToPanel = (panelName, selectors) => {
        const panel = document.querySelector(`[data-admin-panel="${panelName}"]`);
        if (!panel) return;
        selectors.forEach((selector) => {
            const element = document.querySelector(selector);
            if (element) panel.appendChild(element);
        });
    };

    moveToPanel('overview', ['#admin-overview-kpis', '#admin-alerts-card', '#admin-overview-activity']);
    moveToPanel('product', ['#admin-acquisition-kpis', '#admin-product-kpis', '#admin-acquisition-grid', '#admin-workflow-grid', '#admin-document-card', '#admin-actions-card']);
    moveToPanel('operations', ['#admin-operations-navigation', '#admin-system-health', '#admin-live-ai-card', '#admin-incidents-card']);
    moveToPanel('users', ['#admin-usage-source-warning', '#admin-users-card']);
    moveToPanel('support', ['#admin-support-health-card', '#admin-support-inbox']);
    document.querySelectorAll('.admin-detail-metric-grid').forEach((container) => {
        if (!container.children.length) container.remove();
    });

    const setOperationsView = (viewName, {focus = false} = {}) => {
        const requested = viewName === 'incidents' ? 'incidents' : 'health';
        operationsTabButtons.forEach((button) => {
            const selected = button.dataset.adminOperationsTab === requested;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
            if (selected && focus) button.focus();
        });
        if (systemHealthCard) systemHealthCard.hidden = requested !== 'health';
        if (liveAiCard) liveAiCard.hidden = requested !== 'health';
        if (incidentsCard) incidentsCard.hidden = requested !== 'incidents';
    };

    const activateTab = (tabName, {focus = false, updateHash = true} = {}) => {
        const requested = tabButtons.some((button) => button.dataset.adminTab === tabName)
            ? tabName
            : 'overview';
        tabButtons.forEach((button) => {
            const selected = button.dataset.adminTab === requested;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
            if (selected) {
                if (focus) button.focus();
                window.requestAnimationFrame(() => {
                    const tabStrip = button.parentElement;
                    const centeredLeft = button.offsetLeft - (tabStrip.clientWidth - button.offsetWidth) / 2;
                    tabStrip.scrollTo({left: Math.max(0, centeredLeft), behavior: focus ? 'smooth' : 'auto'});
                });
            }
        });
        tabPanels.forEach((panel) => {
            panel.hidden = panel.dataset.adminPanel !== requested;
        });
        exportLink.hidden = requested !== 'users';
        if (updateHash && window.history?.replaceState) {
            window.history.replaceState(null, '', `#${requested}`);
        }
    };

    tabButtons.forEach((button, index) => {
        button.addEventListener('click', () => activateTab(button.dataset.adminTab));
        button.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            let nextIndex = index;
            if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
            if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabButtons.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = tabButtons.length - 1;
            activateTab(tabButtons[nextIndex].dataset.adminTab, {focus: true});
        });
    });
    document.querySelectorAll('[data-admin-open-tab]').forEach((button) => {
        button.addEventListener('click', () => activateTab(button.dataset.adminOpenTab, {focus: true}));
    });
    operationsTabButtons.forEach((button) => {
        button.addEventListener('click', () => setOperationsView(button.dataset.adminOperationsTab, {focus: true}));
    });
    setOperationsView('health');
    activateTab(window.location.hash.replace('#', ''), {updateHash: false});

    let users = [];
    let guestGeography = null;
    let supportRequests = [];
    let selectedSupportRequestId = null;
    let supportSummary = { total: 0, new: 0, read: 0, resolved: 0 };
    let incidents = [];
    let incidentsLoaded = false;
    let incidentSourcesAvailable = {events: true, support: true};
    let lastUsageTrigger = null;

    const formatNumber = new Intl.NumberFormat(window.AppI18n?.locale || undefined);
    const countryDisplayNames = typeof Intl.DisplayNames === 'function'
        ? new Intl.DisplayNames([window.AppI18n?.locale || navigator.language || 'en'], {type: 'region'})
        : null;
    const formatDuration = (seconds) => {
        const total = Math.max(0, Number(seconds) || 0);
        if (total < 60) return total > 0 ? '<1 min' : '0 min';
        const minutes = Math.round(total / 60);
        if (minutes < 60) return `${minutes} min`;
        const hours = Math.floor(minutes / 60);
        const remainder = minutes % 60;
        return remainder ? `${hours} hr ${remainder} min` : `${hours} hr`;
    };
    const formatRecordingDuration = (seconds) => {
        if (seconds == null) return '—';
        const totalSeconds = Math.max(0, Math.round(Number(seconds) || 0));
        if (!totalSeconds) return '—';
        if (totalSeconds < 60) return `${totalSeconds} sec`;
        const totalMinutes = Math.round(totalSeconds / 60);
        if (totalMinutes < 60) return `${totalMinutes} min`;
        const hours = Math.floor(totalMinutes / 60);
        const minutes = totalMinutes % 60;
        return minutes ? `${hours} hr ${minutes} min` : `${hours} hr`;
    };
    const formatDateTime = (epochSeconds) => {
        if (!epochSeconds) return 'Never';
        return new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(new Date(Number(epochSeconds) * 1000));
    };
    const formatIsoDateTime = (value) => {
        if (!value) return 'Unknown date';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, {
            dateStyle: 'medium',
            timeStyle: 'short',
        }).format(date);
    };
    const formatFileSize = (bytes) => {
        const value = Math.max(0, Number(bytes) || 0);
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
        if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
        return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    };
    const escapeHtml = (value) => String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    const statusLabel = (value) => ({
        new: 'New',
        read: 'Read',
        resolved: 'Resolved',
    }[value] || 'New');
    const countryName = (countryCode) => {
        const code = String(countryCode || '').toUpperCase();
        if (!code) return 'Unknown country';
        try {
            return countryDisplayNames?.of(code) || code;
        } catch (error) {
            return code;
        }
    };

    const setLoading = (loading) => {
        refreshButton.disabled = loading;
        refreshButton.textContent = loading ? 'Refreshing…' : 'Refresh';
    };

    const renderComparison = (elementId, comparison, {rate = false} = {}) => {
        const element = document.getElementById(elementId);
        if (!element) return;
        const current = Number(comparison?.current || 0);
        const previous = Number(comparison?.previous || 0);
        const difference = Number(comparison?.change || 0);
        element.classList.remove('is-positive', 'is-negative', 'is-neutral');
        if (!current && !previous) {
            element.textContent = 'No change from the previous period';
            element.classList.add('is-neutral');
            return;
        }
        if (!previous) {
            element.textContent = 'New activity vs previous period';
            element.classList.add('is-positive');
            return;
        }
        const positive = difference > 0;
        const negative = difference < 0;
        const value = rate
            ? `${Math.abs(difference).toFixed(1)} pts`
            : `${Math.abs(Number(comparison?.change_percentage || 0)).toFixed(1)}%`;
        const arrow = positive ? '↑' : negative ? '↓' : '→';
        element.textContent = `${arrow} ${value} vs previous period`;
        element.classList.add(positive ? 'is-positive' : negative ? 'is-negative' : 'is-neutral');
    };

    const renderSummary = (data) => {
        const summary = data.summary || {};
        guestGeography = data.guest_geography || null;
        document.getElementById('admin-unique-guests').textContent = formatNumber.format(summary.unique_guests || 0);
        document.getElementById('admin-registered-users').textContent = formatNumber.format(summary.registered_users || 0);
        document.getElementById('admin-active-users').textContent = formatNumber.format(summary.active_registered_users || 0);
        document.getElementById('admin-active-time').textContent = formatDuration(summary.registered_active_seconds || 0);
        document.getElementById('admin-unique-guests-note').textContent =
            `${formatNumber.format(summary.lifetime_unique_guests || 0)} anonymous browsers all time`;
        document.getElementById('admin-total-documents').textContent = formatNumber.format(summary.document_count || 0);
        document.getElementById('admin-total-document-size').textContent = formatFileSize(summary.document_total_bytes || 0);
        document.getElementById('admin-total-meetings').textContent = formatNumber.format(summary.saved_meeting_count || 0);
        document.getElementById('admin-total-live-qa-answers').textContent = formatNumber.format(summary.live_qa_answer_count || 0);
        document.getElementById('admin-total-desktop-downloads').textContent = formatNumber.format(summary.desktop_download_count || 0);
        document.getElementById('admin-total-desktop-uses').textContent = formatNumber.format(summary.desktop_use_count || 0);
        renderComparison('admin-unique-guests-trend', data.comparisons?.unique_guests);
        renderComparison('admin-active-users-trend', data.comparisons?.active_registered_users);
        renderComparison('admin-active-time-trend', data.comparisons?.registered_active_seconds);

        const sourceLabels = {
            documents: 'Document Library',
            meetings: 'saved meetings',
            live_qa_answers: 'Live Q&A answers',
            desktop_downloads: 'desktop downloads',
            desktop_uses: 'desktop client uses',
            recording_durations: 'recording-length statistics',
        };
        const unavailable = Object.entries(data.usage_sources || {})
            .filter(([, available]) => !available)
            .map(([source]) => sourceLabels[source] || source);
        usageSourceWarning.hidden = unavailable.length === 0;
        usageSourceWarning.textContent = unavailable.length
            ? `Some usage totals are temporarily unavailable: ${unavailable.join(', ')}.`
            : '';
        if (guestGeographyModal && !guestGeographyModal.hidden) renderGuestGeography();
    };

    const renderGuestGeography = () => {
        const geography = guestGeography || {};
        const countries = Array.isArray(geography.countries) ? geography.countries : [];
        const totalGuests = Math.max(0, Number(geography.total_guests) || 0);
        const locatedGuests = Math.max(0, Number(geography.located_guests) || 0);
        const unknownGuests = Math.max(0, Number(geography.unknown_guests) || 0);
        const coverage = Math.max(0, Number(geography.coverage_percentage) || 0);
        const periodLabel = periodSelect.options[periodSelect.selectedIndex]?.textContent || 'Selected period';
        guestGeographyDescription.textContent = `Unique anonymous browsers during ${periodLabel.toLowerCase()}.`;
        guestGeographyCoverage.textContent = totalGuests
            ? `${formatNumber.format(locatedGuests)} of ${formatNumber.format(totalGuests)} guest browsers have country data (${coverage.toFixed(1)}% coverage)${unknownGuests ? `; ${formatNumber.format(unknownGuests)} unknown.` : '.'}`
            : 'No guest browsers were recorded during the selected period.';

        guestGeographyChart.hidden = countries.length === 0;
        guestGeographyEmpty.hidden = countries.length > 0;
        if (!countries.length) {
            guestGeographyChart.replaceChildren();
            guestGeographyEmptyMessage.textContent = geography.tracking_configured
                ? 'No trusted country data was received for guest browsers in the selected period.'
                : 'Country tracking is not configured. Add a trusted reverse-proxy country header to begin collecting this privacy-preserving metric.';
            return;
        }

        const maximumGuests = Math.max(1, ...countries.map((item) => Number(item.guest_count) || 0));
        guestGeographyChart.setAttribute(
            'aria-label',
            `Guest browser distribution across ${formatNumber.format(countries.length)} countries during ${periodLabel.toLowerCase()}.`
        );
        guestGeographyChart.innerHTML = countries.map((item) => {
            const code = String(item.country_code || '').toUpperCase();
            const guests = Math.max(0, Number(item.guest_count) || 0);
            const percentage = Math.max(0, Number(item.percentage) || 0);
            const barWidth = Math.max(2, guests / maximumGuests * 100);
            return `
                <div class="admin-guest-geo-row" role="listitem">
                    <div class="admin-guest-geo-row-heading">
                        <span><strong>${escapeHtml(countryName(code))}</strong><small>${escapeHtml(code)}</small></span>
                        <b>${formatNumber.format(guests)} · ${percentage.toFixed(1)}%</b>
                    </div>
                    <div class="admin-guest-geo-track" aria-hidden="true"><i style="width:${barWidth.toFixed(1)}%"></i></div>
                </div>`;
        }).join('');
    };

    const openGuestGeography = () => {
        if (!guestGeographyModal) return;
        renderGuestGeography();
        guestGeographyModal.hidden = false;
        document.body.classList.add('admin-modal-open');
        guestGeographyModal.querySelector('.admin-usage-close')?.focus();
    };

    const closeGuestGeography = () => {
        if (!guestGeographyModal || guestGeographyModal.hidden) return;
        guestGeographyModal.hidden = true;
        document.body.classList.remove('admin-modal-open');
        guestGeographyTrigger?.focus();
    };

    const formatPercent = (value) => `${Number(value || 0).toFixed(1)}%`;
    const formatMoney = (value, { requestCount = 0, unpricedRequests = 0 } = {}) => {
        const numericAmount = Number(value || 0);
        const amount = Number.isFinite(numericAmount) ? Math.max(0, numericAmount) : 0;
        const totalRequests = Number(requestCount || 0);
        const missingCostData = Number(unpricedRequests || 0);
        if (missingCostData > 0 && missingCostData >= totalRequests && amount <= 0) {
            return window.AppI18n?.t('Not calculated') || 'Not calculated';
        }
        let formatted = amount > 0 && amount < 0.01
            ? '<$0.01'
            : `$${amount.toFixed(2)}`;
        if (missingCostData > 0) {
            const partial = window.AppI18n?.t('partial') || 'partial';
            formatted += ` (${partial})`;
        }
        return formatted;
    };
    const formatMilliseconds = (value) => {
        const milliseconds = Number(value || 0);
        if (!milliseconds) return '—';
        if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
        return `${(milliseconds / 1000).toFixed(1)} sec`;
    };
    const formatCompletionTime = (value) => {
        if (value == null) return '—';
        const hours = Number(value);
        if (!Number.isFinite(hours) || hours < 0) return '—';
        if (hours < 24) {
            const formattedHours = new Intl.NumberFormat(window.AppI18n?.locale || undefined, {
                maximumFractionDigits: 1,
            }).format(hours);
            return `${formattedHours} hr`;
        }
        const days = hours / 24;
        const formattedDays = new Intl.NumberFormat(window.AppI18n?.locale || undefined, {
            maximumFractionDigits: 1,
        }).format(days);
        return `${formattedDays} ${days < 1.05 ? 'day' : 'days'}`;
    };
    const statRow = (label, value, note = '') => `
        <div class="admin-stat-row"><span><strong>${escapeHtml(label)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ''}</span><b>${escapeHtml(value)}</b></div>`;

    const funnelDescriptions = {
        'Recording started': 'Recording sessions that users started during the selected period.',
        'Recording completed': 'Recordings that reached the stop or completion step.',
        'Recording uploaded': 'Completed recordings successfully sent to Réunia for processing.',
        'Processing succeeded': 'Meeting-processing attempts that finished without a recorded error.',
        'Meeting saved': 'Processed meetings stored and available in Meeting Review.',
        'Meeting Review opened': 'Times users opened the review experience for a meeting.',
        'Action created': 'Follow-up tasks created from meetings during the selected period.',
    };

    const featureDescriptions = {
        'Meeting Preparation': 'Accounts that opened or used the meeting-preparation area.',
        'Document Library': 'Accounts that used their reusable document library.',
        'Meeting Materials': 'Accounts that selected or uploaded materials for a meeting.',
        'AI Context': 'Accounts that configured context or response preferences for the AI.',
        'Knowledge Search': 'Accounts that searched or asked questions across their knowledge sources.',
        'Browser Recorder': 'Accounts that used the recorder built into the web application.',
        'Desktop Client': 'Accounts that successfully signed in through the desktop application.',
        'Live Q&A': 'Accounts that started a Live Q&A session or requested an AI answer.',
        'Meeting Review': 'Accounts that opened a saved meeting for review and follow-up.',
        'Career Action Plan': 'Accounts that opened or used the application-linked career planning area.',
        'Analytics': 'Accounts that opened the user-facing Analytics area.',
    };

    const operationDescriptions = {
        'Meeting processing': 'Turning a completed recording into a saved meeting and review.',
        'Document processing': 'Preparing an uploaded document so it can be searched or used by AI.',
        'Live Q&A': 'Generating an AI answer for a question during a live meeting.',
        'AI requests': 'Measured AI calls across supported Réunia features.',
    };

    const renderProgressCollection = (items, valueFor, renderRow, emptyTitle, emptyBody, zeroLabel) => {
        if (!items.length) {
            return `<div class="admin-section-empty"><strong>${escapeHtml(emptyTitle)}</strong><p>${escapeHtml(emptyBody)}</p></div>`;
        }
        const activeItems = items.filter((item) => Number(valueFor(item) || 0) > 0);
        const zeroItems = items.filter((item) => Number(valueFor(item) || 0) <= 0);
        const activeMarkup = activeItems.map(renderRow).join('');
        const zeroMarkup = zeroItems.map(renderRow).join('');
        if (!activeItems.length) {
            return `
                <div class="admin-section-empty"><strong>${escapeHtml(emptyTitle)}</strong><p>${escapeHtml(emptyBody)}</p></div>
                ${zeroItems.length ? `<details class="admin-zero-details"><summary>Show ${zeroItems.length} tracked ${escapeHtml(zeroLabel)}</summary><div>${zeroMarkup}</div></details>` : ''}`;
        }
        return `${activeMarkup}${zeroItems.length ? `<details class="admin-zero-details"><summary>Show ${zeroItems.length} zero-value ${escapeHtml(zeroLabel)}</summary><div>${zeroMarkup}</div></details>` : ''}`;
    };

    const renderAdvancedMetrics = (data) => {
        const growth = data.growth || {};
        const activation = data.activation || {};
        const retention = data.retention || {};
        const reliability = data.reliability || {};
        document.getElementById('admin-conversion-rate').textContent = formatPercent(growth.conversion_rate);
        document.getElementById('admin-conversion-note').textContent = `${formatNumber.format(growth.new_registrations || 0)} new registrations from ${formatNumber.format(growth.unique_guests || 0)} guests`;
        document.getElementById('admin-activation-rate').textContent = formatPercent(activation.activation_rate);
        document.getElementById('admin-activation-note').textContent = `${formatNumber.format(activation.activated_users || 0)} of ${formatNumber.format((activation.activated_users || 0) + (activation.not_activated_users || 0))} accounts activated`;
        document.getElementById('admin-retention-rate').textContent = formatPercent(retention.return_7_day_rate);
        document.getElementById('admin-retention-note').textContent = `${formatNumber.format(retention.returned_within_7_days || 0)} of ${formatNumber.format(retention.eligible_7_days || 0)} eligible users returned`;
        document.getElementById('admin-reliability-rate').textContent = formatPercent(reliability.overall_success_rate);
        document.getElementById('admin-reliability-note').textContent = `${formatNumber.format(reliability.failures || 0)} measured failure${Number(reliability.failures || 0) === 1 ? '' : 's'}`;
        renderComparison('admin-conversion-trend', data.comparisons?.conversion_rate, {rate: true});
        renderComparison('admin-reliability-trend', data.comparisons?.processing_success_rate, {rate: true});

        document.getElementById('admin-growth-metrics').innerHTML = [
            statRow('Unique guest browsers', formatNumber.format(growth.unique_guests || 0), 'Distinct anonymous browsers that visited a tracked page during the selected period.'),
            statRow('Registration-page visitors', formatNumber.format(growth.registration_page_visitors || 0), 'Guest browsers that reached the sign-in or registration page. This becomes more complete as event tracking accumulates.'),
            statRow('New registrations', formatNumber.format(growth.new_registrations || 0), 'New registered accounts created during the selected period.'),
            statRow('Conversion rate', formatPercent(growth.conversion_rate), 'New registrations divided by unique guest browsers in the selected period.'),
        ].join('');

        document.getElementById('admin-activation-retention').innerHTML = [
            statRow('Activated users', formatNumber.format(activation.activated_users || 0), 'Accounts that saved at least one meeting and opened Meeting Review.'),
            statRow('Activated within 1 day', formatNumber.format(activation.activated_within_1_day || 0), 'Activated accounts whose first saved meeting was created within 24 hours of registration.'),
            statRow('Activated within 7 days', formatNumber.format(activation.activated_within_7_days || 0), 'Activated accounts whose first saved meeting was created within seven days of registration.'),
            statRow('Average time to activation', activation.average_hours_to_activation == null ? '—' : `${activation.average_hours_to_activation} hr`, 'Average time from account creation to the first saved meeting among users with measurable activation dates.'),
            statRow('Next-day return', formatPercent(retention.return_next_day_rate), 'Percentage of eligible accounts that were active again exactly one day after registration.'),
            statRow('7-day return', formatPercent(retention.return_7_day_rate), 'Percentage of accounts at least seven days old that returned on any day from day 1 through day 7.'),
            statRow('30-day return', formatPercent(retention.return_30_day_rate), 'Percentage of accounts at least 30 days old that returned on any day from day 1 through day 30.'),
        ].join('');

        const funnel = Array.isArray(data.meeting_funnel) ? data.meeting_funnel : [];
        const funnelMax = Math.max(1, ...funnel.map((item) => Number(item.count || 0)));
        const funnelRow = (item) => {
            const index = funnel.indexOf(item);
            return `
                <div class="admin-progress-row">
                    <div><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(funnelDescriptions[item.label] || 'A measured step in the meeting workflow.')}</small></span><strong>${formatNumber.format(item.count || 0)}</strong></div>
                    <div class="admin-progress-track"><i style="width:${Number(item.count || 0) > 0 ? Math.max(2, Number(item.count || 0) / funnelMax * 100) : 0}%"></i></div>
                    <small>${index === 0 ? 'This is the starting point for the funnel.' : `${Number(item.from_previous_rate || 0).toFixed(1)}% compared with the previous workflow step.`}</small>
                </div>`;
        };
        document.getElementById('admin-meeting-funnel').innerHTML = renderProgressCollection(
            funnel, (item) => item.count, funnelRow,
            'No meeting workflow activity yet',
            'Recorded funnel steps will appear after users begin and complete meetings.',
            'steps',
        );

        const adoption = Array.isArray(data.feature_adoption) ? data.feature_adoption : [];
        const adoptionRow = (item) => `
            <div class="admin-progress-row">
                <div><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(featureDescriptions[item.label] || 'Accounts that used this product area at least once.')}</small></span><strong>${formatNumber.format(item.users || 0)} users</strong></div>
                <div class="admin-progress-track"><i style="width:${Number(item.percentage || 0) > 0 ? Math.max(2, Number(item.percentage || 0)) : 0}%"></i></div>
                <small>${Number(item.percentage || 0).toFixed(1)}% of all currently registered accounts used this feature at least once.</small>
            </div>`;
        document.getElementById('admin-feature-adoption').innerHTML = renderProgressCollection(
            adoption, (item) => item.users, adoptionRow,
            'No feature adoption recorded yet',
            'Feature usage will appear as registered accounts explore Réunia.',
            'features',
        );

        const operations = Array.isArray(reliability.operations) ? reliability.operations : [];
        const measuredOperations = operations.filter((item) => Number(item.successes || 0) + Number(item.failures || 0) > 0);
        document.getElementById('admin-health-body').innerHTML = measuredOperations.map((item) => `
            <tr><td><strong>${escapeHtml(item.operation)}</strong><small class="admin-operation-description">${escapeHtml(operationDescriptions[item.operation] || 'A measured Réunia operation.')}</small></td><td>${formatNumber.format(item.successes || 0)}</td><td>${formatNumber.format(item.failures || 0)}</td><td>${formatPercent(item.success_rate)}</td><td>${escapeHtml(formatMilliseconds(item.average_duration_ms))}</td></tr>`).join('') || '<tr><td colspan="5">No measured operations yet. Success and timing data will appear after an instrumented workflow runs.</td></tr>';

        const docs = data.document_health || {};
        document.getElementById('admin-document-health').innerHTML = [
            statRow('Current documents', formatNumber.format(docs.current_documents || 0), 'Files that are currently stored across all registered users’ Document Libraries.'),
            statRow('Uploaded in period', formatNumber.format(docs.uploaded_in_period || 0), 'Current library files whose recorded upload date falls within the selected period.'),
            statRow('Processing successes', formatNumber.format(docs.processing_successes || 0), 'Document-processing attempts that completed successfully during the selected period.'),
            statRow('Processing failures', formatNumber.format(docs.processing_failures || 0), 'Document-processing attempts that ended with a recorded error during the selected period.'),
        ].join('');
        document.getElementById('admin-file-types').innerHTML = (docs.file_types || []).map((item) => `<span>${escapeHtml((item.extension || 'unknown').toUpperCase())} · ${formatNumber.format(item.count || 0)}</span>`).join('') || '<span>No files</span>';

        const actions = data.action_outcomes || {};
        const completionSampleSize = Number(actions.completion_time_sample_size || 0);
        const completionTimeNote = completionSampleSize
            ? `Lifetime average across ${formatNumber.format(completionSampleSize)} timed completions from all accounts and dates; measured as wall-clock time from creation to completion.`
            : actions.average_completion_hours == null
                ? 'No completed actions have both usable creation and completion timestamps.'
                : 'Lifetime average across completed actions from all accounts and dates; measured as wall-clock time from creation to completion.';
        document.getElementById('admin-action-outcomes').innerHTML = [
            statRow('Total actions', formatNumber.format(actions.total_actions || 0), 'All follow-up actions currently found across registered accounts, regardless of creation date.'),
            statRow('Created in period', formatNumber.format(actions.created_in_period || 0), 'Follow-up actions created during the selected period.'),
            statRow('Open actions', formatNumber.format(actions.open_actions || 0), 'Actions that have not been marked done.'),
            statRow('Completed actions', formatNumber.format(actions.completed_actions || 0), 'Actions currently marked done.'),
            statRow('Overdue actions', formatNumber.format(actions.overdue_actions || 0), 'Open actions whose due date is earlier than today.'),
            statRow('Completion rate', formatPercent(actions.completion_rate), 'Completed actions divided by all actions currently stored.'),
            statRow('Average time to complete (lifetime)', formatCompletionTime(actions.average_completion_hours), completionTimeNote),
        ].join('');

        const live = data.live_qa_health || {};
        const ai = data.ai_usage || {};
        document.getElementById('admin-live-ai-health').innerHTML = [
            statRow('Live Q&A sessions', formatNumber.format(live.sessions || 0), 'Distinct Live Q&A sessions started during the selected period.'),
            statRow('Questions/requests', formatNumber.format(live.requests || 0), 'Questions sent to Live Q&A for an AI-generated response.'),
            statRow('AI answers', formatNumber.format(live.answers || 0), 'Live Q&A requests that produced and saved an AI answer.'),
            statRow('Live Q&A failures', formatNumber.format(live.failures || 0), 'Live Q&A requests that ended with a recorded error.'),
            statRow('Live Q&A success rate', formatPercent(live.success_rate), 'Successful AI answers divided by successful answers plus recorded failures.'),
            statRow('Average response time', formatMilliseconds(live.average_response_ms), 'Average measured time between a Live Q&A request and its result.'),
            statRow('Measured AI requests', formatNumber.format(ai.requests || 0), 'AI and transcription calls recorded during the selected period across supported features.'),
            statRow(
                'Estimated AI cost',
                formatMoney(ai.estimated_cost_usd || 0, {
                    requestCount: ai.requests,
                    unpricedRequests: ai.unpriced_requests,
                }),
                'Estimated from model-specific token pricing and recorded transcription duration. “Not calculated” or “partial” means one or more requests did not provide enough usage data.'
            ),
            statRow('Requests missing cost data', formatNumber.format(ai.unpriced_requests || 0), 'AI requests that were recorded but could not be priced because model, token, or audio-duration data was unavailable.'),
        ].join('');

        const support = data.support_health || {};
        document.getElementById('admin-support-health').innerHTML = [
            statRow('Total requests', formatNumber.format(support.total || 0), 'All support requests currently stored, including new, read, and resolved messages.'),
            statRow('New messages', formatNumber.format(support.new || 0), 'Support requests that an administrator has not opened yet.'),
            statRow('Resolved messages', formatNumber.format(support.resolved || 0), 'Support requests currently marked resolved.'),
            statRow('Unread over 24 hours', formatNumber.format(support.unread_over_24_hours || 0), 'New support requests that have waited more than 24 hours without being opened.'),
            statRow('Average first-read time', support.average_first_read_hours == null ? '—' : `${support.average_first_read_hours} hr`, 'Average time from submission until an administrator first opened the request.'),
            statRow('Average resolution time', support.average_resolution_hours == null ? '—' : `${support.average_resolution_hours} hr`, 'Average time from submission until the request was marked resolved.'),
        ].join('');

        const alerts = Array.isArray(data.alerts) ? data.alerts : [];
        const alertsCard = document.getElementById('admin-alerts-card');
        alertsCard.hidden = alerts.length === 0;
        alertList.innerHTML = alerts.map((alert) => {
            const content = `<strong>${escapeHtml(alert.title || 'Attention needed')}</strong><p>${escapeHtml(alert.detail || '')}</p>`;
            if (['view_incidents', 'review_repeated_failures'].includes(alert.action)) {
                return `<button class="admin-alert admin-alert-button is-${escapeHtml(alert.severity || 'info')}" type="button" data-admin-alert-action="view_incidents">${content}<span class="admin-alert-action">${escapeHtml(alert.action_label || 'View incidents')} →</span></button>`;
            }
            return `<article class="admin-alert is-${escapeHtml(alert.severity || 'info')}">${content}</article>`;
        }).join('');
    };

    const renderUsers = () => {
        const query = (searchInput.value || '').trim().toLowerCase();
        const activation = activationFilter?.value || 'all';
        const activity = activityFilter?.value || 'all';
        const filtered = users.filter((user) => {
            const haystack = `${user.full_name || ''} ${user.email || ''}`.toLowerCase();
            const matchesSearch = !query || haystack.includes(query);
            const matchesActivation = activation === 'all'
                || (activation === 'activated' && Boolean(user.activated))
                || (activation === 'not-activated' && !user.activated);
            const hasPeriodActivity = Boolean(user.period_has_activity)
                || Number(user.period_active_seconds || 0) > 0
                || Number(user.active_day_count || 0) > 0;
            const matchesActivity = activity === 'all'
                || (activity === 'active' && hasPeriodActivity)
                || (activity === 'inactive' && !hasPeriodActivity);
            return matchesSearch && matchesActivation && matchesActivity;
        });

        tableBody.innerHTML = filtered.map((user) => {
            const displayName = user.full_name || user.email || user.user_id || 'Registered user';
            const email = user.email || user.user_id || '';
            return `
                <tr>
                    <td>
                        <div class="admin-user-identity">
                            <span class="admin-user-avatar" aria-hidden="true">${escapeHtml(displayName.slice(0, 1).toUpperCase())}</span>
                            <span><strong>${escapeHtml(displayName)}</strong><small>${escapeHtml(email)}</small></span>
                        </div>
                    </td>
                    <td>${escapeHtml(formatDateTime(user.last_active))}</td>
                    <td><span class="admin-status-pill ${user.activated ? 'is-positive' : 'is-neutral'}">${user.activated ? 'Yes' : 'No'}</span></td>
                    <td>${formatNumber.format(user.active_day_count || 0)}</td>
                    <td>${formatNumber.format(user.saved_meeting_count || 0)}</td>
                    <td>${formatNumber.format(user.document_count || 0)}</td>
                    <td>${formatNumber.format(user.action_count || 0)}</td>
                    <td>
                        <select class="admin-feature-access-select" data-live-assistance-user="${escapeHtml(user.user_id || email)}" data-previous-value="${user.live_interview_assistance_override == null ? 'inherit' : (user.live_interview_assistance_override ? 'enabled' : 'disabled')}" aria-label="Live assistance access for ${escapeHtml(displayName)}">
                            <option value="inherit" ${user.live_interview_assistance_override == null ? 'selected' : ''}>Inherit (${user.live_interview_assistance_enabled ? 'enabled' : 'disabled'})</option>
                            <option value="enabled" ${user.live_interview_assistance_override === true ? 'selected' : ''}>Enabled</option>
                            <option value="disabled" ${user.live_interview_assistance_override === false ? 'selected' : ''}>Disabled</option>
                        </select>
                        <small class="admin-feature-access-reason">${escapeHtml(String(user.live_interview_assistance_reason || '').replaceAll('_', ' '))}</small>
                    </td>
                    <td><button class="admin-user-details-button" type="button" data-user-usage-id="${escapeHtml(user.user_id || email)}">View details</button></td>
                </tr>`;
        }).join('');
        emptyUsers.hidden = filtered.length > 0;
    };

    const svgElement = (name, attributes = {}) => {
        const element = document.createElementNS('http://www.w3.org/2000/svg', name);
        Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
        return element;
    };

    const renderChart = (daily) => {
        chart.replaceChildren();
        const title = svgElement('title', { id: 'admin-chart-title' });
        title.textContent = 'Daily guest and registered-user activity';
        const description = svgElement('desc', { id: 'admin-chart-description' });
        description.textContent = 'A line chart comparing unique guest browsers and active registered users by day.';
        chart.append(title, description);
        const points = Array.isArray(daily) ? daily : [];
        const hasActivity = points.some((point) => (point.unique_guests || 0) + (point.active_registered_users || 0) > 0);
        chart.hidden = !hasActivity;
        chartEmpty.hidden = hasActivity;
        if (!hasActivity) return;

        const width = 900;
        const height = 310;
        const margin = { left: 46, right: 20, top: 20, bottom: 48 };
        const plotWidth = width - margin.left - margin.right;
        const plotHeight = height - margin.top - margin.bottom;
        const maxValue = Math.max(1, ...points.flatMap((point) => [point.unique_guests || 0, point.active_registered_users || 0]));
        const xFor = (index) => margin.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth);
        const yFor = (value) => margin.top + plotHeight - (Number(value || 0) / maxValue) * plotHeight;

        for (let step = 0; step <= 4; step += 1) {
            const value = Math.round((maxValue * step) / 4);
            const y = yFor(value);
            chart.append(svgElement('line', { x1: margin.left, x2: width - margin.right, y1: y, y2: y, class: 'admin-chart-gridline' }));
            const label = svgElement('text', { x: margin.left - 10, y: y + 4, class: 'admin-chart-axis-label', 'text-anchor': 'end' });
            label.textContent = value;
            chart.append(label);
        }

        const visibleLabels = Math.min(points.length, 7);
        const labelIndexes = new Set(Array.from({ length: visibleLabels }, (_, index) => Math.round(index * (points.length - 1) / Math.max(1, visibleLabels - 1))));
        labelIndexes.forEach((index) => {
            const date = new Date(`${points[index].date}T00:00:00`);
            const label = svgElement('text', { x: xFor(index), y: height - 16, class: 'admin-chart-axis-label', 'text-anchor': 'middle' });
            label.textContent = new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, { month: 'short', day: 'numeric' }).format(date);
            chart.append(label);
        });

        const drawSeries = (field, className) => {
            const polyline = svgElement('polyline', {
                points: points.map((point, index) => `${xFor(index)},${yFor(point[field])}`).join(' '),
                class: className,
                fill: 'none',
            });
            chart.append(polyline);
            points.forEach((point, index) => {
                const circle = svgElement('circle', {
                    cx: xFor(index),
                    cy: yFor(point[field]),
                    r: 3.5,
                    class: `${className}-point`,
                });
                const pointTitle = svgElement('title');
                pointTitle.textContent = `${point.date}: ${point[field] || 0}`;
                circle.append(pointTitle);
                chart.append(circle);
            });
        };
        drawSeries('unique_guests', 'admin-chart-guests');
        drawSeries('active_registered_users', 'admin-chart-users');
    };

    const renderSupportSummary = () => {
        const newCount = Number(supportSummary.new) || 0;
        const total = Number(supportSummary.total) || 0;
        newSupportCount.textContent = formatNumber.format(newCount);
        supportCountNote.textContent = `${formatNumber.format(total)} total message${total === 1 ? '' : 's'}`;
        supportNewBadge.hidden = newCount === 0;
        supportNewBadge.textContent = `${formatNumber.format(newCount)} new`;
        if (supportTabBadge) {
            supportTabBadge.hidden = newCount === 0;
            supportTabBadge.textContent = formatNumber.format(newCount);
            supportTabBadge.setAttribute('aria-label', `${formatNumber.format(newCount)} new support messages`);
        }
    };

    const recomputeSupportSummary = () => {
        supportSummary = {
            total: supportRequests.length,
            new: supportRequests.filter((item) => item.status === 'new').length,
            read: supportRequests.filter((item) => item.status === 'read').length,
            resolved: supportRequests.filter((item) => item.status === 'resolved').length,
        };
        renderSupportSummary();
    };

    const renderSupportList = () => {
        const filter = supportFilter.value || 'all';
        const filtered = supportRequests.filter((item) => filter === 'all' || item.status === filter);
        supportList.innerHTML = filtered.map((item) => `
            <button class="admin-support-list-item ${item.status === 'new' ? 'is-new' : ''} ${item.request_id === selectedSupportRequestId ? 'is-selected' : ''}"
                    type="button" data-support-request-id="${escapeHtml(item.request_id)}">
                <span class="admin-support-list-topline">
                    <span class="admin-support-sender">${escapeHtml(item.name || item.email || 'Unknown sender')}</span>
                    <span class="admin-support-date">${escapeHtml(formatIsoDateTime(item.created_at))}</span>
                </span>
                <strong>${escapeHtml(item.subject || 'No subject')}</strong>
                <span class="admin-support-list-meta">
                    <span class="admin-support-status-badge is-${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
                    <span>${escapeHtml(item.topic_label || 'Other')}</span>
                    ${item.has_attachment ? '<span aria-label="Has attachment">📎</span>' : ''}
                </span>
            </button>
        `).join('');
        supportEmpty.hidden = filtered.length > 0;
        supportList.hidden = filtered.length === 0;
    };

    const renderSupportDetail = (item) => {
        selectedSupportRequestId = item.request_id;
        supportDetailPlaceholder.hidden = true;
        supportDetailContent.hidden = false;
        const safePageUrl = /^https?:\/\//i.test(item.page_url || '') ? item.page_url : '';
        const attachment = item.attachment || null;
        const messageHtml = escapeHtml(item.message || '').replaceAll('\n', '<br>');
        supportDetailContent.innerHTML = `
            <div class="admin-support-detail-header">
                <div>
                    <span class="admin-support-status-badge is-${escapeHtml(item.status)}">${escapeHtml(statusLabel(item.status))}</span>
                    <h3>${escapeHtml(item.subject || 'No subject')}</h3>
                    <p>Received ${escapeHtml(formatIsoDateTime(item.created_at))}</p>
                </div>
                <label class="admin-support-status-select">
                    <span>Status</span>
                    <select id="admin-support-detail-status">
                        <option value="new" ${item.status === 'new' ? 'selected' : ''}>New</option>
                        <option value="read" ${item.status === 'read' ? 'selected' : ''}>Read</option>
                        <option value="resolved" ${item.status === 'resolved' ? 'selected' : ''}>Resolved</option>
                    </select>
                </label>
            </div>
            <dl class="admin-support-contact-grid">
                <div><dt>From</dt><dd>${escapeHtml(item.name || 'Unknown')}</dd></div>
                <div><dt>Email</dt><dd><a href="mailto:${encodeURIComponent(item.email || '')}">${escapeHtml(item.email || 'Not provided')}</a></dd></div>
                <div><dt>Request type</dt><dd>${escapeHtml(item.topic_label || 'Other')}</dd></div>
                <div><dt>Feature or area</dt><dd>${escapeHtml(item.area_label || 'Other')}</dd></div>
                ${item.user_id ? `<div><dt>Registered user</dt><dd>${escapeHtml(item.user_id)}</dd></div>` : ''}
                ${safePageUrl ? `<div><dt>Submitted from</dt><dd><a href="${escapeHtml(safePageUrl)}" target="_blank" rel="noopener noreferrer">Open page</a></dd></div>` : ''}
            </dl>
            <div class="admin-support-message">
                <span>Message</span>
                <div>${messageHtml || '<em>No message text was stored.</em>'}</div>
            </div>
            ${attachment ? `
                <div class="admin-support-attachment">
                    <div><span aria-hidden="true">📎</span><span><strong>${escapeHtml(attachment.filename)}</strong><small>${escapeHtml(formatFileSize(attachment.size_bytes))}</small></span></div>
                    <a class="admin-secondary-button" href="${root}/api/admin/support-requests/${encodeURIComponent(item.request_id)}/attachment" target="_blank" rel="noopener">Open attachment</a>
                </div>` : ''}
            <footer class="admin-support-reference">Reference: ${escapeHtml(item.request_id)}</footer>
        `;
        document.getElementById('admin-support-detail-status')?.addEventListener('change', (event) => {
            updateSupportStatus(item.request_id, event.target.value);
        });
        renderSupportList();
    };

    const showSupportError = (message) => {
        supportStatus.hidden = false;
        supportStatus.classList.add('is-error');
        supportStatus.textContent = message;
    };

    const loadSupportInbox = async ({ silent = false } = {}) => {
        if (!silent) {
            supportRefreshButton.disabled = true;
            supportRefreshButton.textContent = 'Refreshing…';
            supportStatus.hidden = false;
            supportStatus.classList.remove('is-error');
            supportStatus.textContent = 'Loading support messages…';
        }
        try {
            const response = await fetch(`${root}/api/admin/support-requests`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            supportRequests = Array.isArray(data.requests) ? data.requests : [];
            supportSummary = data.summary || { total: 0, new: 0, read: 0, resolved: 0 };
            if (selectedSupportRequestId && !supportRequests.some((item) => item.request_id === selectedSupportRequestId)) {
                selectedSupportRequestId = null;
                supportDetailContent.hidden = true;
                supportDetailPlaceholder.hidden = false;
            }
            renderSupportSummary();
            renderSupportList();
            supportStatus.classList.remove('is-error');
            supportStatus.textContent = `Inbox updated ${new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, { timeStyle: 'short' }).format(new Date())}.`;
            window.setTimeout(() => { supportStatus.hidden = true; }, 2200);
        } catch (error) {
            showSupportError('Support messages could not be loaded. Check the application configuration and access permissions.');
        } finally {
            supportRefreshButton.disabled = false;
            supportRefreshButton.textContent = 'Refresh inbox';
        }
    };

    const openSupportRequest = async (requestId) => {
        selectedSupportRequestId = requestId;
        renderSupportList();
        supportDetailPlaceholder.hidden = false;
        supportDetailPlaceholder.innerHTML = '<strong>Loading message…</strong><p>Retrieving the full support request.</p>';
        supportDetailContent.hidden = true;
        try {
            const response = await fetch(`${root}/api/admin/support-requests/${encodeURIComponent(requestId)}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            const item = data.request;
            supportRequests = supportRequests.map((requestItem) => (
                requestItem.request_id === item.request_id
                    ? { ...requestItem, ...item }
                    : requestItem
            ));
            recomputeSupportSummary();
            renderSupportDetail(item);
        } catch (error) {
            supportDetailPlaceholder.hidden = false;
            supportDetailPlaceholder.innerHTML = '<strong>Message unavailable</strong><p>This support request could not be loaded.</p>';
            showSupportError('The selected support message could not be loaded.');
        }
    };

    const updateSupportStatus = async (requestId, newStatus) => {
        const statusSelect = document.getElementById('admin-support-detail-status');
        if (statusSelect) statusSelect.disabled = true;
        try {
            const response = await fetch(`${root}/api/admin/support-requests/${encodeURIComponent(requestId)}`, {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: {
                    Accept: 'application/json',
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ status: newStatus }),
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            const item = data.request;
            supportRequests = supportRequests.map((requestItem) => (
                requestItem.request_id === item.request_id
                    ? { ...requestItem, ...item }
                    : requestItem
            ));
            recomputeSupportSummary();
            renderSupportDetail(item);
            supportStatus.hidden = false;
            supportStatus.classList.remove('is-error');
            supportStatus.textContent = `Message marked ${statusLabel(item.status).toLowerCase()}.`;
            window.setTimeout(() => { supportStatus.hidden = true; }, 2200);
        } catch (error) {
            showSupportError('The message status could not be updated.');
            if (statusSelect) statusSelect.disabled = false;
        }
    };

    const incidentMetaChip = (label, value) => {
        if (value == null || String(value).trim() === '') return '';
        return `<span>${escapeHtml(label)}: ${escapeHtml(value)}</span>`;
    };

    const incidentStatusLabel = (value) => {
        const normalized = String(value || 'open').toLowerCase();
        return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    };

    const renderIncidentSupportReport = (report) => `
        <details class="admin-failure-support-report">
            <summary>${escapeHtml(report.subject || report.request_id || 'Automated error report')} · ${escapeHtml(formatIsoDateTime(report.created_at))}</summary>
            <div class="admin-failure-support-report-body">
                <pre>${escapeHtml(report.message || 'No diagnostic message is available.')}</pre>
                ${report.request_id ? `<button class="admin-failure-open-support" type="button" data-open-support-request="${escapeHtml(report.request_id)}">Open in Support inbox</button>` : ''}
            </div>
        </details>`;

    const renderIncident = (incident) => {
        const httpStatus = [incident.http_status, incident.status_text].filter(Boolean).join(' ');
        const duration = Number(incident.duration_ms || 0);
        const metadata = [
            incidentMetaChip('Stage', incident.stage),
            incidentMetaChip('HTTP', httpStatus),
            incidentMetaChip('Reference', incident.reference_id),
            incidentMetaChip('Source', incident.source),
            incidentMetaChip('Model', incident.model),
            incidentMetaChip('Duration', duration > 0 ? formatMilliseconds(duration) : ''),
        ].filter(Boolean).join('');
        const reports = Array.isArray(incident.support_reports) ? incident.support_reports : [];
        const status = String(incident.status || 'open').toLowerCase();
        return `
            <details class="admin-incident-item">
                <summary>
                    <span class="admin-incident-title">
                        <strong>${escapeHtml(incident.error_type || incident.label || 'Recorded failure')}</strong>
                        <small>${escapeHtml(incident.feature || 'Other')} · ${escapeHtml(formatIsoDateTime(incident.occurred_at))}</small>
                    </span>
                    <span class="admin-incident-status is-${escapeHtml(status)}">${escapeHtml(incidentStatusLabel(status))}</span>
                </summary>
                <div class="admin-incident-body">
                    <div class="admin-incident-diagnostic-grid">
                        <section>
                            <h4>Error message</h4>
                            ${incident.error_summary
                                ? `<p>${escapeHtml(incident.error_summary)}</p>`
                                : '<p class="admin-failure-missing">No detailed error message was recorded for this event.</p>'}
                        </section>
                        <section>
                            <h4>Likely cause</h4>
                            <p>${escapeHtml(incident.cause || 'The available telemetry does not identify a confirmed cause.')}</p>
                        </section>
                    </div>
                    ${metadata ? `<div class="admin-failure-meta">${metadata}</div>` : ''}
                    <section class="admin-failure-section admin-incident-support-section">
                        <header><h3>Related automated support reports</h3><p>Reports are matched using the reference ID or a nearby submission time.</p></header>
                        <div class="admin-failure-event-list">${reports.length ? reports.map(renderIncidentSupportReport).join('') : '<div class="admin-incident-no-report">No related automated support report was found.</div>'}</div>
                    </section>
                </div>
            </details>`;
    };

    const incidentTimestamp = (value) => {
        const parsed = new Date(value || '');
        return Number.isNaN(parsed.getTime()) ? 0 : parsed.getTime();
    };

    const filteredIncidents = () => {
        const days = incidentDateFilter?.value || 'all';
        const cutoff = days === 'all' ? 0 : Date.now() - Number(days) * 86400000;
        const dateFiltered = incidents.filter((incident) => !cutoff || incidentTimestamp(incident.occurred_at) >= cutoff);
        const periodCounts = new Map();
        dateFiltered.forEach((incident) => {
            const userId = String(incident.user_id || incident.email || '');
            periodCounts.set(userId, (periodCounts.get(userId) || 0) + 1);
        });
        const query = String(incidentUserFilter?.value || '').trim().toLowerCase();
        const feature = incidentFeatureFilter?.value || 'all';
        const statusValue = incidentStatusFilter?.value || 'all';
        const errorType = incidentTypeFilter?.value || 'all';
        const repeatedOnly = Boolean(incidentRepeatedFilter?.checked);
        return dateFiltered.filter((incident) => {
            const userId = String(incident.user_id || incident.email || '');
            const identity = `${incident.full_name || ''} ${incident.email || ''} ${userId}`.toLowerCase();
            if (query && !identity.includes(query)) return false;
            if (feature !== 'all' && incident.feature !== feature) return false;
            if (statusValue !== 'all' && incident.status !== statusValue) return false;
            if (errorType !== 'all' && incident.error_type !== errorType) return false;
            if (repeatedOnly && (periodCounts.get(userId) || 0) < 3) return false;
            return true;
        });
    };

    const renderIncidents = () => {
        if (!incidentList || !incidentEmpty) return;
        const visible = filteredIncidents();
        document.getElementById('admin-incident-visible').textContent = formatNumber.format(visible.length);
        incidentEmpty.hidden = visible.length !== 0;
        incidentList.hidden = visible.length === 0;
        if (!visible.length) {
            incidentList.replaceChildren();
            return;
        }

        const grouped = new Map();
        visible.forEach((incident) => {
            const key = String(incident.user_id || incident.email || 'unknown');
            if (!grouped.has(key)) grouped.set(key, []);
            grouped.get(key).push(incident);
        });
        const groups = Array.from(grouped.entries()).sort((left, right) => {
            const latestDifference = incidentTimestamp(right[1][0]?.occurred_at) - incidentTimestamp(left[1][0]?.occurred_at);
            return latestDifference || right[1].length - left[1].length;
        });
        incidentList.innerHTML = groups.map(([userId, items], index) => {
            const first = items[0] || {};
            const displayName = first.full_name || first.email || userId || 'Unknown user';
            const email = first.email || userId;
            return `
                <details class="admin-failure-user admin-incident-user" ${index === 0 ? 'open' : ''}>
                    <summary>
                        <span class="admin-failure-user-identity"><strong>${escapeHtml(displayName)}</strong><small>${escapeHtml(email)}</small></span>
                        <span class="admin-failure-count">${formatNumber.format(items.length)} incident${items.length === 1 ? '' : 's'}</span>
                    </summary>
                    <div class="admin-failure-user-body admin-incident-user-body">${items.map(renderIncident).join('')}</div>
                </details>`;
        }).join('');
    };

    const replaceIncidentFilterOptions = (select, values, allLabel) => {
        if (!select) return;
        const selected = select.value;
        select.innerHTML = `<option value="all">${escapeHtml(allLabel)}</option>${(values || []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(incidentStatusLabel(value))}</option>`).join('')}`;
        if (Array.from(select.options).some((option) => option.value === selected)) select.value = selected;
    };

    const loadIncidents = async ({silent = false} = {}) => {
        if (!incidentsStatus || !incidentList) return;
        if (!silent) {
            incidentsStatus.hidden = false;
            incidentsStatus.classList.remove('is-error');
            incidentsStatus.textContent = 'Loading incidents…';
        }
        try {
            const response = await fetch(`${root}/api/admin/analytics/incidents`, {
                credentials: 'same-origin',
                headers: {Accept: 'application/json'},
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            incidents = Array.isArray(data.incidents) ? data.incidents : [];
            incidentsLoaded = true;
            incidentSourcesAvailable = {
                events: data.events_available !== false,
                support: data.support_reports_available !== false,
            };
            document.getElementById('admin-incident-total').textContent = formatNumber.format(data.incident_count || incidents.length);
            document.getElementById('admin-incident-users').textContent = formatNumber.format(data.affected_user_count || 0);
            document.getElementById('admin-incident-repeated-users').textContent = formatNumber.format(data.repeated_user_count || 0);
            if (incidentsTabBadge) {
                incidentsTabBadge.hidden = incidents.length === 0;
                incidentsTabBadge.textContent = formatNumber.format(incidents.length);
            }
            replaceIncidentFilterOptions(incidentFeatureFilter, data.filters?.features, 'All features');
            replaceIncidentFilterOptions(incidentStatusFilter, data.filters?.statuses, 'All statuses');
            replaceIncidentFilterOptions(incidentTypeFilter, data.filters?.error_types, 'All error types');
            renderIncidents();
            const unavailable = [];
            if (!incidentSourcesAvailable.events) unavailable.push('failure events');
            if (!incidentSourcesAvailable.support) unavailable.push('support reports');
            incidentsStatus.hidden = unavailable.length === 0;
            incidentsStatus.textContent = unavailable.length ? `Some incident details are unavailable: ${unavailable.join(', ')}.` : '';
        } catch (error) {
            incidentsLoaded = false;
            incidentsStatus.hidden = false;
            incidentsStatus.classList.add('is-error');
            incidentsStatus.textContent = 'Incidents could not be loaded. Check the application configuration and access permissions.';
        }
    };

    const openIncidents = ({repeatedOnly = false, days = null} = {}) => {
        activateTab('operations', {focus: true});
        setOperationsView('incidents', {focus: true});
        if (days && incidentDateFilter && Array.from(incidentDateFilter.options).some((option) => option.value === String(days))) {
            incidentDateFilter.value = String(days);
        }
        if (incidentRepeatedFilter) incidentRepeatedFilter.checked = repeatedOnly;
        if (incidentsLoaded) renderIncidents();
        else loadIncidents();
        incidentsCard?.scrollIntoView({behavior: 'smooth', block: 'start'});
    };

    const closeUsageModal = () => {
        if (!usageModal || usageModal.hidden) return;
        usageModal.hidden = true;
        document.body.classList.remove('admin-modal-open');
        if (lastUsageTrigger) lastUsageTrigger.focus();
        lastUsageTrigger = null;
    };

    const renderUsageItems = (items, kind) => {
        if (!Array.isArray(items) || items.length === 0) {
            return `<div class="admin-usage-list-empty">No ${kind === 'documents' ? 'documents' : 'saved meetings'} for this user.</div>`;
        }
        if (kind === 'documents') {
            return items.map((item) => `
                <div class="admin-usage-list-item">
                    <span>
                        <strong>${escapeHtml(item.filename || 'Document')}</strong>
                        <small>${escapeHtml(item.collection_name || 'Uncategorized')}${item.created_at ? ` · ${escapeHtml(formatIsoDateTime(item.created_at))}` : ''}</small>
                    </span>
                    <strong>${escapeHtml(formatFileSize(item.size_bytes || 0))}</strong>
                </div>`).join('');
        }
        return items.map((item) => `
            <div class="admin-usage-list-item">
                <span>
                    <strong>${escapeHtml(item.title || 'Unnamed Meeting')}</strong>
                    <small>${escapeHtml(formatIsoDateTime(item.timestamp))}</small>
                </span>
            </div>`).join('');
    };

    const openUserUsage = async (userId, trigger) => {
        const user = users.find((item) => String(item.user_id || item.email || '') === String(userId));
        const displayName = user?.full_name || user?.email || userId || 'Registered user';
        const email = user?.email || userId || '';
        lastUsageTrigger = trigger || document.activeElement;
        usageTitle.textContent = displayName;
        usageEmail.textContent = email;
        usageDialogStatus.hidden = false;
        usageDialogStatus.classList.remove('is-error');
        usageDialogStatus.textContent = 'Loading usage details…';
        usageDialogContent.hidden = true;
        usageModal.hidden = false;
        document.body.classList.add('admin-modal-open');
        usageModal.querySelector('.admin-usage-close')?.focus();

        try {
            const response = await fetch(`${root}/api/admin/analytics/users/${encodeURIComponent(userId)}/usage`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            const usage = data.usage || {};
            const summary = usage.summary || {};
            document.getElementById('admin-detail-documents').textContent = formatNumber.format(summary.document_count || 0);
            document.getElementById('admin-detail-storage').textContent = formatFileSize(summary.document_total_bytes || 0);
            document.getElementById('admin-detail-meetings').textContent = formatNumber.format(summary.saved_meeting_count || 0);
            document.getElementById('admin-detail-recording-average').textContent = formatRecordingDuration(summary.average_recording_duration_seconds);
            document.getElementById('admin-detail-recording-maximum').textContent = formatRecordingDuration(summary.maximum_recording_duration_seconds);
            document.getElementById('admin-detail-recording-minimum').textContent = formatRecordingDuration(summary.minimum_recording_duration_seconds);
            document.getElementById('admin-detail-answers').textContent = formatNumber.format(summary.live_qa_answer_count || 0);
            document.getElementById('admin-detail-desktop-downloads').textContent = formatNumber.format(summary.desktop_download_count || 0);
            document.getElementById('admin-detail-desktop-uses').textContent = formatNumber.format(summary.desktop_use_count || 0);
            document.getElementById('admin-detail-active-days').textContent = formatNumber.format(user?.active_day_count || 0);
            document.getElementById('admin-detail-returned').textContent = user?.returned_within_7_days ? 'Yes' : 'No';
            document.getElementById('admin-detail-sessions').textContent = formatNumber.format(user?.session_count || 0);
            document.getElementById('admin-detail-today-time').textContent = formatDuration(user?.today_active_seconds || 0);
            document.getElementById('admin-detail-period-time').textContent = formatDuration(user?.period_active_seconds || 0);
            document.getElementById('admin-detail-lifetime-time').textContent = formatDuration(user?.lifetime_active_seconds || 0);
            document.getElementById('admin-detail-completed-actions').textContent = formatNumber.format(user?.completed_action_count || 0);
            document.getElementById('admin-detail-overdue-actions').textContent = formatNumber.format(user?.overdue_action_count || 0);
            document.getElementById('admin-detail-failures').textContent = formatNumber.format(user?.failure_count || 0);
            document.getElementById('admin-detail-ai-cost').textContent = formatMoney(
                user?.estimated_ai_cost_usd || 0,
                {
                    requestCount: user?.ai_request_count,
                    unpricedRequests: user?.ai_unpriced_request_count,
                },
            );
            detailDocumentList.innerHTML = renderUsageItems(usage.documents, 'documents');
            detailMeetingList.innerHTML = renderUsageItems(usage.meetings, 'meetings');
            liveQaTrackingNote.textContent = usage.live_qa_tracking_note || '';
            desktopTrackingNote.textContent = usage.desktop_tracking_note || '';
            const recordingSampleCount = Number(summary.recording_duration_sample_count || 0);
            recordingDurationTrackingNote.textContent = recordingSampleCount
                ? `Recording lengths are based on ${formatNumber.format(recordingSampleCount)} completed browser recording${recordingSampleCount === 1 ? '' : 's'} with usable timing data.`
                : 'No completed browser recordings with usable timing data are available for this user.';

            const unavailable = Object.entries(usage.sources || {})
                .filter(([, available]) => !available)
                .map(([source]) => ({
                    documents: 'documents',
                    meetings: 'saved meetings',
                    live_qa_answers: 'Live Q&A totals',
                    desktop_downloads: 'desktop downloads',
                    desktop_uses: 'desktop client uses',
                    recording_durations: 'recording-length statistics',
                }[source] || source));
            usageDialogStatus.hidden = unavailable.length === 0;
            usageDialogStatus.textContent = unavailable.length
                ? `Some details are unavailable: ${unavailable.join(', ')}.`
                : '';
            usageDialogContent.hidden = false;
        } catch (error) {
            usageDialogStatus.hidden = false;
            usageDialogStatus.classList.add('is-error');
            usageDialogStatus.textContent = 'Usage details could not be loaded. Check the application configuration and access permissions.';
        }
    };

    const loadAnalytics = async () => {
        status.hidden = false;
        status.classList.remove('is-error');
        status.textContent = 'Loading admin analytics…';
        const days = periodSelect.value;
        exportLink.href = `${root}/api/admin/analytics/users.csv?days=${encodeURIComponent(days)}`;

        try {
            const response = await fetch(`${root}/api/admin/analytics?days=${encodeURIComponent(days)}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error(`Request failed (${response.status})`);
            const data = await response.json();
            users = Array.isArray(data.users) ? data.users : [];
            renderSummary(data);
            renderAdvancedMetrics(data);
            renderChart(data.daily);
            renderUsers();
            const generated = new Date(data.generated_at);
            status.textContent = `Last updated ${new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(generated)}`;
        } catch (error) {
            status.hidden = false;
            status.classList.add('is-error');
            status.textContent = 'Admin analytics could not be loaded. Check the application configuration and access permissions.';
        }
    };

    const loadAll = async () => {
        setLoading(true);
        await Promise.allSettled([loadAnalytics(), loadSupportInbox(), loadIncidents()]);
        setLoading(false);
    };

    periodSelect.addEventListener('change', loadAnalytics);
    refreshButton.addEventListener('click', loadAll);
    searchInput.addEventListener('input', renderUsers);
    activationFilter?.addEventListener('change', renderUsers);
    activityFilter?.addEventListener('change', renderUsers);
    guestGeographyTrigger?.addEventListener('click', openGuestGeography);
    document.querySelectorAll('[data-admin-guest-geo-close]').forEach((button) => {
        button.addEventListener('click', closeGuestGeography);
    });
    alertList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-admin-alert-action="view_incidents"]');
        if (!button) return;
        openIncidents({repeatedOnly: true, days: periodSelect.value});
    });
    incidentList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-open-support-request]');
        if (!button) return;
        activateTab('support', {focus: true});
        openSupportRequest(button.dataset.openSupportRequest);
    });
    [incidentUserFilter, incidentFeatureFilter, incidentDateFilter, incidentStatusFilter, incidentTypeFilter, incidentRepeatedFilter].forEach((control) => {
        control?.addEventListener(control === incidentUserFilter ? 'input' : 'change', renderIncidents);
    });
    incidentsRefreshButton?.addEventListener('click', () => loadIncidents());
    tableBody.addEventListener('click', (event) => {
        const button = event.target.closest('[data-user-usage-id]');
        if (!button) return;
        openUserUsage(button.dataset.userUsageId, button);
    });
    tableBody.addEventListener('change', async (event) => {
        const select = event.target.closest('[data-live-assistance-user]');
        if (!select) return;
        const userId = select.dataset.liveAssistanceUser;
        const previous = select.dataset.previousValue || 'inherit';
        select.disabled = true;
        try {
            const response = await fetch(`${root}/api/admin/features/live-interview-assistance/users/${encodeURIComponent(userId)}`, {
                method: 'PATCH',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({mode: select.value})
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
            const user = users.find((item) => String(item.user_id || item.email || '') === String(userId));
            if (user) {
                user.live_interview_assistance_enabled = Boolean(result.access?.enabled);
                user.live_interview_assistance_reason = result.access?.reason || '';
                user.live_interview_assistance_override = result.access?.override;
            }
            renderUsers();
        } catch (error) {
            select.value = previous;
            window.AppUI?.showToast?.(`Could not update Live Assistance access: ${error.message}`, {type: 'error'});
        } finally {
            select.disabled = false;
        }
    });
    document.querySelectorAll('[data-admin-usage-close]').forEach((button) => {
        button.addEventListener('click', closeUsageModal);
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (guestGeographyModal && !guestGeographyModal.hidden) {
            closeGuestGeography();
        } else if (usageModal && !usageModal.hidden) {
            closeUsageModal();
        }
    });
    supportFilter.addEventListener('change', renderSupportList);
    supportRefreshButton.addEventListener('click', () => loadSupportInbox());
    supportList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-support-request-id]');
        if (!button) return;
        openSupportRequest(button.dataset.supportRequestId);
    });

    window.setInterval(() => {
        if (document.visibilityState === 'visible') loadSupportInbox({ silent: true });
    }, 60000);

    loadAll();
})();
