'use strict';

(function () {
    const STORAGE_KEY = 'meeting-assistant-action-center-v1';
    const API_ACTIONS = '/api/career/actions';
    const API_MEETINGS = '/api/career/interview-reviews';
    const PRIORITY_WEIGHT = {urgent: 4, high: 3, medium: 2, low: 1, none: 0};
    const STATUS_LABELS = {
        not_started: 'Not started',
        in_progress: 'In progress',
        blocked: 'Blocked',
        done: 'Done'
    };
    const PRIORITY_LABELS = {
        urgent: 'Urgent',
        high: 'High',
        medium: 'Medium',
        low: 'Low',
        none: 'No priority'
    };

    const state = {
        actions: [],
        meetings: [],
        backendAvailable: false,
        loading: true,
        quickView: 'open',
        requestedMeeting: '',
        currentUser: '',
        openPopoverId: null,
        modalPreviousFocus: null
    };

    const elements = {};

    document.addEventListener('DOMContentLoaded', initialize);

    async function initialize() {
        cacheElements();
        state.currentUser = (elements.app?.dataset.currentUser || '').trim();
        state.requestedMeeting = new URLSearchParams(window.location.search).get('meeting') || '';
        bindEvents();
        await loadActionCenter();
    }

    function cacheElements() {
        elements.app = document.getElementById('action-center-app');
        elements.storageStatus = document.getElementById('action-storage-status');
        elements.tableShell = document.getElementById('action-table-shell');
        elements.tableBody = document.getElementById('action-table-body');
        elements.loadingState = document.getElementById('action-loading-state');
        elements.emptyState = document.getElementById('action-empty-state');
        elements.emptyTitle = document.getElementById('action-empty-title');
        elements.emptyMessage = document.getElementById('action-empty-message');
        elements.resultsSummary = document.getElementById('action-results-summary');
        elements.search = document.getElementById('action-search');
        elements.statusFilter = document.getElementById('action-status-filter');
        elements.priorityFilter = document.getElementById('action-priority-filter');
        elements.ownerFilter = document.getElementById('action-owner-filter');
        elements.meetingFilter = document.getElementById('action-meeting-filter');
        elements.dueFilter = document.getElementById('action-due-filter');
        elements.sort = document.getElementById('action-sort');
        elements.clearFilters = document.getElementById('clear-action-filters');
        elements.addAction = document.getElementById('add-action-button');
        elements.emptyAddAction = document.getElementById('empty-add-action-button');
        elements.modal = document.getElementById('action-modal');
        elements.modalTitle = document.getElementById('action-modal-title');
        elements.modalClose = document.getElementById('action-modal-close');
        elements.form = document.getElementById('action-form');
        elements.formId = document.getElementById('action-form-id');
        elements.formDescription = document.getElementById('action-form-description');
        elements.formMeeting = document.getElementById('action-form-meeting');
        elements.formOwner = document.getElementById('action-form-owner');
        elements.assignMe = document.getElementById('action-assign-me');
        elements.assignMeLabel = document.getElementById('action-assign-me-label');
        elements.formDueDate = document.getElementById('action-form-due-date');
        elements.formPriority = document.getElementById('action-form-priority');
        elements.formStatus = document.getElementById('action-form-status');
        elements.formCancel = document.getElementById('action-form-cancel');
        elements.formSubmit = document.getElementById('action-form-submit');
    }

    function bindEvents() {
        const rerender = window.AppUI?.debounce(render, 120) || render;
        elements.search?.addEventListener('input', rerender);
        [elements.meetingFilter, elements.ownerFilter, elements.dueFilter, elements.priorityFilter, elements.statusFilter, elements.sort]
            .forEach(element => element?.addEventListener('change', render));

        elements.clearFilters?.addEventListener('click', clearFilters);
        elements.addAction?.addEventListener('click', () => openActionModal());
        elements.emptyAddAction?.addEventListener('click', () => openActionModal());
        elements.modalClose?.addEventListener('click', closeActionModal);
        elements.formCancel?.addEventListener('click', closeActionModal);
        elements.assignMe?.addEventListener('click', assignActionToCurrentUser);
        elements.formOwner?.addEventListener('input', updateAssignToMeState);
        elements.form?.addEventListener('submit', saveActionFromForm);

        document.querySelectorAll('[data-quick-view]').forEach(button => {
            button.addEventListener('click', () => {
                state.quickView = button.dataset.quickView || 'open';
                elements.statusFilter.value = 'all';
                elements.dueFilter.value = 'all';
                document.querySelectorAll('[data-quick-view]').forEach(item => {
                    item.classList.toggle('is-active', item === button);
                });
                render();
            });
        });

        document.querySelectorAll('[data-kpi-filter]').forEach(button => {
            button.addEventListener('click', () => {
                const value = button.dataset.kpiFilter || 'all';
                state.quickView = 'all';
                document.querySelectorAll('[data-quick-view]').forEach(item => {
                    item.classList.toggle('is-active', item.dataset.quickView === 'all');
                });
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
            if (event.key === 'Escape') {
                if (!elements.modal?.hidden) closeActionModal();
                closeAllPopovers();
            }
        });

        document.addEventListener('click', event => {
            if (!event.target.closest('.action-row-actions')) closeAllPopovers();
        });
    }

    async function loadActionCenter() {
        state.loading = true;
        updateLoadingState();

        let actionsResponse = null;
        try {
            actionsResponse = await fetch(appUrl(API_ACTIONS), {headers: {'Accept': 'application/json'}});
        } catch (error) {
            console.warn('Action API unavailable; using interview-derived actions.', error);
        }

        if (actionsResponse?.ok) {
            try {
                const payload = await actionsResponse.json();
                state.actions = ensureActionArray(payload).map((item, index) => normalizeAction(item, index));
                state.backendAvailable = true;
                await loadMeetingsForReference(false);

                let migrated = false;
                try {
                    migrated = await migrateBrowserActionsToBackend();
                } catch (migrationError) {
                    console.error('Unable to migrate browser-saved actions:', migrationError);
                    elements.storageStatus.textContent =
                        'Server synchronization is active, but older browser-saved changes could not be migrated automatically.';
                }

                if (migrated) {
                    const refreshed = await fetch(appUrl(API_ACTIONS), {headers: {'Accept': 'application/json'}});
                    if (refreshed.ok) {
                        state.actions = ensureActionArray(await refreshed.json())
                            .map((item, index) => normalizeAction(item, index));
                    }
                    elements.storageStatus.textContent =
                        'Browser-saved actions were migrated and are now synchronized with your account.';
                } else if (!elements.storageStatus.textContent.includes('could not be migrated')) {
                    elements.storageStatus.textContent = 'Changes are synchronized with your Career Action Plan data.';
                }
            } catch (error) {
                console.error('Invalid action response:', error);
                await loadMeetingFallback('The action service returned invalid data. Showing actions extracted from mock interviews.');
            }
        } else {
            await loadMeetingFallback();
        }

        state.loading = false;
        populateFilterOptions();
        applyRequestedMeetingFilter();
        updateLoadingState();
        render();
    }

    async function migrateBrowserActionsToBackend() {
        const stored = readLocalState();
        const hasBrowserData = Boolean(
            Object.keys(stored.overrides || {}).length ||
            (stored.manualActions || []).length ||
            (stored.deletedIds || []).length
        );
        if (!hasBrowserData) return false;

        for (const manualAction of stored.manualActions || []) {
            const response = await fetch(appUrl(API_ACTIONS), {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify(toApiPayload(normalizeAction(manualAction)))
            });
            if (!response.ok) {
                throw new Error(await readApiError(response, 'A browser-saved action could not be migrated.'));
            }
        }

        for (const [actionId, override] of Object.entries(stored.overrides || {})) {
            const current = state.actions.find(action => action.id === actionId);
            if (!current) continue;
            await updateActionOnServer(normalizeAction({...current, ...override, action_id: actionId, id: actionId}));
        }

        for (const actionId of stored.deletedIds || []) {
            const current = state.actions.find(action => action.id === actionId);
            if (!current) continue;
            await deleteActionOnServer(current);
        }

        window.localStorage.removeItem(STORAGE_KEY);
        return true;
    }

    async function loadMeetingFallback(message = '') {
        state.backendAvailable = false;
        await loadMeetingsForReference(true);
        state.actions = mergeWithLocalState(extractActionsFromMeetings(state.meetings));
        elements.storageStatus.textContent = message ||
            'Action API not detected. Changes are saved in this browser and remain linked to the source mock interviews.';
    }

    async function loadMeetingsForReference(required) {
        try {
            const response = await fetch(appUrl(API_MEETINGS), {headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error(`Mock-interview request failed with ${response.status}`);
            state.meetings = sortMeetingsByDate(ensureArrayPayload(await response.json()));
        } catch (error) {
            console.error('Unable to load mock interviews for Career Action Plan:', error);
            state.meetings = [];
            if (required) {
                elements.storageStatus.textContent = 'Interview-derived actions could not be loaded. You can still add manual actions in this browser.';
                state.actions = mergeWithLocalState([]);
            }
        }
    }

    function ensureActionArray(payload) {
        const unwrapped = unwrapDynamoDBValue(payload);
        if (Array.isArray(unwrapped)) return unwrapped;
        if (Array.isArray(unwrapped?.items)) return unwrapped.items;
        if (Array.isArray(unwrapped?.actions)) return unwrapped.actions;
        if (Array.isArray(unwrapped?.data)) return unwrapped.data;
        throw new TypeError('The server returned an invalid action list.');
    }

    function extractActionsFromMeetings(meetings) {
        const actions = [];
        meetings.forEach((meeting, meetingIndex) => {
            const meetingId = getMeetingId(meeting, meetingIndex);
            const meetingName = getMeetingName(meeting, meetingIndex);
            const meetingDate = getMeetingDate(meeting);
            const rawActions = normalizeDynamoDBList(meeting.action_items);

            rawActions.forEach((rawAction, actionIndex) => {
                actions.push(normalizeAction(rawAction, actionIndex, {
                    meetingId,
                    meetingName,
                    meetingDate,
                    derivedId: `meeting-${simpleHash(`${meetingId}|${actionIndex}|${actionText(rawAction)}`)}`,
                    source: 'meeting'
                }));
            });
        });
        return actions;
    }

    function normalizeAction(rawAction, index = 0, context = {}) {
        const action = unwrapDynamoDBValue(rawAction);
        const objectValue = action && typeof action === 'object' && !Array.isArray(action) ? action : {};
        const description = String(
            objectValue.description || objectValue.task || objectValue.text || objectValue.action ||
            (typeof action === 'string' || typeof action === 'number' ? action : '') ||
            'Untitled action'
        ).trim();
        const meetingId = String(
            objectValue.meeting_id || objectValue.transcript_id || context.meetingId || ''
        );
        const meetingName = String(
            objectValue.meeting_name || objectValue.meeting || context.meetingName || 'No linked application'
        );
        const rawStatus = String(objectValue.status || (objectValue.completed ? 'done' : 'not_started')).toLowerCase();
        const rawPriority = String(objectValue.priority || 'none').toLowerCase();
        const id = String(
            objectValue.action_id || objectValue.id || context.derivedId ||
            `action-${simpleHash(`${meetingId}|${description}|${index}`)}`
        );

        return {
            id,
            action_id: id,
            description,
            meeting_id: meetingId,
            meeting_name: meetingName,
            meeting_date: normalizeDateValue(objectValue.meeting_date || objectValue.timestamp || context.meetingDate || ''),
            owner: String(objectValue.owner || objectValue.assignee || 'Unassigned').trim() || 'Unassigned',
            due_date: normalizeDateValue(objectValue.due_date || objectValue.deadline || objectValue.due || ''),
            priority: PRIORITY_WEIGHT[rawPriority] !== undefined ? rawPriority : 'none',
            status: STATUS_LABELS[rawStatus] ? rawStatus : 'not_started',
            source: String(objectValue.source || context.source || 'manual'),
            created_at: String(objectValue.created_at || objectValue.created || new Date().toISOString()),
            completed_at: objectValue.completed_at || null
        };
    }

    function actionText(value) {
        const item = unwrapDynamoDBValue(value);
        if (item && typeof item === 'object') {
            return item.description || item.task || item.text || item.action || JSON.stringify(item);
        }
        return String(item || '');
    }

    function mergeWithLocalState(actions) {
        const stored = readLocalState();
        const deleted = new Set(stored.deletedIds || []);
        const overrides = stored.overrides || {};
        const merged = actions
            .filter(action => !deleted.has(action.id))
            .map(action => ({...action, ...(overrides[action.id] || {})}));

        (stored.manualActions || []).forEach(item => {
            const normalized = normalizeAction(item);
            if (!deleted.has(normalized.id) && !merged.some(action => action.id === normalized.id)) {
                merged.push(normalized);
            }
        });
        return merged;
    }

    function readLocalState() {
        try {
            const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
            return {
                overrides: parsed.overrides && typeof parsed.overrides === 'object' ? parsed.overrides : {},
                manualActions: Array.isArray(parsed.manualActions) ? parsed.manualActions : [],
                deletedIds: Array.isArray(parsed.deletedIds) ? parsed.deletedIds : []
            };
        } catch (error) {
            console.warn('Unable to read Career Action Plan browser data:', error);
            return {overrides: {}, manualActions: [], deletedIds: []};
        }
    }

    function writeLocalState(data) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    }

    function persistLocally(action, options = {}) {
        const stored = readLocalState();
        const manualIndex = stored.manualActions.findIndex(item => String(item.id || item.action_id) === action.id);
        const isManual = action.source === 'manual' || action.id.startsWith('manual-');

        if (options.delete) {
            if (manualIndex >= 0) stored.manualActions.splice(manualIndex, 1);
            delete stored.overrides[action.id];
            if (!stored.deletedIds.includes(action.id)) stored.deletedIds.push(action.id);
        } else if (isManual) {
            if (manualIndex >= 0) stored.manualActions[manualIndex] = action;
            else stored.manualActions.push(action);
            stored.deletedIds = stored.deletedIds.filter(id => id !== action.id);
        } else {
            stored.overrides[action.id] = {
                description: action.description,
                owner: action.owner,
                due_date: action.due_date,
                priority: action.priority,
                status: action.status,
                completed_at: action.completed_at
            };
            stored.deletedIds = stored.deletedIds.filter(id => id !== action.id);
        }

        writeLocalState(stored);
    }

    function populateFilterOptions() {
        const ownerValue = elements.ownerFilter.value || 'all';
        const meetingValue = elements.meetingFilter.value || 'all';
        const owners = uniqueSorted(state.actions.map(action => action.owner).filter(Boolean));
        const meetings = getMeetingOptions();

        replaceSelectOptions(elements.ownerFilter, [{value: 'all', label: 'All owners'}].concat(
            owners.map(owner => ({value: owner, label: owner}))
        ));
        replaceSelectOptions(elements.meetingFilter, [{value: 'all', label: 'All applications'}].concat(meetings));
        replaceSelectOptions(elements.formMeeting, [{value: '', label: 'No linked application'}].concat(meetings));

        if (Array.from(elements.ownerFilter.options).some(option => option.value === ownerValue)) {
            elements.ownerFilter.value = ownerValue;
        }
        if (Array.from(elements.meetingFilter.options).some(option => option.value === meetingValue)) {
            elements.meetingFilter.value = meetingValue;
        }
    }

    function getMeetingOptions() {
        const map = new Map();
        state.meetings.forEach((meeting, index) => {
            const id = getMeetingId(meeting, index);
            map.set(id, getMeetingName(meeting, index));
        });
        state.actions.forEach(action => {
            if (action.meeting_id && !map.has(action.meeting_id)) map.set(action.meeting_id, action.meeting_name);
        });
        return Array.from(map.entries())
            .map(([value, label]) => ({value, label}))
            .sort((a, b) => a.label.localeCompare(b.label));
    }

    function replaceSelectOptions(select, options) {
        if (!select) return;
        select.replaceChildren();
        options.forEach(item => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            select.appendChild(option);
        });
    }

    function applyRequestedMeetingFilter() {
        if (!state.requestedMeeting) return;
        const matchingOption = Array.from(elements.meetingFilter.options).find(option =>
            option.value === state.requestedMeeting || option.textContent === state.requestedMeeting
        );
        if (matchingOption) elements.meetingFilter.value = matchingOption.value;
    }

    function render() {
        if (state.loading) return;
        const filtered = getFilteredActions();
        updateKpis();
        renderTable(filtered);
        updateResultsSummary(filtered);
    }

    function getFilteredActions() {
        const search = (elements.search.value || '').trim().toLowerCase();
        const status = elements.statusFilter.value;
        const priority = elements.priorityFilter.value;
        const owner = elements.ownerFilter.value;
        const meeting = elements.meetingFilter.value;
        const due = elements.dueFilter.value;

        const filtered = state.actions.filter(action => {
            const searchable = [action.description, action.meeting_name, action.owner, action.priority, action.status]
                .join(' ').toLowerCase();
            if (search && !searchable.includes(search)) return false;
            if (priority !== 'all' && action.priority !== priority) return false;
            if (owner !== 'all' && action.owner !== owner) return false;
            if (meeting !== 'all' && action.meeting_id !== meeting) return false;
            if (!matchesDueDateFilter(action, due)) return false;
            if (!matchesStatusFilter(action, status)) return false;
            if (state.quickView === 'mine' && !isCurrentUserOwner(action.owner)) return false;
            if (state.quickView === 'open' && action.status === 'done') return false;
            if (state.quickView === 'closed' && action.status !== 'done') return false;
            return true;
        });

        return filtered.sort(getSortComparator(elements.sort.value));
    }

    function matchesStatusFilter(action, filter) {
        if (filter === 'all') return true;
        if (filter === 'open') return action.status !== 'done';
        return action.status === filter;
    }

    function matchesDueDateFilter(action, filter) {
        if (filter === 'all') return true;
        if (filter === 'none') return !action.due_date;
        if (filter === 'overdue') return isOverdue(action);
        if (filter === 'today') return isDueToday(action);
        if (filter === 'due_soon') return isDueSoon(action);
        if (filter === 'later') return isDueLater(action);
        return true;
    }

    function getSortComparator(sortValue) {
        if (sortValue === 'due_asc') return (a, b) => compareDates(a.due_date, b.due_date, true);
        if (sortValue === 'due_desc') return (a, b) => compareDates(a.due_date, b.due_date, false);
        if (sortValue === 'priority') return (a, b) => PRIORITY_WEIGHT[b.priority] - PRIORITY_WEIGHT[a.priority] || compareDates(a.due_date, b.due_date, true);
        if (sortValue === 'meeting') return (a, b) => a.meeting_name.localeCompare(b.meeting_name) || a.description.localeCompare(b.description);
        if (sortValue === 'newest') return (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime();

        return (a, b) => {
            const doneDiff = Number(a.status === 'done') - Number(b.status === 'done');
            if (doneDiff) return doneDiff;
            const overdueDiff = Number(isOverdue(b)) - Number(isOverdue(a));
            if (overdueDiff) return overdueDiff;
            const blockedDiff = Number(b.status === 'blocked') - Number(a.status === 'blocked');
            if (blockedDiff) return blockedDiff;
            const priorityDiff = PRIORITY_WEIGHT[b.priority] - PRIORITY_WEIGHT[a.priority];
            if (priorityDiff) return priorityDiff;
            return compareDates(a.due_date, b.due_date, true);
        };
    }

    function compareDates(dateA, dateB, ascending) {
        const timeA = dateA ? new Date(`${dateA}T00:00:00`).getTime() : Number.POSITIVE_INFINITY;
        const timeB = dateB ? new Date(`${dateB}T00:00:00`).getTime() : Number.POSITIVE_INFINITY;
        const result = timeA - timeB;
        return ascending ? result : -result;
    }

    function renderTable(actions) {
        elements.tableBody.replaceChildren();
        elements.loadingState.hidden = true;
        elements.tableShell.setAttribute('aria-busy', 'false');

        if (actions.length === 0) {
            elements.emptyState.hidden = false;

            if (state.actions.length > 0 && !hasActiveFilters() && state.quickView === 'open') {
                elements.emptyTitle.textContent = 'No open actions';
                elements.emptyMessage.textContent = 'You are all caught up. Select Closed to review completed actions.';
            } else if (state.actions.length > 0 && state.quickView === 'closed' && elements.statusFilter.value === 'all') {
                elements.emptyTitle.textContent = 'No closed actions';
                elements.emptyMessage.textContent = 'Completed actions will appear here.';
            } else {
                const filtered = hasActiveFilters();
                elements.emptyTitle.textContent = filtered ? 'No actions match these filters' : 'No actions yet';
                elements.emptyMessage.textContent = filtered
                    ? 'Clear or change a filter to see more actions.'
                    : 'Add an action manually or create improvement actions from an Interview Review.';
            }
            return;
        }

        elements.emptyState.hidden = true;
        actions.forEach(action => elements.tableBody.appendChild(createActionRow(action)));
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
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = action.status === 'done';
        checkbox.setAttribute('aria-label', `${checkbox.checked ? 'Reopen' : 'Complete'} ${action.description}`);
        const checkVisual = document.createElement('span');
        checkVisual.textContent = '✓';
        checkbox.addEventListener('change', async () => {
            const previousStatus = action.status;
            const nextStatus = checkbox.checked ? 'done' : 'not_started';
            try {
                await updateAction(action.id, {status: nextStatus}, {revert: () => {
                    checkbox.checked = previousStatus === 'done';
                }});
            } catch (error) {
                // updateAction restores the previous state and displays the error.
            }
        });
        checkLabel.append(checkbox, checkVisual);
        completionCell.appendChild(checkLabel);

        const taskCell = document.createElement('td');
        taskCell.className = 'action-task-cell';
        const taskTitle = document.createElement('span');
        taskTitle.className = 'action-task-title';
        taskTitle.textContent = action.description;
        const taskMeta = document.createElement('span');
        taskMeta.className = 'action-task-meta';
        const sourceChip = document.createElement('span');
        sourceChip.className = 'action-source-chip';
        sourceChip.textContent = action.source === 'meeting' ? 'Interview coaching action' : 'Manual action';
        taskMeta.appendChild(sourceChip);
        if (action.status === 'blocked') {
            const blockedChip = document.createElement('span');
            blockedChip.className = 'action-status-badge action-priority-urgent';
            blockedChip.textContent = 'Blocked';
            taskMeta.appendChild(blockedChip);
        }
        taskCell.append(taskTitle, taskMeta);

        const meetingCell = document.createElement('td');
        if (action.meeting_id) {
            const link = document.createElement('a');
            link.className = 'action-meeting-link';
            link.href = `${appUrl('/interview-review')}?meeting=${encodeURIComponent(action.meeting_id)}`;
            link.textContent = action.meeting_name || 'Open mock interview';
            meetingCell.appendChild(link);
            if (action.meeting_date) {
                const date = document.createElement('span');
                date.className = 'action-meeting-date';
                date.textContent = formatDisplayDate(action.meeting_date);
                meetingCell.appendChild(date);
            }
        } else {
            const noMeeting = document.createElement('span');
            noMeeting.className = 'action-date-empty';
            noMeeting.textContent = 'No linked application';
            meetingCell.appendChild(noMeeting);
        }

        const ownerCell = document.createElement('td');
        const ownerChip = document.createElement('span');
        ownerChip.className = 'action-owner-chip';
        ownerChip.textContent = action.owner || 'Unassigned';
        ownerCell.appendChild(ownerChip);

        const dueCell = document.createElement('td');
        if (action.due_date) {
            const dueChip = document.createElement('span');
            dueChip.className = 'action-date-chip';
            if (isOverdue(action)) dueChip.classList.add('is-overdue');
            else if (isDueSoon(action)) dueChip.classList.add('is-due-soon');
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
                await updateAction(action.id, {priority: prioritySelect.value}, {revert: () => { prioritySelect.value = oldValue; }});
            } catch (error) {
                // updateAction restores the previous state and displays the error.
            }
        });
        priorityCell.appendChild(prioritySelect);

        const statusCell = document.createElement('td');
        const statusSelect = createInlineSelect(STATUS_LABELS, action.status, `Status for ${action.description}`);
        statusSelect.addEventListener('change', async () => {
            const oldValue = action.status;
            try {
                await updateAction(action.id, {status: statusSelect.value}, {revert: () => { statusSelect.value = oldValue; }});
            } catch (error) {
                // updateAction restores the previous state and displays the error.
            }
        });
        statusCell.appendChild(statusSelect);

        const menuCell = document.createElement('td');
        menuCell.className = 'action-menu-column';
        const actionsWrapper = document.createElement('div');
        actionsWrapper.className = 'action-row-actions';
        const menuButton = document.createElement('button');
        menuButton.type = 'button';
        menuButton.className = 'action-row-menu';
        menuButton.setAttribute('aria-label', `More options for ${action.description}`);
        menuButton.setAttribute('aria-expanded', 'false');
        menuButton.textContent = '⋮';
        menuButton.addEventListener('click', event => {
            event.stopPropagation();
            toggleRowPopover(action.id, actionsWrapper, menuButton);
        });
        actionsWrapper.appendChild(menuButton);
        menuCell.appendChild(actionsWrapper);

        row.append(completionCell, taskCell, meetingCell, ownerCell, dueCell, priorityCell, statusCell, menuCell);
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

        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'is-danger';
        deleteButton.textContent = 'Delete action';
        deleteButton.addEventListener('click', async () => {
            closeAllPopovers();
            await deleteAction(action);
        });

        popover.append(editButton, deleteButton);
        wrapper.appendChild(popover);
        button.setAttribute('aria-expanded', 'true');
        state.openPopoverId = actionId;
    }

    function closeAllPopovers() {
        document.querySelectorAll('.action-row-popover').forEach(popover => popover.remove());
        document.querySelectorAll('.action-row-menu').forEach(button => button.setAttribute('aria-expanded', 'false'));
        state.openPopoverId = null;
    }

    function updateKpis() {
        const open = state.actions.filter(action => action.status !== 'done').length;
        const dueSoon = state.actions.filter(isDueSoon).length;
        const overdue = state.actions.filter(isOverdue).length;
        const done = state.actions.filter(action => action.status === 'done').length;
        setText('action-open-count', open);
        setText('action-due-soon-count', dueSoon);
        setText('action-overdue-count', overdue);
        setText('action-done-count', done);
    }

    function updateResultsSummary(actions) {
        let total = state.actions.length;
        let label = 'action';

        if (state.quickView === 'open') {
            total = state.actions.filter(action => action.status !== 'done').length;
            label = 'open action';
        } else if (state.quickView === 'closed') {
            total = state.actions.filter(action => action.status === 'done').length;
            label = 'closed action';
        } else if (state.quickView === 'mine') {
            total = state.actions.filter(action => isCurrentUserOwner(action.owner)).length;
            label = 'assigned action';
        }

        const count = actions.length;
        const actionLabel = `${label}${total === 1 ? '' : 's'}`;
        elements.resultsSummary.textContent = count === total
            ? `${count} ${actionLabel} shown`
            : `${count} of ${total} ${actionLabel} shown`;
    }

    function updateLoadingState() {
        elements.loadingState.hidden = !state.loading;
        elements.tableShell?.setAttribute('aria-busy', String(state.loading));
        if (state.loading) {
            elements.emptyState.hidden = true;
            elements.tableBody.replaceChildren();
        }
    }

    function clearFilters() {
        state.quickView = 'open';
        document.querySelectorAll('[data-quick-view]').forEach(button => {
            button.classList.toggle('is-active', button.dataset.quickView === 'open');
        });
        elements.search.value = '';
        elements.statusFilter.value = 'all';
        elements.priorityFilter.value = 'all';
        elements.ownerFilter.value = 'all';
        elements.meetingFilter.value = 'all';
        elements.dueFilter.value = 'all';
        elements.sort.value = 'attention';
        render();
    }

    function hasActiveFilters() {
        return Boolean(
            elements.search.value.trim() ||
            elements.statusFilter.value !== 'all' ||
            elements.priorityFilter.value !== 'all' ||
            elements.ownerFilter.value !== 'all' ||
            elements.meetingFilter.value !== 'all' ||
            elements.dueFilter.value !== 'all' ||
            state.quickView !== 'open'
        );
    }

    function openActionModal(action = null) {
        state.modalPreviousFocus = document.activeElement;
        elements.form.reset();
        elements.formId.value = action?.id || '';
        elements.formDescription.value = action?.description || '';
        elements.formOwner.value = action?.owner === 'Unassigned' ? '' : (action?.owner || state.currentUser || '');
        elements.formDueDate.value = action?.due_date || '';
        elements.formPriority.value = action?.priority || 'none';
        elements.formStatus.value = action?.status || 'not_started';
        updateAssignToMeState();
        elements.modalTitle.textContent = action ? 'Edit action' : 'Add action';
        elements.formSubmit.textContent = action ? 'Save changes' : 'Add action';

        const meetingValue = action?.meeting_id || (elements.meetingFilter.value !== 'all' ? elements.meetingFilter.value : '');
        elements.formMeeting.value = Array.from(elements.formMeeting.options).some(option => option.value === meetingValue)
            ? meetingValue
            : '';

        elements.modal.hidden = false;
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(() => elements.formDescription.focus());
    }

    function assignActionToCurrentUser() {
        if (!state.currentUser) {
            window.AppUI?.showToast(
                'Add your name or email to your profile before assigning actions to yourself.',
                {type: 'warning'}
            );
            return;
        }

        elements.formOwner.value = state.currentUser;
        updateAssignToMeState();
        elements.formOwner.focus();
    }

    function updateAssignToMeState() {
        if (!elements.assignMe) return;

        const hasCurrentUser = Boolean(state.currentUser);
        const isAssigned = hasCurrentUser && isCurrentUserOwner(elements.formOwner?.value);
        elements.assignMe.disabled = !hasCurrentUser;
        elements.assignMe.classList.toggle('is-assigned', isAssigned);
        elements.assignMe.setAttribute('aria-pressed', String(isAssigned));
        elements.assignMe.title = hasCurrentUser
            ? (isAssigned ? `Assigned to ${state.currentUser}` : `Assign to ${state.currentUser}`)
            : 'Your profile does not include a name, email, or user ID.';

        if (elements.assignMeLabel) {
            elements.assignMeLabel.textContent = isAssigned ? 'Assigned to me' : 'Assign to me';
        }
    }

    function closeActionModal() {
        if (elements.modal.hidden) return;
        elements.modal.hidden = true;
        document.body.style.overflow = '';
        state.modalPreviousFocus?.focus?.();
    }

    async function saveActionFromForm(event) {
        event.preventDefault();
        const description = elements.formDescription.value.trim();
        if (!description) {
            elements.formDescription.focus();
            return;
        }

        const id = elements.formId.value;
        const linkedMeeting = getSelectedMeetingReference(elements.formMeeting.value);
        const changes = {
            description,
            meeting_id: linkedMeeting.id,
            meeting_name: linkedMeeting.name,
            meeting_date: linkedMeeting.date,
            owner: elements.formOwner.value.trim() || 'Unassigned',
            due_date: elements.formDueDate.value,
            priority: elements.formPriority.value,
            status: elements.formStatus.value
        };

        elements.formSubmit.disabled = true;
        elements.formSubmit.textContent = id ? 'Saving…' : 'Adding…';

        try {
            if (id) {
                await updateAction(id, changes, {silent: true});
                window.AppUI?.showToast('Action updated.', {type: 'success'});
            } else {
                await createAction(changes);
                window.AppUI?.showToast('Action added.', {type: 'success'});
            }
            closeActionModal();
        } catch (error) {
            console.error('Unable to save action:', error);
            window.AppUI?.showToast(error.message || 'The action could not be saved.', {type: 'error'});
        } finally {
            elements.formSubmit.disabled = false;
            elements.formSubmit.textContent = id ? 'Save changes' : 'Add action';
        }
    }

    function getSelectedMeetingReference(meetingId) {
        if (!meetingId) return {id: '', name: 'No linked application', date: ''};
        const meetingIndex = state.meetings.findIndex((meeting, index) => getMeetingId(meeting, index) === meetingId);
        if (meetingIndex >= 0) {
            return {
                id: meetingId,
                name: getMeetingName(state.meetings[meetingIndex], meetingIndex),
                date: normalizeDateValue(getMeetingDate(state.meetings[meetingIndex]))
            };
        }
        const existing = state.actions.find(action => action.meeting_id === meetingId);
        return {id: meetingId, name: existing?.meeting_name || 'Linked application', date: existing?.meeting_date || ''};
    }

    async function createAction(changes) {
        const action = normalizeAction({
            ...changes,
            id: `manual-${createId()}`,
            source: 'manual',
            created_at: new Date().toISOString()
        });

        if (state.backendAvailable) {
            const response = await fetch(appUrl(API_ACTIONS), {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify(toApiPayload(action))
            });
            if (!response.ok) throw new Error(await readApiError(response, 'The action could not be added.'));
            const result = await response.json().catch(() => null);
            state.actions.push(result ? normalizeAction(result) : action);
        } else {
            persistLocally(action);
            state.actions.push(action);
        }

        populateFilterOptions();
        render();
    }

    async function updateAction(actionId, changes, options = {}) {
        const index = state.actions.findIndex(action => action.id === actionId);
        if (index < 0) return;
        const before = {...state.actions[index]};
        const updated = {
            ...before,
            ...changes,
            completed_at: (changes.status || before.status) === 'done'
                ? (before.completed_at || new Date().toISOString())
                : null
        };

        state.actions[index] = updated;
        render();

        try {
            if (state.backendAvailable) {
                await updateActionOnServer(updated);
            } else {
                persistLocally(updated);
            }
            populateFilterOptions();
            render();
            if (!options.silent) window.AppUI?.showToast('Action updated.', {type: 'success', duration: 2200});
        } catch (error) {
            state.actions[index] = before;
            options.revert?.();
            render();
            if (!options.silent) window.AppUI?.showToast(error.message || 'The action could not be updated.', {type: 'error'});
            throw error;
        }
    }

    async function updateActionOnServer(action) {
        const payload = toApiPayload(action);
        const response = await fetch(`${appUrl(API_ACTIONS)}/${encodeURIComponent(action.id)}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            throw new Error(await readApiError(response, 'The action could not be updated.'));
        }
    }

    async function deleteAction(action) {
        const confirmed = await window.AppUI.confirm({
            title: 'Delete action?',
            message: `Delete “${action.description}”? This removes it from the Career Action Plan.`,
            confirmLabel: 'Delete action',
            danger: true
        });
        if (!confirmed) return;

        try {
            if (state.backendAvailable) {
                await deleteActionOnServer(action);
            } else {
                persistLocally(action, {delete: true});
            }
            state.actions = state.actions.filter(item => item.id !== action.id);
            populateFilterOptions();
            render();
            window.AppUI?.showToast('Action deleted.', {type: 'success'});
        } catch (error) {
            console.error('Unable to delete action:', error);
            window.AppUI?.showToast(error.message || 'The action could not be deleted.', {type: 'error'});
        }
    }

    async function deleteActionOnServer(action) {
        const response = await fetch(`${appUrl(API_ACTIONS)}/${encodeURIComponent(action.id)}`, {method: 'DELETE'});
        if (!response.ok) throw new Error(await readApiError(response, 'The action could not be deleted.'));
    }

    function toApiPayload(action) {
        return {
            action_id: action.id,
            description: action.description,
            meeting_id: action.meeting_id || null,
            meeting_name: action.meeting_name || null,
            meeting_date: action.meeting_date || null,
            owner: action.owner,
            due_date: action.due_date || null,
            priority: action.priority,
            status: action.status,
            source: action.source,
            created_at: action.created_at,
            completed_at: action.completed_at
        };
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

    function isCurrentUserOwner(owner) {
        if (!state.currentUser) return false;
        const normalizedOwner = String(owner || '').trim().toLowerCase();
        const normalizedUser = state.currentUser.toLowerCase();
        return normalizedOwner === normalizedUser || normalizedOwner === 'me';
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
        const difference = dateAtMidnight(action.due_date) - todayAtMidnight();
        return difference > 7 * 24 * 60 * 60 * 1000;
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
        const isoMatch = stringValue.match(/^\d{4}-\d{2}-\d{2}/);
        if (isoMatch) return isoMatch[0];
        const parsed = new Date(stringValue);
        if (Number.isNaN(parsed.getTime())) return '';
        const year = parsed.getFullYear();
        const month = String(parsed.getMonth() + 1).padStart(2, '0');
        const day = String(parsed.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function formatDisplayDate(value) {
        if (!value) return '';
        const date = new Date(`${value}T00:00:00`);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleDateString(window.AppI18n?.locale || undefined, {year: 'numeric', month: 'short', day: 'numeric'});
    }

    function formatDueDate(action) {
        const formatted = formatDisplayDate(action.due_date);
        if (isOverdue(action)) return `Overdue · ${formatted}`;
        if (isDueSoon(action)) return `Due soon · ${formatted}`;
        return formatted;
    }

    function getMeetingId(meeting, index) {
        return String(
            getValue(meeting?.meeting_id, '') ||
            getValue(meeting?.transcript_id, '') ||
            getValue(meeting?.id, '') ||
            getMeetingDate(meeting) ||
            `meeting-${index}`
        );
    }

    function uniqueSorted(values) {
        return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
    }

    function simpleHash(value) {
        let hash = 2166136261;
        const text = String(value || '');
        for (let index = 0; index < text.length; index += 1) {
            hash ^= text.charCodeAt(index);
            hash = Math.imul(hash, 16777619);
        }
        return (hash >>> 0).toString(36);
    }

    function createId() {
        if (window.crypto?.randomUUID) return window.crypto.randomUUID();
        return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value);
    }

    function appUrl(path) {
        return window.AppUI?.appUrl(path) || path;
    }
})();
