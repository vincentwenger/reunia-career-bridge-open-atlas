'use strict';

(function () {
    const API_ACTIONS = '/api/career/actions';
    const API_CONTEXT = '/api/career/action-plan/context';
    const LEGACY_STORAGE_KEYS = [
        'reunia-career-action-plan-v2',
        'meeting-assistant-action-center-v1'
    ];
    const PRIORITY_WEIGHT = {urgent: 4, high: 3, medium: 2, low: 1, none: 0};
    const PRIORITY_LABELS = {
        urgent: 'Urgent',
        high: 'High',
        medium: 'Medium',
        low: 'Low',
        none: 'No priority'
    };
    const STATUS_LABELS = {
        not_started: 'Not started',
        in_progress: 'In progress',
        blocked: 'Blocked',
        done: 'Done'
    };
    const DEFAULT_SOURCES = [
        {value: 'resume_gap', label: 'Resume gaps'},
        {value: 'evidence_review', label: 'Evidence-review findings'},
        {value: 'interview_scorecard', label: 'Interview scorecard findings'},
        {value: 'upcoming_interview', label: 'Upcoming interviews'},
        {value: 'application_follow_up', label: 'Application follow-ups'},
        {value: 'application_next_action', label: 'Application next steps'},
        {value: 'manual', label: 'Manual actions'}
    ];

    const state = {
        actions: [],
        applications: [],
        sources: DEFAULT_SOURCES,
        backendAvailable: false,
        loading: true,
        quickView: 'open',
        requestedApplication: '',
        currentUser: '',
        openPopoverId: null,
        modalPreviousFocus: null,
        loadError: ''
    };
    const elements = {};

    document.addEventListener('DOMContentLoaded', initialize);

    async function initialize() {
        cacheElements();
        state.currentUser = (elements.app?.dataset.currentUser || '').trim();
        const params = new URLSearchParams(window.location.search);
        state.requestedApplication = params.get('application_id') || params.get('application') || '';
        bindEvents();
        await loadPlan();
    }

    function cacheElements() {
        elements.app = document.getElementById('action-center-app');
        elements.storageStatus = document.getElementById('action-storage-status');
        elements.applicationStrip = document.getElementById('action-application-strip');
        elements.tableShell = document.getElementById('action-table-shell');
        elements.tableBody = document.getElementById('action-table-body');
        elements.loadingState = document.getElementById('action-loading-state');
        elements.emptyState = document.getElementById('action-empty-state');
        elements.emptyTitle = elements.emptyState?.querySelector('[data-state-title]');
        elements.emptyMessage = elements.emptyState?.querySelector('[data-state-message]');
        elements.errorState = document.getElementById('action-error-state');
        elements.retryButton = document.getElementById('action-retry-button');
        elements.resultsSummary = document.getElementById('action-results-summary');
        elements.search = document.getElementById('action-search');
        elements.applicationFilter = document.getElementById('action-application-filter');
        elements.sourceFilter = document.getElementById('action-source-filter');
        elements.dueFilter = document.getElementById('action-due-filter');
        elements.priorityFilter = document.getElementById('action-priority-filter');
        elements.statusFilter = document.getElementById('action-status-filter');
        elements.sort = document.getElementById('action-sort');
        elements.clearFilters = document.getElementById('clear-action-filters');
        elements.addAction = document.getElementById('add-action-button');
        elements.emptyAddAction = document.getElementById('empty-add-action-button');
        elements.modal = document.getElementById('action-modal');
        elements.modalCard = elements.modal?.querySelector('.action-modal-card');
        elements.modalTitle = document.getElementById('action-modal-title');
        elements.modalClose = document.getElementById('action-modal-close');
        elements.form = document.getElementById('action-form');
        elements.formId = document.getElementById('action-form-id');
        elements.formDescription = document.getElementById('action-form-description');
        elements.formApplication = document.getElementById('action-form-application');
        elements.formDueDate = document.getElementById('action-form-due-date');
        elements.formPriority = document.getElementById('action-form-priority');
        elements.formStatus = document.getElementById('action-form-status');
        elements.formCancel = document.getElementById('action-form-cancel');
        elements.formSubmit = document.getElementById('action-form-submit');
        elements.generatedNote = document.getElementById('action-generated-note');
        elements.generatedSource = document.getElementById('action-generated-source');
        elements.generatedDetail = document.getElementById('action-generated-detail');
    }

    function bindEvents() {
        const rerender = window.AppUI?.debounce(render, 120) || render;
        elements.search?.addEventListener('input', rerender);
        [
            elements.applicationFilter,
            elements.sourceFilter,
            elements.dueFilter,
            elements.priorityFilter,
            elements.statusFilter,
            elements.sort
        ].forEach(element => element?.addEventListener('change', render));

        elements.clearFilters?.addEventListener('click', clearFilters);
        elements.addAction?.addEventListener('click', () => openActionModal());
        elements.emptyAddAction?.addEventListener('click', () => openActionModal());
        elements.retryButton?.addEventListener('click', loadPlan);
        elements.modalClose?.addEventListener('click', closeActionModal);
        elements.formCancel?.addEventListener('click', closeActionModal);
        elements.form?.addEventListener('submit', saveActionFromForm);

        document.querySelectorAll('[data-quick-view]').forEach(button => {
            button.addEventListener('click', () => {
                setQuickView(button.dataset.quickView || 'open');
                elements.statusFilter.value = 'all';
                elements.dueFilter.value = 'all';
                render();
            });
        });

        document.querySelectorAll('[data-kpi-filter]').forEach(button => {
            button.addEventListener('click', () => {
                const value = button.dataset.kpiFilter || 'all';
                setQuickView('all');
                if (value === 'overdue' || value === 'due_soon') {
                    elements.statusFilter.value = 'all';
                    elements.dueFilter.value = value;
                } else {
                    elements.statusFilter.value = value;
                    elements.dueFilter.value = 'all';
                }
                render();
                elements.tableShell?.scrollIntoView({behavior: 'smooth', block: 'start'});
            });
        });

        elements.modal?.addEventListener('click', event => {
            if (event.target === elements.modal) closeActionModal();
        });
        document.addEventListener('keydown', event => {
            if (!elements.modal?.hidden && event.key === 'Tab') {
                const focusable = [...elements.modal.querySelectorAll(
                    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
                )].filter(item => !item.hidden && item.offsetParent !== null);
                if (focusable.length) {
                    const first = focusable[0];
                    const last = focusable[focusable.length - 1];
                    if (event.shiftKey && document.activeElement === first) {
                        event.preventDefault();
                        last.focus();
                    } else if (!event.shiftKey && document.activeElement === last) {
                        event.preventDefault();
                        first.focus();
                    }
                }
            }
            if (event.key !== 'Escape') return;
            if (!elements.modal?.hidden) closeActionModal();
            closeAllPopovers();
        });
        document.addEventListener('click', event => {
            if (!event.target.closest('.action-row-actions')) closeAllPopovers();
        });
    }

    async function loadPlan() {
        state.loading = true;
        state.loadError = '';
        updateLoadingState();

        const [contextResult, actionsResult] = await Promise.allSettled([
            fetchJson(API_CONTEXT),
            fetchJson(API_ACTIONS)
        ]);

        const loadIssues = [];
        if (contextResult.status === 'fulfilled') {
            const context = contextResult.value || {};
            state.applications = ensureArray(context.applications).map(normalizeApplication);
            state.sources = ensureArray(context.sources).length ? context.sources : DEFAULT_SOURCES;
        } else {
            console.error('Unable to load Career Action Plan context:', contextResult.reason);
            state.applications = [];
            state.sources = DEFAULT_SOURCES;
            loadIssues.push('Job application context is unavailable.');
        }

        if (actionsResult.status === 'fulfilled') {
            state.actions = ensureArray(actionsResult.value).map(normalizeAction);
            state.backendAvailable = true;
            elements.storageStatus.textContent =
                'Automatically generated actions stay synchronized with each job application and its latest findings.';
            await migrateLegacyBrowserActions();
        } else {
            console.error('Unable to load Career Action Plan actions:', actionsResult.reason);
            state.backendAvailable = false;
            state.actions = [];
            elements.storageStatus.textContent =
                'The action service is unavailable. Changes are disabled until the connection is restored.';
            loadIssues.push('Career actions could not be loaded. Retry before adding or changing actions.');
        }
        state.loadError = loadIssues.join(' ');

        state.loading = false;
        updateMutationAvailability();
        populateOptions();
        applyRequestedApplication();
        updateLoadingState();
        render();
    }

    async function fetchJson(path, options = {}) {
        const {headers = {}, ...requestOptions} = options;
        const response = await fetch(appUrl(path), {
            ...requestOptions,
            headers: {'Accept': 'application/json', ...headers}
        });
        if (!response.ok) {
            throw new Error(await readApiError(response, `Request failed with ${response.status}.`));
        }
        return response.json();
    }

    function updateMutationAvailability() {
        const unavailable = !state.backendAvailable;
        [elements.addAction, elements.emptyAddAction].forEach(button => {
            if (!button) return;
            button.disabled = unavailable;
            button.setAttribute('aria-disabled', String(unavailable));
            button.title = unavailable
                ? 'Career actions cannot be changed while the action service is unavailable.'
                : '';
        });
    }

    function requireActionService() {
        if (state.backendAvailable) return;
        throw new Error('The action service is unavailable. Retry loading the Career Action Plan before making changes.');
    }

    function ensureArray(value) {
        if (Array.isArray(value)) return value;
        if (Array.isArray(value?.items)) return value.items;
        if (Array.isArray(value?.actions)) return value.actions;
        return [];
    }

    function normalizeApplication(raw) {
        const id = String(raw?.id || raw?.application_id || '').trim();
        const company = String(raw?.company || raw?.application_company || '').trim();
        const role = String(raw?.role || raw?.application_role || '').trim();
        return {
            id,
            company,
            role,
            label: String(raw?.label || raw?.application_label || applicationLabel(role, company)).trim(),
            status: String(raw?.status || raw?.application_status || '').trim(),
            status_label: String(raw?.status_label || '').trim(),
            next_action: String(raw?.next_action || '').trim(),
            next_follow_up_date: normalizeDateValue(raw?.next_follow_up_date),
            upcoming_event_date: normalizeDateValue(raw?.upcoming_event_date),
            upcoming_event_type: String(raw?.upcoming_event_type || '').trim(),
            builder_url: String(raw?.builder_url || `/applications/?tab=tailoring&application_id=${encodeURIComponent(id)}`),
            interview_preparation_url: String(raw?.interview_preparation_url || `/applications/interview-preparation?application_id=${encodeURIComponent(id)}`),
            mock_interview_url: String(raw?.mock_interview_url || `/mock-interview?application_id=${encodeURIComponent(id)}`)
        };
    }

    function normalizeAction(raw, index = 0) {
        const id = String(raw?.action_id || raw?.id || `manual-local-${Date.now()}-${index}`);
        const applicationId = String(raw?.application_id || raw?.meeting_id || '');
        const application = state.applications.find(item => item.id === applicationId);
        const source = String(raw?.source || 'manual').toLowerCase();
        const status = STATUS_LABELS[String(raw?.status || '').toLowerCase()]
            ? String(raw.status).toLowerCase()
            : 'not_started';
        const priority = PRIORITY_LABELS[String(raw?.priority || '').toLowerCase()]
            ? String(raw.priority).toLowerCase()
            : 'none';
        return {
            id,
            action_id: id,
            description: String(raw?.description || raw?.task || 'Untitled action').trim(),
            application_id: applicationId,
            application_company: String(raw?.application_company || application?.company || '').trim(),
            application_role: String(raw?.application_role || application?.role || '').trim(),
            application_label: String(raw?.application_label || raw?.meeting_name || application?.label || 'Application unavailable').trim(),
            application_status: String(raw?.application_status || application?.status || '').trim(),
            owner: String(raw?.owner || 'Me').trim() || 'Me',
            due_date: normalizeDateValue(raw?.due_date),
            priority,
            status,
            source,
            source_label: String(raw?.source_label || sourceLabel(source)).trim(),
            source_detail: String(raw?.source_detail || '').trim(),
            source_reference: String(raw?.source_reference || '').trim(),
            generated: Boolean(raw?.generated ?? source !== 'manual'),
            link_url: String(raw?.link_url || application?.builder_url || '').trim(),
            created_at: String(raw?.created_at || new Date().toISOString()),
            completed_at: raw?.completed_at || null
        };
    }

    function populateOptions() {
        const selectedApplication = elements.applicationFilter.value || 'all';
        const selectedSource = elements.sourceFilter.value || 'all';
        const applicationOptions = state.applications.map(application => ({
            value: application.id,
            label: application.label
        }));
        replaceSelectOptions(elements.applicationFilter, [
            {value: 'all', label: 'All applications'},
            ...applicationOptions
        ]);
        replaceSelectOptions(elements.formApplication, [
            {value: '', label: 'Choose an application'},
            ...applicationOptions
        ]);
        replaceSelectOptions(elements.sourceFilter, [
            {value: 'all', label: 'All sources'},
            ...state.sources.map(item => ({value: String(item.value), label: String(item.label)}))
        ]);
        if (Array.from(elements.applicationFilter.options).some(option => option.value === selectedApplication)) {
            elements.applicationFilter.value = selectedApplication;
        }
        if (Array.from(elements.sourceFilter.options).some(option => option.value === selectedSource)) {
            elements.sourceFilter.value = selectedSource;
        }
    }

    function replaceSelectOptions(select, options) {
        const fragment = document.createDocumentFragment();
        options.forEach(({value, label}) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            fragment.appendChild(option);
        });
        select.replaceChildren(fragment);
    }

    function applyRequestedApplication() {
        if (!state.requestedApplication) return;
        if (state.applications.some(application => application.id === state.requestedApplication)) {
            elements.applicationFilter.value = state.requestedApplication;
        }
    }

    function render() {
        if (state.loading) return;
        closeAllPopovers();
        const actions = getFilteredActions();
        renderApplicationStrip();
        renderLoadError();
        renderActions(actions);
        updateKpis();
        updateResultsSummary(actions);
    }

    function renderApplicationStrip() {
        if (!state.applications.length) {
            const empty = document.createElement('div');
            empty.className = 'action-application-empty';
            const copy = document.createElement('div');
            const title = document.createElement('strong');
            title.textContent = 'Create a job application first';
            const text = document.createElement('p');
            text.textContent = 'Career actions are generated only when they can be connected to a specific application.';
            copy.append(title, text);
            const link = document.createElement('a');
            link.href = '/applications/?tab=applications';
            link.textContent = 'Open Job Applications';
            empty.append(copy, link);
            elements.applicationStrip.replaceChildren(empty);
            return;
        }

        const cards = state.applications.map(application => {
            const actions = state.actions.filter(action => action.application_id === application.id);
            const open = actions.filter(action => action.status !== 'done').length;
            const urgent = actions.filter(action => action.status !== 'done' && (isOverdue(action) || action.priority === 'urgent')).length;
            const card = document.createElement('button');
            card.type = 'button';
            card.className = 'action-application-card';
            card.classList.toggle('is-selected', elements.applicationFilter.value === application.id);
            card.addEventListener('click', () => {
                elements.applicationFilter.value = application.id;
                setQuickView('open');
                render();
                elements.tableShell?.scrollIntoView({behavior: 'smooth', block: 'start'});
            });

            const heading = document.createElement('div');
            const company = document.createElement('span');
            company.textContent = application.company || 'Company not specified';
            const role = document.createElement('strong');
            role.textContent = application.role || 'Role not specified';
            heading.append(company, role);

            const counts = document.createElement('div');
            counts.className = 'action-application-counts';
            const openCount = document.createElement('span');
            openCount.innerHTML = `<strong>${open}</strong> open`;
            const urgentCount = document.createElement('span');
            urgentCount.innerHTML = `<strong>${urgent}</strong> urgent`;
            counts.append(openCount, urgentCount);

            const milestone = document.createElement('small');
            if (application.upcoming_event_type === 'interview' && application.upcoming_event_date) {
                milestone.textContent = `Interview · ${formatDisplayDate(application.upcoming_event_date)}`;
            } else if (application.next_follow_up_date) {
                milestone.textContent = `Follow-up · ${formatDisplayDate(application.next_follow_up_date)}`;
            } else {
                milestone.textContent = application.status_label || application.status.replaceAll('_', ' ') || 'Application tracked';
            }
            card.append(heading, counts, milestone);
            return card;
        });
        elements.applicationStrip.replaceChildren(...cards);
    }

    function getFilteredActions() {
        const query = elements.search.value.trim().toLowerCase();
        const application = elements.applicationFilter.value;
        const source = elements.sourceFilter.value;
        const due = elements.dueFilter.value;
        const priority = elements.priorityFilter.value;
        const status = elements.statusFilter.value;

        const filtered = state.actions.filter(action => {
            if (state.quickView === 'open' && action.status === 'done') return false;
            if (state.quickView === 'closed' && action.status !== 'done') return false;
            if (state.quickView === 'generated' && !action.generated) return false;
            if (application !== 'all' && action.application_id !== application) return false;
            if (source !== 'all' && action.source !== source) return false;
            if (priority !== 'all' && action.priority !== priority) return false;
            if (status === 'open' && action.status === 'done') return false;
            if (status !== 'all' && status !== 'open' && action.status !== status) return false;
            if (!matchesDueFilter(action, due)) return false;
            if (query) {
                const haystack = [
                    action.description,
                    action.application_company,
                    action.application_role,
                    action.application_label,
                    action.source_label,
                    action.source_detail
                ].join(' ').toLowerCase();
                if (!haystack.includes(query)) return false;
            }
            return true;
        });
        return sortActions(filtered, elements.sort.value);
    }

    function matchesDueFilter(action, due) {
        if (due === 'all') return true;
        if (due === 'overdue') return isOverdue(action);
        if (due === 'today') return isDueToday(action);
        if (due === 'due_soon') return isDueSoon(action);
        if (due === 'later') return isDueLater(action);
        if (due === 'none') return !action.due_date;
        return true;
    }

    function sortActions(actions, mode) {
        const values = [...actions];
        values.sort((left, right) => {
            if (mode === 'due_asc') return compareDueDate(left, right, false);
            if (mode === 'due_desc') return compareDueDate(left, right, true);
            if (mode === 'priority') return PRIORITY_WEIGHT[right.priority] - PRIORITY_WEIGHT[left.priority] || compareDueDate(left, right, false);
            if (mode === 'application') return left.application_label.localeCompare(right.application_label) || left.description.localeCompare(right.description);
            if (mode === 'source') return left.source_label.localeCompare(right.source_label) || left.description.localeCompare(right.description);
            if (mode === 'newest') return String(right.created_at).localeCompare(String(left.created_at));
            return attentionScore(left) - attentionScore(right)
                || compareDueDate(left, right, false)
                || PRIORITY_WEIGHT[right.priority] - PRIORITY_WEIGHT[left.priority]
                || left.application_label.localeCompare(right.application_label);
        });
        return values;
    }

    function attentionScore(action) {
        if (action.status === 'done') return 100;
        if (isOverdue(action)) return 0;
        if (action.status === 'blocked') return 1;
        if (action.priority === 'urgent') return 2;
        if (isDueToday(action)) return 3;
        if (action.priority === 'high') return 4;
        if (isDueSoon(action)) return 5;
        if (action.status === 'in_progress') return 6;
        return 10;
    }

    function compareDueDate(left, right, descending) {
        const leftValue = left.due_date || (descending ? '0000-00-00' : '9999-12-31');
        const rightValue = right.due_date || (descending ? '0000-00-00' : '9999-12-31');
        return descending ? rightValue.localeCompare(leftValue) : leftValue.localeCompare(rightValue);
    }

    function renderActions(actions) {
        elements.tableBody.replaceChildren(...actions.map(createActionRow));
        const empty = !actions.length && !state.loadError;
        elements.emptyState.hidden = !empty;
        if (empty) {
            const hasApplications = state.applications.length > 0;
            elements.emptyTitle.textContent = hasActiveFilters() ? 'No actions match these filters' : 'No open career actions';
            elements.emptyMessage.textContent = hasActiveFilters()
                ? 'Clear one or more filters to see other application actions.'
                : hasApplications
                    ? 'Your automatically generated plan is clear. Add a manual action when another step is needed.'
                    : 'Create a job application so Career Bridge can generate an application-specific plan.';
            elements.emptyAddAction.hidden = !hasApplications;
        }
    }

    function createActionRow(action) {
        const row = document.createElement('tr');
        row.dataset.actionId = action.id;
        row.classList.toggle('is-done', action.status === 'done');
        row.classList.toggle('is-overdue', isOverdue(action));

        const completionCell = document.createElement('td');
        completionCell.className = 'action-complete-column';
        const checkLabel = document.createElement('label');
        checkLabel.className = 'action-check';
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = action.status === 'done';
        check.setAttribute('aria-label', `Mark ${action.description} as ${check.checked ? 'open' : 'complete'}`);
        const checkVisual = document.createElement('span');
        checkVisual.textContent = '✓';
        check.addEventListener('change', async () => {
            const previous = action.status;
            try {
                await updateAction(action.id, {status: check.checked ? 'done' : 'not_started'}, {
                    revert: () => { check.checked = previous === 'done'; }
                });
            } catch (error) {
                // updateAction handles the visible error.
            }
        });
        checkLabel.append(check, checkVisual);
        completionCell.appendChild(checkLabel);

        const taskCell = document.createElement('td');
        taskCell.className = 'action-task-cell';
        const taskTitle = document.createElement('button');
        taskTitle.type = 'button';
        taskTitle.className = 'action-task-title action-task-title-button';
        taskTitle.textContent = action.description;
        taskTitle.addEventListener('click', () => openActionModal(action));
        const taskMeta = document.createElement('div');
        taskMeta.className = 'action-task-meta';
        const generated = document.createElement('span');
        generated.className = `action-source-chip source-${action.source}`;
        generated.textContent = action.generated ? 'Auto-generated' : 'Manual';
        taskMeta.appendChild(generated);
        if (action.source_detail) {
            const detail = document.createElement('span');
            detail.className = 'action-task-detail';
            detail.textContent = action.source_detail;
            detail.title = action.source_detail;
            taskMeta.appendChild(detail);
        }
        taskCell.append(taskTitle, taskMeta);

        const applicationCell = document.createElement('td');
        const applicationLink = document.createElement('a');
        applicationLink.className = 'action-meeting-link';
        applicationLink.href = applicationUrl(action);
        applicationLink.textContent = action.application_role || action.application_label;
        const company = document.createElement('span');
        company.className = 'action-meeting-date';
        company.textContent = action.application_company || action.application_status.replaceAll('_', ' ');
        applicationCell.append(applicationLink, company);

        const sourceCell = document.createElement('td');
        const sourceChip = document.createElement('span');
        sourceChip.className = `action-source-chip source-${action.source}`;
        sourceChip.textContent = action.source_label;
        sourceCell.appendChild(sourceChip);

        const dueCell = document.createElement('td');
        if (action.due_date) {
            const dueChip = document.createElement('span');
            dueChip.className = 'action-date-chip';
            if (isOverdue(action)) dueChip.classList.add('is-overdue');
            if (isDueSoon(action)) dueChip.classList.add('is-due-soon');
            dueChip.textContent = formatDueDate(action);
            dueCell.appendChild(dueChip);
        } else {
            const noDate = document.createElement('span');
            noDate.className = 'action-date-empty';
            noDate.textContent = 'No deadline';
            dueCell.appendChild(noDate);
        }

        const priorityCell = document.createElement('td');
        const prioritySelect = createInlineSelect(PRIORITY_LABELS, action.priority, `Priority for ${action.description}`);
        prioritySelect.addEventListener('change', async () => {
            const oldValue = action.priority;
            try {
                await updateAction(action.id, {priority: prioritySelect.value}, {
                    revert: () => { prioritySelect.value = oldValue; }
                });
            } catch (error) {
                // handled by updateAction
            }
        });
        priorityCell.appendChild(prioritySelect);

        const statusCell = document.createElement('td');
        const statusSelect = createInlineSelect(STATUS_LABELS, action.status, `Status for ${action.description}`);
        statusSelect.addEventListener('change', async () => {
            const oldValue = action.status;
            try {
                await updateAction(action.id, {status: statusSelect.value}, {
                    revert: () => { statusSelect.value = oldValue; }
                });
            } catch (error) {
                // handled by updateAction
            }
        });
        statusCell.appendChild(statusSelect);

        const menuCell = document.createElement('td');
        menuCell.className = 'action-menu-column';
        const wrapper = document.createElement('div');
        wrapper.className = 'action-row-actions';
        const menuButton = document.createElement('button');
        menuButton.type = 'button';
        menuButton.className = 'action-row-menu';
        menuButton.setAttribute('aria-label', `More options for ${action.description}`);
        menuButton.setAttribute('aria-expanded', 'false');
        menuButton.textContent = '⋮';
        menuButton.addEventListener('click', event => {
            event.stopPropagation();
            toggleRowPopover(action.id, wrapper, menuButton);
        });
        wrapper.appendChild(menuButton);
        menuCell.appendChild(wrapper);

        row.append(completionCell, taskCell, applicationCell, sourceCell, dueCell, priorityCell, statusCell, menuCell);
        return row;
    }

    function createInlineSelect(labels, selectedValue, ariaLabel) {
        const select = document.createElement('select');
        select.className = 'action-inline-select';
        select.setAttribute('aria-label', ariaLabel);
        Object.entries(labels).forEach(([value, label]) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            option.selected = value === selectedValue;
            select.appendChild(option);
        });
        return select;
    }

    function toggleRowPopover(actionId, wrapper, button) {
        const wasOpen = state.openPopoverId === actionId;
        closeAllPopovers();
        if (wasOpen) return;
        const action = state.actions.find(item => item.id === actionId);
        if (!action) return;

        const popover = document.createElement('div');
        popover.className = 'action-row-popover';
        const editButton = document.createElement('button');
        editButton.type = 'button';
        editButton.textContent = 'Edit action';
        editButton.addEventListener('click', () => {
            closeAllPopovers();
            openActionModal(action);
        });
        popover.appendChild(editButton);

        if (action.link_url) {
            const sourceButton = document.createElement('button');
            sourceButton.type = 'button';
            sourceButton.textContent = action.generated ? 'Open source finding' : 'Open application';
            sourceButton.addEventListener('click', () => {
                window.location.href = appUrl(action.link_url);
            });
            popover.appendChild(sourceButton);
        }

        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'is-danger';
        deleteButton.textContent = action.generated ? 'Dismiss action' : 'Delete action';
        deleteButton.addEventListener('click', async () => {
            closeAllPopovers();
            await deleteAction(action);
        });
        popover.appendChild(deleteButton);
        wrapper.appendChild(popover);
        button.setAttribute('aria-expanded', 'true');
        state.openPopoverId = actionId;
    }

    function closeAllPopovers() {
        document.querySelectorAll('.action-row-popover').forEach(popover => popover.remove());
        document.querySelectorAll('.action-row-menu').forEach(button => button.setAttribute('aria-expanded', 'false'));
        state.openPopoverId = null;
    }

    function renderLoadError() {
        if (!elements.errorState) return;
        if (!state.loadError) {
            elements.errorState.hidden = true;
            return;
        }
        window.AppUI?.showWorkspaceState(elements.errorState, {
            state: 'error',
            title: state.actions.length
                ? 'Some Career Action Plan data is unavailable'
                : 'Career actions could not be loaded',
            message: state.loadError
        });
    }

    function updateKpis() {
        setText('action-open-count', state.actions.filter(action => action.status !== 'done').length);
        setText('action-overdue-count', state.actions.filter(isOverdue).length);
        setText('action-due-soon-count', state.actions.filter(isDueSoon).length);
        setText('action-done-count', state.actions.filter(action => action.status === 'done').length);
    }

    function updateResultsSummary(actions) {
        const application = elements.applicationFilter.value;
        const applicationName = state.applications.find(item => item.id === application)?.label;
        const suffix = applicationName ? ` for ${applicationName}` : ' across all applications';
        elements.resultsSummary.textContent = `${actions.length} action${actions.length === 1 ? '' : 's'} shown${suffix}`;
    }

    function updateLoadingState() {
        elements.loadingState.hidden = !state.loading;
        elements.tableShell?.setAttribute('aria-busy', String(state.loading));
        if (state.loading) {
            elements.emptyState.hidden = true;
            if (elements.errorState) elements.errorState.hidden = true;
            elements.tableBody.replaceChildren();
        }
    }

    function clearFilters() {
        setQuickView('open');
        elements.search.value = '';
        elements.applicationFilter.value = 'all';
        elements.sourceFilter.value = 'all';
        elements.dueFilter.value = 'all';
        elements.priorityFilter.value = 'all';
        elements.statusFilter.value = 'all';
        elements.sort.value = 'attention';
        render();
    }

    function setQuickView(value) {
        state.quickView = value;
        document.querySelectorAll('[data-quick-view]').forEach(button => {
            button.classList.toggle('is-active', button.dataset.quickView === value);
        });
    }

    function hasActiveFilters() {
        return Boolean(
            elements.search.value.trim()
            || elements.applicationFilter.value !== 'all'
            || elements.sourceFilter.value !== 'all'
            || elements.dueFilter.value !== 'all'
            || elements.priorityFilter.value !== 'all'
            || elements.statusFilter.value !== 'all'
            || state.quickView !== 'open'
        );
    }

    function openActionModal(action = null) {
        if (!state.backendAvailable) {
            window.AppUI?.showToast(
                'The action service is unavailable. Retry loading the Career Action Plan before making changes.',
                {type: 'error'}
            );
            return;
        }
        if (!state.applications.length) {
            window.AppUI?.showToast('Create a job application before adding a career action.', {type: 'warning'});
            return;
        }
        state.modalPreviousFocus = document.activeElement;
        elements.form.reset();
        elements.formId.value = action?.id || '';
        elements.formDescription.value = action?.description || '';
        elements.formDueDate.value = action?.due_date || '';
        elements.formPriority.value = action?.priority || 'medium';
        elements.formStatus.value = action?.status || 'not_started';
        elements.modalTitle.textContent = action ? 'Edit action' : 'Add action';
        elements.formSubmit.textContent = action ? 'Save changes' : 'Add action';

        const filterApplication = elements.applicationFilter.value !== 'all'
            ? elements.applicationFilter.value
            : '';
        const applicationId = action?.application_id || filterApplication || state.requestedApplication || state.applications[0]?.id || '';
        elements.formApplication.value = state.applications.some(item => item.id === applicationId) ? applicationId : '';

        const generated = Boolean(action?.generated);
        elements.generatedNote.hidden = !generated;
        elements.generatedSource.textContent = action?.source_label || 'Automatically generated';
        elements.generatedDetail.textContent = action?.source_detail || 'This action was generated from Career Bridge findings.';

        elements.modal.hidden = false;
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(() => {
            elements.modalCard?.focus({preventScroll: true});
            elements.formDescription.focus({preventScroll: true});
        });
    }

    function closeActionModal() {
        if (elements.modal.hidden) return;
        elements.modal.hidden = true;
        document.body.style.overflow = '';
        state.modalPreviousFocus?.focus?.();
        state.modalPreviousFocus = null;
    }

    async function saveActionFromForm(event) {
        event.preventDefault();
        const applicationId = elements.formApplication.value;
        const description = elements.formDescription.value.trim();
        if (!applicationId) {
            elements.formApplication.focus();
            window.AppUI?.showToast('Choose the job application this action belongs to.', {type: 'warning'});
            return;
        }
        if (!description) {
            elements.formDescription.focus();
            return;
        }

        const actionId = elements.formId.value;
        const existing = state.actions.find(action => action.id === actionId);
        const payload = {
            application_id: applicationId,
            description,
            due_date: elements.formDueDate.value || null,
            priority: elements.formPriority.value,
            status: elements.formStatus.value,
            owner: existing?.owner || state.currentUser || 'Me'
        };

        elements.formSubmit.disabled = true;
        try {
            if (existing) {
                await updateAction(existing.id, payload, {silent: true});
            } else {
                await createAction(payload);
            }
            closeActionModal();
            window.AppUI?.showToast(existing ? 'Career action updated.' : 'Career action added.', {type: 'success'});
        } catch (error) {
            console.error('Unable to save action:', error);
            window.AppUI?.showToast(error.message || 'The career action could not be saved.', {type: 'error'});
        } finally {
            elements.formSubmit.disabled = false;
        }
    }

    async function createAction(payload) {
        requireActionService();
        const created = normalizeAction(await fetchJson(API_ACTIONS, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }));
        state.actions.push(created);
        render();
    }

    async function updateAction(actionId, changes, options = {}) {
        requireActionService();
        const index = state.actions.findIndex(action => action.id === actionId);
        if (index < 0) return;
        const before = {...state.actions[index]};
        const updated = normalizeAction({
            ...before,
            ...changes,
            action_id: before.id,
            completed_at: (changes.status || before.status) === 'done'
                ? (before.completed_at || new Date().toISOString())
                : null
        });
        state.actions[index] = updated;
        render();

        try {
            const result = await fetchJson(`${API_ACTIONS}/${encodeURIComponent(actionId)}`, {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...changes, application_id: updated.application_id})
            });
            state.actions[index] = normalizeAction(result);
            render();
            if (!options.silent) window.AppUI?.showToast('Career action updated.', {type: 'success', duration: 2200});
        } catch (error) {
            state.actions[index] = before;
            options.revert?.();
            render();
            if (!options.silent) window.AppUI?.showToast(error.message || 'The career action could not be updated.', {type: 'error'});
            throw error;
        }
    }

    async function deleteAction(action) {
        try {
            requireActionService();
        } catch (error) {
            window.AppUI?.showToast(error.message, {type: 'error'});
            return;
        }
        const wording = action.generated ? 'Dismiss action?' : 'Delete action?';
        const message = action.generated
            ? `Dismiss “${action.description}”? It will stay hidden unless the underlying finding changes enough to create a new action.`
            : `Delete “${action.description}”?`;
        const confirmed = await window.AppUI.confirm({
            title: wording,
            message,
            confirmLabel: action.generated ? 'Dismiss action' : 'Delete action',
            danger: true
        });
        if (!confirmed) return;

        try {
            await fetchJson(`${API_ACTIONS}/${encodeURIComponent(action.id)}`, {method: 'DELETE'});
            state.actions = state.actions.filter(item => item.id !== action.id);
            render();
            window.AppUI?.showToast(action.generated ? 'Generated action dismissed.' : 'Career action deleted.', {type: 'success'});
        } catch (error) {
            console.error('Unable to delete action:', error);
            window.AppUI?.showToast(error.message || 'The career action could not be removed.', {type: 'error'});
        }
    }

    async function migrateLegacyBrowserActions() {
        const candidates = [];
        try {
            LEGACY_STORAGE_KEYS.forEach(key => {
                const raw = window.localStorage.getItem(key);
                if (!raw) return;
                const parsed = JSON.parse(raw);
                const records = Array.isArray(parsed) ? parsed : parsed?.manualActions;
                ensureArray(records).forEach(record => candidates.push(normalizeAction(record)));
            });
        } catch (error) {
            console.warn('Legacy Career Action Plan data could not be read:', error);
            window.AppUI?.showToast(
                'Older browser-saved actions could not be read. Current actions remain server-backed.',
                {type: 'warning'}
            );
            return;
        }

        if (!candidates.length || !state.applications.length) return;
        const knownApplications = new Set(state.applications.map(item => item.id));
        const applicable = candidates.filter(action => knownApplications.has(action.application_id));
        if (!applicable.length) return;
        const existingKeys = new Set(state.actions.map(action =>
            `${action.application_id}|${action.description}|${action.due_date || ''}`
        ));
        const pending = applicable.filter(action => {
            const key = `${action.application_id}|${action.description}|${action.due_date || ''}`;
            return !existingKeys.has(key);
        });

        if (!pending.length) {
            if (applicable.length === candidates.length) {
                LEGACY_STORAGE_KEYS.forEach(key => window.localStorage.removeItem(key));
            }
            return;
        }

        try {
            const results = [];
            for (const action of pending) {
                const result = await fetchJson(API_ACTIONS, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        action_id: action.id.startsWith('manual-') ? action.id : undefined,
                        application_id: action.application_id,
                        description: action.description,
                        due_date: action.due_date || null,
                        priority: action.priority,
                        status: action.status,
                        owner: action.owner
                    })
                });
                results.push(normalizeAction(result));
            }
            state.actions.push(...results);
            const allLegacyRecordsHandled = applicable.length === candidates.length;
            if (allLegacyRecordsHandled) {
                LEGACY_STORAGE_KEYS.forEach(key => window.localStorage.removeItem(key));
            }
            render();
            window.AppUI?.showToast(
                allLegacyRecordsHandled
                    ? `${results.length} older browser-saved action${results.length === 1 ? '' : 's'} imported to Career Bridge.`
                    : `${results.length} older browser-saved action${results.length === 1 ? '' : 's'} imported. Unmatched legacy records were left untouched.`,
                {type: allLegacyRecordsHandled ? 'success' : 'warning'}
            );
        } catch (error) {
            console.warn('Legacy action migration failed:', error);
            window.AppUI?.showToast(
                'Older browser-saved actions could not be imported. They were not treated as saved Career Bridge actions.',
                {type: 'warning'}
            );
        }
    }

    function applicationUrl(action) {
        const application = state.applications.find(item => item.id === action.application_id);
        return appUrl(application?.builder_url || `/applications/?tab=tailoring&application_id=${encodeURIComponent(action.application_id)}`);
    }

    function sourceLabel(source) {
        return state.sources.find(item => String(item.value) === source)?.label
            || source.replaceAll('_', ' ').replace(/\b\w/g, value => value.toUpperCase());
    }

    function applicationLabel(role, company) {
        return `${role || 'Role not specified'} at ${company || 'Company not specified'}`;
    }

    function isOverdue(action) {
        if (!action.due_date || action.status === 'done') return false;
        return dateAtMidnight(action.due_date) < todayAtMidnight();
    }

    function isDueToday(action) {
        if (!action.due_date || action.status === 'done') return false;
        return dateAtMidnight(action.due_date) === todayAtMidnight();
    }

    function isDueSoon(action) {
        if (!action.due_date || action.status === 'done' || isOverdue(action)) return false;
        const difference = dateAtMidnight(action.due_date) - todayAtMidnight();
        return difference <= 7 * 24 * 60 * 60 * 1000;
    }

    function isDueLater(action) {
        if (!action.due_date || action.status === 'done') return false;
        return dateAtMidnight(action.due_date) - todayAtMidnight() > 7 * 24 * 60 * 60 * 1000;
    }

    function dateAtMidnight(value) {
        return new Date(`${value}T00:00:00`).getTime();
    }

    function todayAtMidnight() {
        const now = new Date();
        return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    }

    function normalizeDateValue(value) {
        if (!value) return '';
        const stringValue = String(value);
        const match = stringValue.match(/^\d{4}-\d{2}-\d{2}/);
        if (match) return match[0];
        const parsed = new Date(stringValue);
        if (Number.isNaN(parsed.getTime())) return '';
        const year = parsed.getFullYear();
        const month = String(parsed.getMonth() + 1).padStart(2, '0');
        const day = String(parsed.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function formatDisplayDate(value) {
        if (!value) return '';
        const parsed = new Date(`${value}T00:00:00`);
        if (Number.isNaN(parsed.getTime())) return value;
        return parsed.toLocaleDateString(window.AppI18n?.locale || undefined, {
            year: 'numeric', month: 'short', day: 'numeric'
        });
    }

    function formatDueDate(action) {
        const formatted = formatDisplayDate(action.due_date);
        if (isOverdue(action)) return `Overdue · ${formatted}`;
        if (isDueToday(action)) return `Today · ${formatted}`;
        if (isDueSoon(action)) return `Due soon · ${formatted}`;
        return formatted;
    }

    async function readApiError(response, fallback) {
        const text = await response.text();
        if (!text) return fallback;
        try {
            const parsed = JSON.parse(text);
            return parsed.error || parsed.message || fallback;
        } catch (error) {
            return text;
        }
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value);
    }

    function appUrl(path) {
        return window.AppUI?.appUrl(path) || path;
    }
})();
