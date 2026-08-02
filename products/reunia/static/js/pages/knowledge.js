'use strict';

 document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('knowledgePage');
    if (!root) return;

    const appUrl = window.AppUI?.appUrl || (path => path);
    const scopeKey = encodeURIComponent(root.dataset.contextStorageScope || 'default');
    const contextStorageKey = `meetingAssistant.assistantContext.v2.${scopeKey}`;
    const meetingContextStorageKey = `meetingAssistant.meetingContexts.v1.${scopeKey}`;
    const meetingMaterialsStorageKey = `meetingAssistant.meetingMaterials.v1.${scopeKey}`;
    const upcomingMeetingsStorageKey = `meetingAssistant.upcomingMeetings.v1.${scopeKey}`;

    const endpoints = {
        upload: appUrl('/api/career/evidence'),
        files: appUrl('/api/career/evidence'),
        collections: appUrl('/api/knowledge/collections'),
        ask: appUrl('/api/career/evidence/search'),
        transcripts: appUrl('/api/career/interview-reviews'),
        context: root.dataset.contextEndpoint || appUrl('/api/career/profile'),
        materials: root.dataset.materialsEndpoint || appUrl('/api/career/application-materials'),
        meetings: appUrl('/api/career/application-workspaces'),
        activeMeeting: appUrl('/api/career/active-application')
    };

    const state = {
        selectedCollectionId: 'all',
        selectedCollectionName: 'All Files',
        completedMeetings: [],
        upcomingMeetings: normalizeUpcomingMeetings(readJsonStorage(upcomingMeetingsStorageKey, [])),
        context: null,
        meetingContexts: normalizeMeetingContexts(readJsonStorage(meetingContextStorageKey, {})),
        materials: readJsonStorage(meetingMaterialsStorageKey, {}),
        activeModal: null,
        modalTrigger: null,
        pendingTemporaryFiles: [],
        currentTemporaryFiles: [],
        pendingCollectionDeleteId: null,
        pendingMeetingDeleteId: null
    };

    let savedDefaultContextSnapshot = '';
    let defaultContextLoaded = false;
    const savedMeetingContextSnapshots = {};
    let activeContextMeetingId = '';
    let isSavingContext = false;

    function showToast(message, isError = false) {
        if (window.AppUI?.showToast) {
            window.AppUI.showToast(message, {type: isError ? 'error' : 'success'});
            return;
        }
        const toast = document.getElementById('toast');
        if (!toast) return;
        toast.textContent = String(message || '');
        toast.classList.toggle('error', isError);
        toast.hidden = false;
        window.clearTimeout(showToast.timeoutId);
        showToast.timeoutId = window.setTimeout(() => { toast.hidden = true; }, 3800);
    }

    function readJsonStorage(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (error) {
            console.warn(`Unable to read ${key}:`, error);
            return fallback;
        }
    }

    function writeJsonStorage(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
            return true;
        } catch (error) {
            console.warn(`Unable to save ${key}:`, error);
            return false;
        }
    }

    function formatBytes(bytes) {
        const value = Number(bytes || 0);
        if (!value) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
        return `${(value / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
    }

    function escapeHtml(value) {
        const element = document.createElement('div');
        element.textContent = String(value ?? '');
        return element.innerHTML;
    }

    function unwrapValue(value, fallback = '') {
        if (value === null || value === undefined) return fallback;
        if (typeof value !== 'object') return value;
        if (Object.prototype.hasOwnProperty.call(value, 'S')) return value.S;
        if (Object.prototype.hasOwnProperty.call(value, 'N')) return value.N;
        if (Object.prototype.hasOwnProperty.call(value, 'BOOL')) return value.BOOL;
        if (Object.prototype.hasOwnProperty.call(value, 'L')) return value.L.map(item => unwrapValue(item, ''));
        if (Object.prototype.hasOwnProperty.call(value, 'SS')) return value.SS;
        return value;
    }

    function normalizeMeetingPayload(payload) {
        if (Array.isArray(payload)) return payload;
        if (Array.isArray(payload?.items)) return payload.items;
        if (Array.isArray(payload?.application_workspaces)) return payload.application_workspaces;
        if (Array.isArray(payload?.upcoming_interviews)) return payload.upcoming_interviews;
        if (Array.isArray(payload?.meetings)) return payload.meetings;
        if (Array.isArray(payload?.data)) return payload.data;
        return [];
    }

    function meetingId(meeting, index) {
        return String(
            unwrapValue(meeting?.meeting_id, '') ||
            unwrapValue(meeting?.transcript_id, '') ||
            unwrapValue(meeting?.id, '') ||
            unwrapValue(meeting?.timestamp, '') ||
            `meeting-${index}`
        );
    }

    function meetingName(meeting, index) {
        return String(
            unwrapValue(meeting?.meeting_name, '') ||
            unwrapValue(meeting?.title, '') ||
            `Application Workspace ${index + 1}`
        );
    }

    function meetingDate(meeting) {
        const raw = unwrapValue(meeting?.timestamp, '') || unwrapValue(meeting?.date, '') || unwrapValue(meeting?.created_at, '');
        if (!raw) return '';
        const parsed = new Date(raw);
        return Number.isNaN(parsed.getTime()) ? String(raw) : parsed.toLocaleDateString(window.AppI18n?.locale || undefined);
    }

    function meetingParticipants(meeting) {
        const raw = unwrapValue(meeting?.participants, []) || unwrapValue(meeting?.attendees, []) || unwrapValue(meeting?.speakers, []);
        const values = Array.isArray(raw) ? raw : String(raw || '').split(',');
        return values
            .map(value => {
                const normalized = unwrapValue(value, '');
                if (normalized && typeof normalized === 'object') {
                    return unwrapValue(normalized.name, '') || unwrapValue(normalized.full_name, '') || unwrapValue(normalized.email, '');
                }
                return String(normalized || '');
            })
            .map(value => value.trim())
            .filter(Boolean);
    }

    function normalizeUpcomingMeetings(value) {
        const records = Array.isArray(value) ? value : [];
        return records
            .filter(record => record && typeof record === 'object' && String(record.id || '').trim())
            .map(record => ({
                id: String(record.id),
                title: String(record.title || 'Untitled application').trim() || 'Untitled application',
                scheduled_at: String(record.scheduled_at || ''),
                participants: Array.isArray(record.participants)
                    ? record.participants.map(item => String(item || '').trim()).filter(Boolean)
                    : String(record.participants || '').split(',').map(item => item.trim()).filter(Boolean),
                purpose: String(record.purpose || ''),
                status: String(record.status || (record.scheduled_at ? 'upcoming' : 'draft')),
                created_at: String(record.created_at || ''),
                updated_at: String(record.updated_at || ''),
                completed_at: String(record.completed_at || ''),
                completed_meeting_id: String(record.completed_meeting_id || '')
            }));
    }

    function activeUpcomingMeetings() {
        return state.upcomingMeetings.filter(meeting => !['completed', 'cancelled'].includes(meeting.status));
    }

    function upcomingMeetingLabel(meeting) {
        const scheduled = meeting.scheduled_at ? new Date(meeting.scheduled_at) : null;
        const date = scheduled && !Number.isNaN(scheduled.getTime())
            ? scheduled.toLocaleString(window.AppI18n?.locale || undefined, {dateStyle: 'medium', timeStyle: 'short'})
            : '';
        const status = meeting.status === 'draft' ? 'Draft' : '';
        return [meeting.title, date || status].filter(Boolean).join(' · ');
    }

    function updateDeleteMeetingButton() {
        const button = document.getElementById('deleteUpcomingMeeting');
        if (!button) return;
        const selectedId = document.getElementById('meetingMaterialsMeeting')?.value || '';
        const meeting = activeUpcomingMeetings().find(item => item.id === selectedId);
        const deleting = Boolean(state.pendingMeetingDeleteId);
        const disabled = !meeting || deleting;
        const label = deleting
            ? 'Deleting application workspace'
            : meeting
                ? `Delete ${meeting.title}`
                : 'Select an upcoming interview to delete';
        button.disabled = disabled;
        button.setAttribute('aria-disabled', String(disabled));
        button.setAttribute('aria-busy', String(deleting));
        button.setAttribute('aria-label', label);
        button.title = label;
        const hiddenLabel = button.querySelector('.sr-only');
        if (hiddenLabel) hiddenLabel.textContent = label;
    }

    function populateUpcomingMeetingSelects(preferredId = '') {
        const meetings = activeUpcomingMeetings();
        document.querySelectorAll('[data-upcoming-meeting-select]').forEach(select => {
            const previousValue = preferredId || select.value;
            const defaultLabel = select.id === 'contextMeetingSelect'
                ? 'No upcoming interview selected'
                : 'Select an upcoming interview';
            const firstOption = new Option(defaultLabel, '');
            select.replaceChildren(firstOption);
            meetings.forEach(meeting => select.add(new Option(upcomingMeetingLabel(meeting), meeting.id)));
            select.disabled = meetings.length === 0;
            if (!meetings.length) {
                firstOption.textContent = 'No upcoming interviews yet';
            }
            if (previousValue && meetings.some(meeting => meeting.id === previousValue)) {
                select.value = previousValue;
            }
        });
        updateDeleteMeetingButton();
        renderUpcomingMeetingDetails(document.getElementById('meetingMaterialsMeeting')?.value || '');
    }

    async function loadCompletedMeetings() {
        const meetingSelects = Array.from(document.querySelectorAll('[data-completed-meeting-select]'));
        const participantSelects = Array.from(document.querySelectorAll('[data-participant-select]'));
        if (!meetingSelects.length && !participantSelects.length) return;

        try {
            const response = await fetch(endpoints.transcripts, {headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error(`Application workspace endpoint returned ${response.status}.`);
            state.completedMeetings = normalizeMeetingPayload(await response.json());
        } catch (error) {
            console.info('Completed mock interview list could not be loaded.', error);
            state.completedMeetings = [];
        }

        const participants = new Set();
        state.completedMeetings.forEach(meeting => meetingParticipants(meeting).forEach(value => participants.add(value)));

        meetingSelects.forEach(select => {
            const previousValue = select.value;
            const firstOption = select.options[0]?.cloneNode(true) || new Option('All completed mock interviews', '');
            select.replaceChildren(firstOption);
            state.completedMeetings.forEach((meeting, index) => {
                const id = meetingId(meeting, index);
                const date = meetingDate(meeting);
                select.add(new Option(`${meetingName(meeting, index)}${date ? ` · ${date}` : ''}`, id));
            });
            if (previousValue && Array.from(select.options).some(option => option.value === previousValue)) {
                select.value = previousValue;
            }
        });

        participantSelects.forEach(select => {
            const firstOption = select.options[0]?.cloneNode(true) || new Option('All interviewers and contacts', '');
            select.replaceChildren(firstOption);
            Array.from(participants).sort((a, b) => a.localeCompare(b)).forEach(name => select.add(new Option(name, name)));
        });
    }

    function createUpcomingMeetingId() {
        return window.crypto?.randomUUID?.() || `upcoming-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function saveUpcomingMeetings() {
        writeJsonStorage(upcomingMeetingsStorageKey, state.upcomingMeetings);
    }

    async function setActiveMeeting(meetingId) {
        try {
            await fetch(endpoints.activeMeeting, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({application_workspace_id: meetingId || '', meeting_id: meetingId || ''})
            });
        } catch (error) {
            console.info('Active application workspace could not be synchronized.', error);
        }
    }

    async function loadUpcomingMeetingsFromServer() {
        const localMeetings = normalizeUpcomingMeetings(readJsonStorage(upcomingMeetingsStorageKey, []));
        try {
            let response = await fetch(endpoints.meetings, {headers: {'Accept': 'application/json'}});
            if (!response.ok) throw new Error(`Application workspace endpoint returned ${response.status}.`);
            let result = await response.json();
            let serverMeetings = normalizeUpcomingMeetings(result.meetings || []);

            // Migrate packages created by the previous browser-only implementation.
            if (localMeetings.length) {
                const serverIds = new Set(serverMeetings.map(meeting => meeting.id));
                for (const meeting of localMeetings) {
                    if (serverIds.has(meeting.id)) continue;
                    const created = await fetch(endpoints.meetings, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...meeting, activate: false})
                    });
                    if (!created.ok) continue;
                    const localMaterials = state.materials[meeting.id];
                    if (localMaterials) {
                        await fetch(endpoints.materials, {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({meeting_id: meeting.id, ...localMaterials, activate: false})
                        });
                    }
                    const localContext = state.meetingContexts[meeting.id];
                    if (localContext) {
                        await fetch(`${endpoints.meetings}/${encodeURIComponent(meeting.id)}/context`, {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(localContext)
                        });
                    }
                }
                response = await fetch(endpoints.meetings, {headers: {'Accept': 'application/json'}});
                result = response.ok ? await response.json() : result;
                serverMeetings = normalizeUpcomingMeetings(result.meetings || []);
            }

            state.upcomingMeetings = serverMeetings;
            saveUpcomingMeetings();
            let activeMeetingId = result.active_application_workspace_id || result.active_meeting_id || '';
            if (!activeMeetingId && localMeetings.length) {
                const preparedLocal = [...localMeetings]
                    .filter(meeting => (state.materials[meeting.id]?.library_file_ids || []).length)
                    .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))[0];
                if (preparedLocal && serverMeetings.some(meeting => meeting.id === preparedLocal.id)) {
                    activeMeetingId = preparedLocal.id;
                    await setActiveMeeting(activeMeetingId);
                }
            }
            populateUpcomingMeetingSelects(activeMeetingId);
            return activeMeetingId;
        } catch (error) {
            console.info('Upcoming interviews endpoint unavailable; using browser storage.', error);
            state.upcomingMeetings = localMeetings;
            populateUpcomingMeetingSelects();
            return '';
        }
    }

    function getFocusable(modal) {
        return Array.from(modal.querySelectorAll(
            'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )).filter(element => !element.hidden && element.offsetParent !== null);
    }

    function openModal(modal, trigger) {
        if (!modal) return;
        state.activeModal = modal;
        state.modalTrigger = trigger || document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        root.inert = true;
        if (trigger?.hasAttribute('aria-expanded')) trigger.setAttribute('aria-expanded', 'true');
        const focusTarget = getFocusable(modal)[0] || modal.querySelector('[role="dialog"]');
        window.requestAnimationFrame(() => focusTarget?.focus());
    }

    function closeModal(modal) {
        if (!modal) return;
        modal.hidden = true;
        document.body.style.overflow = '';
        root.inert = false;
        if (state.modalTrigger?.hasAttribute?.('aria-expanded')) state.modalTrigger.setAttribute('aria-expanded', 'false');
        state.modalTrigger?.focus?.();
        state.activeModal = null;
        state.modalTrigger = null;
    }

    function trapModalFocus(event) {
        if (event.key !== 'Tab' || !state.activeModal) return;
        const focusable = getFocusable(state.activeModal);
        if (!focusable.length) return;
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

    document.querySelectorAll('[data-close-modal]').forEach(button => {
        button.addEventListener('click', () => closeModal(button.closest('.modal-backdrop')));
    });
    document.querySelectorAll('.modal-backdrop').forEach(modal => {
        modal.addEventListener('mousedown', event => {
            if (event.target === modal) closeModal(modal);
        });
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.activeModal) closeModal(state.activeModal);
        trapModalFocus(event);
    });

    function parseParticipantValues(value) {
        const source = Array.isArray(value) ? value : [value];
        const seen = new Set();
        const participants = [];
        source.forEach(item => {
            String(item || '')
                .split(/[,;\n]+/)
                .map(name => name.trim())
                .filter(Boolean)
                .forEach(name => {
                    const normalized = name.slice(0, 200);
                    const key = normalized.toLocaleLowerCase();
                    if (!normalized || seen.has(key) || participants.length >= 100) return;
                    seen.add(key);
                    participants.push(normalized);
                });
        });
        return participants;
    }

    function createParticipantEditor(rootElement) {
        if (!rootElement) return null;
        const valueInput = document.getElementById(rootElement.dataset.valueInput || '');
        const countElement = document.getElementById(rootElement.dataset.countTarget || '');
        const listElement = rootElement.querySelector('[data-participant-list]');
        const entryInput = rootElement.querySelector('.participant-chip-entry');
        if (!valueInput || !listElement || !entryInput) return null;

        let participants = [];
        let disabled = Boolean(valueInput.disabled || entryInput.disabled || rootElement.classList.contains('is-disabled'));

        function currentValues(includePending = true) {
            const pending = includePending ? entryInput.value.trim() : '';
            return parseParticipantValues(pending ? [...participants, pending] : participants);
        }

        function updateCount() {
            const count = currentValues(true).length;
            if (countElement) {
                countElement.textContent = String(count);
                const countContainer = countElement.closest('.participant-total');
                if (countContainer) countContainer.setAttribute('aria-label', `Participant count: ${count}`);
            }
        }

        function syncValue(dispatch = true) {
            valueInput.value = currentValues(true).join(', ');
            updateCount();
            if (dispatch) valueInput.dispatchEvent(new Event('input', {bubbles: true}));
        }

        function render(dispatch = false) {
            listElement.replaceChildren();
            participants.forEach((name, index) => {
                const chip = document.createElement('span');
                chip.className = 'participant-chip';

                const chipName = document.createElement('span');
                chipName.className = 'participant-chip-name';
                chipName.dataset.i18nSkip = '';
                chipName.textContent = name;

                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'participant-chip-remove';
                removeButton.textContent = '×';
                removeButton.disabled = disabled;
                removeButton.setAttribute('aria-label', `Remove ${name}`);
                removeButton.addEventListener('click', () => {
                    if (disabled) return;
                    participants.splice(index, 1);
                    render(true);
                    entryInput.focus();
                });

                chip.append(chipName, removeButton);
                listElement.appendChild(chip);
            });
            rootElement.classList.toggle('is-disabled', disabled);
            entryInput.disabled = disabled;
            valueInput.disabled = disabled;
            syncValue(dispatch);
        }

        function commitPending(dispatch = true) {
            const additions = parseParticipantValues(entryInput.value);
            if (!additions.length) {
                entryInput.value = '';
                syncValue(dispatch);
                return false;
            }
            participants = parseParticipantValues([...participants, ...additions]);
            entryInput.value = '';
            render(dispatch);
            return true;
        }

        entryInput.addEventListener('input', () => {
            if (/[,;\n]/.test(entryInput.value)) commitPending(true);
            else syncValue(true);
        });

        entryInput.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ',' || event.key === ';') {
                event.preventDefault();
                commitPending(true);
                return;
            }
            if (event.key === 'Tab' && entryInput.value.trim()) {
                commitPending(true);
                return;
            }
            if (event.key === 'Backspace' && !entryInput.value && participants.length) {
                participants.pop();
                render(true);
            }
        });

        entryInput.addEventListener('paste', event => {
            const pastedText = event.clipboardData?.getData('text') || '';
            if (!/[,;\n]/.test(pastedText)) return;
            event.preventDefault();
            participants = parseParticipantValues([...participants, pastedText]);
            entryInput.value = '';
            render(true);
        });

        entryInput.addEventListener('blur', () => {
            if (entryInput.value.trim()) commitPending(true);
        });

        rootElement.addEventListener('click', event => {
            if (disabled || event.target.closest('.participant-chip-remove')) return;
            entryInput.focus();
        });

        render(false);
        return {
            getValues: () => currentValues(true),
            setValues(values, {dispatch = false} = {}) {
                participants = parseParticipantValues(values);
                entryInput.value = '';
                render(dispatch);
            },
            clear(options = {}) {
                this.setValues([], options);
            },
            setDisabled(value) {
                disabled = Boolean(value);
                render(false);
            },
            focus() {
                entryInput.focus();
            }
        };
    }

    // Upcoming interviews used by Application Materials and application-specific career profile.
    const upcomingMeetingModal = document.getElementById('upcomingMeetingModal');
    const meetingDetailsTitleInput = document.getElementById('meetingDetailsTitleInput');
    const meetingDetailsDateInput = document.getElementById('meetingDetailsDate');
    const meetingDetailsParticipantsInput = document.getElementById('meetingDetailsParticipants');
    const meetingDetailsParticipantsEditor = createParticipantEditor(document.getElementById('meetingDetailsParticipantEditor'));
    const upcomingMeetingParticipantsEditor = createParticipantEditor(document.getElementById('upcomingMeetingParticipantEditor'));
    const meetingDetailsPurposeInput = document.getElementById('meetingDetailsPurpose');
    const meetingDetailsStatus = document.getElementById('meetingDetailsStatus');
    const saveUpcomingMeetingDetailsButton = document.getElementById('saveUpcomingMeetingDetails');
    const meetingDetailsInputs = [
        meetingDetailsTitleInput,
        meetingDetailsDateInput,
        meetingDetailsParticipantsInput,
        meetingDetailsPurposeInput
    ].filter(Boolean);
    let savedUpcomingMeetingDetailsSnapshot = null;
    let isSavingUpcomingMeetingDetails = false;

    function selectedUpcomingMeeting(meetingId = '') {
        const selectedId = meetingId || document.getElementById('meetingMaterialsMeeting')?.value || '';
        return activeUpcomingMeetings().find(meeting => meeting.id === selectedId) || null;
    }

    function dateTimeLocalValue(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        const localDate = new Date(date.getTime() - (date.getTimezoneOffset() * 60_000));
        return localDate.toISOString().slice(0, 16);
    }

    function readUpcomingMeetingDetailsForm() {
        const scheduledValue = meetingDetailsDateInput?.value || '';
        const scheduledDate = scheduledValue ? new Date(scheduledValue) : null;
        const scheduledAt = scheduledDate && !Number.isNaN(scheduledDate.getTime())
            ? scheduledDate.toISOString()
            : '';
        return {
            title: meetingDetailsTitleInput?.value.trim() || '',
            scheduled_at: scheduledAt,
            participants: meetingDetailsParticipantsEditor?.getValues()
                || parseParticipantValues(meetingDetailsParticipantsInput?.value || ''),
            purpose: meetingDetailsPurposeInput?.value.trim() || '',
            status: scheduledAt ? 'upcoming' : 'draft'
        };
    }

    function upcomingMeetingDetailsSnapshot() {
        const details = readUpcomingMeetingDetailsForm();
        return JSON.stringify({
            ...details,
            participants: [...details.participants].map(String)
        });
    }

    function upcomingMeetingDetailsHaveUnsavedChanges() {
        return Boolean(
            selectedUpcomingMeeting() &&
            savedUpcomingMeetingDetailsSnapshot !== null &&
            upcomingMeetingDetailsSnapshot() !== savedUpcomingMeetingDetailsSnapshot
        );
    }

    function updateUpcomingMeetingDetailsSaveState() {
        if (!saveUpcomingMeetingDetailsButton) return;
        const details = readUpcomingMeetingDetailsForm();
        const disabled = Boolean(
            isSavingUpcomingMeetingDetails ||
            !selectedUpcomingMeeting() ||
            !details.title ||
            !upcomingMeetingDetailsHaveUnsavedChanges()
        );
        saveUpcomingMeetingDetailsButton.disabled = disabled;
        saveUpcomingMeetingDetailsButton.setAttribute('aria-disabled', String(disabled));
    }

    function renderUpcomingMeetingDetails(meetingId = '') {
        if (!meetingDetailsInputs.length) return;
        const meeting = selectedUpcomingMeeting(meetingId);
        meetingDetailsInputs.forEach(input => { input.disabled = !meeting; });
        meetingDetailsParticipantsEditor?.setDisabled(!meeting);
        if (!meeting) {
            if (meetingDetailsTitleInput) meetingDetailsTitleInput.value = '';
            if (meetingDetailsDateInput) meetingDetailsDateInput.value = '';
            meetingDetailsParticipantsEditor?.clear({dispatch: false});
            if (meetingDetailsPurposeInput) meetingDetailsPurposeInput.value = '';
            savedUpcomingMeetingDetailsSnapshot = null;
            if (meetingDetailsStatus) {
                meetingDetailsStatus.textContent = 'Select a job application to view its target role, notes, interviewer contacts, and interview date.';
            }
            updateUpcomingMeetingDetailsSaveState();
            return;
        }

        if (meetingDetailsTitleInput) meetingDetailsTitleInput.value = meeting.title || '';
        if (meetingDetailsDateInput) meetingDetailsDateInput.value = dateTimeLocalValue(meeting.scheduled_at);
        meetingDetailsParticipantsEditor?.setValues(meeting.participants || [], {dispatch: false});
        if (meetingDetailsPurposeInput) meetingDetailsPurposeInput.value = meeting.purpose || '';
        savedUpcomingMeetingDetailsSnapshot = upcomingMeetingDetailsSnapshot();
        if (meetingDetailsStatus) {
            meetingDetailsStatus.textContent = `Saved details for “${meeting.title}”. Edit any field below to update this application.`;
        }
        updateUpcomingMeetingDetailsSaveState();
    }

    meetingDetailsInputs.forEach(input => {
        input.addEventListener('input', () => {
            const meeting = selectedUpcomingMeeting();
            if (meetingDetailsStatus && meeting) {
                meetingDetailsStatus.textContent = upcomingMeetingDetailsHaveUnsavedChanges()
                    ? 'You have unsaved application detail changes.'
                    : `Saved details for “${meeting.title}”. Edit any field below to update this application.`;
            }
            updateUpcomingMeetingDetailsSaveState();
        });
    });
    document.querySelectorAll('[data-open-upcoming-meeting-modal]').forEach(button => {
        button.addEventListener('click', event => {
            document.getElementById('upcomingMeetingForm')?.reset();
            upcomingMeetingParticipantsEditor?.clear({dispatch: false});
            openModal(upcomingMeetingModal, event.currentTarget);
        });
    });

    document.getElementById('upcomingMeetingForm')?.addEventListener('submit', async event => {
        event.preventDefault();
        const title = document.getElementById('upcomingMeetingTitle')?.value.trim() || '';
        if (!title) {
            showToast('Enter a target role or application name.', true);
            document.getElementById('upcomingMeetingTitle')?.focus();
            return;
        }

        const scheduledValue = document.getElementById('upcomingMeetingDate')?.value || '';
        const scheduledDate = scheduledValue ? new Date(scheduledValue) : null;
        const scheduledAt = scheduledDate && !Number.isNaN(scheduledDate.getTime())
            ? scheduledDate.toISOString()
            : '';
        const now = new Date().toISOString();
        const meeting = {
            id: createUpcomingMeetingId(),
            title,
            scheduled_at: scheduledAt,
            participants: upcomingMeetingParticipantsEditor?.getValues()
                || parseParticipantValues(document.getElementById('upcomingMeetingParticipants')?.value || ''),
            purpose: document.getElementById('upcomingMeetingPurpose')?.value.trim() || '',
            status: scheduledAt ? 'upcoming' : 'draft',
            created_at: now,
            updated_at: now,
            completed_at: '',
            completed_meeting_id: ''
        };
        try {
            const response = await fetch(endpoints.meetings, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...meeting, activate: true})
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The application workspace could not be created.');
            Object.assign(meeting, result.meeting || {});
        } catch (error) {
            console.info('Creating application workspace in browser storage.', error);
        }
        state.upcomingMeetings = state.upcomingMeetings.filter(item => item.id !== meeting.id);
        state.upcomingMeetings.push(meeting);
        saveUpcomingMeetings();
        populateUpcomingMeetingSelects(meeting.id);
        closeModal(upcomingMeetingModal);

        if (materialsMeeting) {
            materialsMeeting.value = meeting.id;
            loadMeetingMaterials();
        }
        const contextMeeting = document.getElementById('contextMeetingSelect');
        if (contextMeeting) {
            contextMeeting.value = meeting.id;
            writeMeetingContextForm({});
            updateEffectiveContextPreview();
        }
        showToast(scheduledAt ? 'Upcoming interview created.' : 'Draft application workspace created.');
    });

    saveUpcomingMeetingDetailsButton?.addEventListener('click', async () => {
        const meeting = selectedUpcomingMeeting();
        if (!meeting || isSavingUpcomingMeetingDetails) return;

        const details = readUpcomingMeetingDetailsForm();
        if (!details.title) {
            showToast('Enter a target role or application name.', true);
            meetingDetailsTitleInput?.focus();
            return;
        }
        if (!upcomingMeetingDetailsHaveUnsavedChanges()) return;

        isSavingUpcomingMeetingDetails = true;
        saveUpcomingMeetingDetailsButton.textContent = 'Saving…';
        updateUpcomingMeetingDetailsSaveState();
        try {
            const response = await fetch(`${endpoints.meetings}/${encodeURIComponent(meeting.id)}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...details, activate: true})
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The application details could not be saved.');

            const savedMeeting = {
                ...meeting,
                ...details,
                ...(result.meeting || {}),
                updated_at: result.meeting?.updated_at || new Date().toISOString()
            };
            state.upcomingMeetings = state.upcomingMeetings.map(item =>
                item.id === meeting.id ? normalizeUpcomingMeetings([savedMeeting])[0] : item
            );
            saveUpcomingMeetings();
            populateUpcomingMeetingSelects(meeting.id);
            showToast('Application details saved.');
        } catch (error) {
            console.info('Upcoming interview details could not be saved.', error);
            showToast(error.message || 'The application details could not be saved.', true);
        } finally {
            isSavingUpcomingMeetingDetails = false;
            saveUpcomingMeetingDetailsButton.textContent = 'Save application details';
            updateUpcomingMeetingDetailsSaveState();
        }
    });

    document.getElementById('deleteUpcomingMeeting')?.addEventListener('click', async event => {
        const button = event.currentTarget;
        const selectedId = materialsMeeting?.value || '';
        const meeting = activeUpcomingMeetings().find(item => item.id === selectedId);
        if (!meeting || state.pendingMeetingDeleteId) {
            updateDeleteMeetingButton();
            return;
        }

        const confirmed = window.AppUI?.confirm
            ? await window.AppUI.confirm({
                title: 'Delete upcoming interview?',
                message: `Delete “${meeting.title}”? Its selected materials, application-specific context, and temporary files will also be removed. This action cannot be undone.`,
                confirmLabel: 'Delete application',
                danger: true
            })
            : window.confirm(window.AppI18n?.t(`Delete “${meeting.title}”? This action cannot be undone.`) || `Delete “${meeting.title}”? This action cannot be undone.`);
        if (!confirmed) return;

        state.pendingMeetingDeleteId = selectedId;
        updateDeleteMeetingButton();

        try {
            const response = await fetch(`${endpoints.meetings}/${encodeURIComponent(selectedId)}`, {
                method: 'DELETE',
                headers: {'Accept': 'application/json'}
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(result.error || 'The application workspace could not be deleted.');
            }

            state.upcomingMeetings = state.upcomingMeetings.filter(item => item.id !== selectedId);
            delete state.materials[selectedId];
            delete state.meetingContexts[selectedId];
            delete savedMeetingContextSnapshots[selectedId];
            saveUpcomingMeetings();
            writeJsonStorage(meetingMaterialsStorageKey, state.materials);
            writeJsonStorage(meetingContextStorageKey, state.meetingContexts);

            populateUpcomingMeetingSelects();
            if (materialsMeeting) {
                materialsMeeting.value = '';
                await loadMeetingMaterials();
            }
            const contextMeeting = document.getElementById('contextMeetingSelect');
            if (contextMeeting?.value === selectedId || activeContextMeetingId === selectedId) {
                contextMeeting.value = '';
                activeContextMeetingId = '';
                writeMeetingContextForm({});
                updateEffectiveContextPreview();
                updateSaveContextButton();
            }
            showToast('Upcoming interview deleted.');
        } catch (error) {
            showToast(error.message, true);
        } finally {
            state.pendingMeetingDeleteId = null;
            updateDeleteMeetingButton();
        }
    });

    // Document Library
    const uploadModal = document.getElementById('uploadModal');
    const collectionModal = document.getElementById('collectionModal');
    const fileInput = document.getElementById('fileInput');
    const selectedFiles = document.getElementById('selectedFiles');
    const filesTableBody = document.getElementById('filesTableBody');
    const fileSearch = document.getElementById('fileSearch');
    const emptyState = document.getElementById('emptyState');
    const collectionList = document.getElementById('collectionList');

    document.getElementById('openUploadModal')?.addEventListener('click', event => openModal(uploadModal, event.currentTarget));
    document.querySelectorAll('[data-open-upload]').forEach(button => button.addEventListener('click', event => openModal(uploadModal, event.currentTarget)));
    document.getElementById('openCollectionModal')?.addEventListener('click', event => openModal(collectionModal, event.currentTarget));
    document.getElementById('newCollectionButton')?.addEventListener('click', event => openModal(collectionModal, event.currentTarget));

    function updateSelectedFiles() {
        if (!fileInput || !selectedFiles) return;
        const files = Array.from(fileInput.files || []);
        selectedFiles.replaceChildren();
        selectedFiles.hidden = files.length === 0;
        files.forEach(file => {
            const row = document.createElement('div');
            row.className = 'selected-file-row';
            row.innerHTML = `<span>${escapeHtml(file.name)}</span><span>${formatBytes(file.size)}</span>`;
            selectedFiles.appendChild(row);
        });
    }

    fileInput?.addEventListener('change', updateSelectedFiles);

    const dropZone = document.getElementById('dropZone');
    ['dragenter', 'dragover'].forEach(type => dropZone?.addEventListener(type, event => {
        event.preventDefault();
        dropZone.classList.add('dragging');
    }));
    ['dragleave', 'drop'].forEach(type => dropZone?.addEventListener(type, event => {
        event.preventDefault();
        dropZone.classList.remove('dragging');
    }));
    dropZone?.addEventListener('drop', event => {
        if (!fileInput || !event.dataTransfer?.files?.length) return;
        const transfer = new DataTransfer();
        Array.from(event.dataTransfer.files).forEach(file => transfer.items.add(file));
        fileInput.files = transfer.files;
        updateSelectedFiles();
    });

    document.getElementById('uploadForm')?.addEventListener('submit', async event => {
        event.preventDefault();
        const files = Array.from(fileInput?.files || []);
        if (!files.length) {
            showToast('Choose at least one file to upload.', true);
            return;
        }
        const button = document.getElementById('uploadButton');
        const formData = new FormData(event.currentTarget);
        button.disabled = true;
        button.textContent = 'Uploading…';
        try {
            const response = await fetch(endpoints.upload, {method: 'POST', body: formData});
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The files could not be uploaded.');
            showToast(`${files.length} file${files.length === 1 ? '' : 's'} added to Career Evidence Library.`);
            closeModal(uploadModal);
            window.location.reload();
        } catch (error) {
            showToast(error.message, true);
        } finally {
            button.disabled = false;
            button.textContent = 'Upload Files';
        }
    });

    document.getElementById('collectionForm')?.addEventListener('submit', async event => {
        event.preventDefault();
        const payload = {
            name: document.getElementById('collectionName')?.value.trim() || '',
            description: document.getElementById('collectionDescription')?.value.trim() || ''
        };
        try {
            const response = await fetch(endpoints.collections, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The collection could not be created.');
            showToast('Collection created.');
            closeModal(collectionModal);
            window.location.reload();
        } catch (error) {
            showToast(error.message, true);
        }
    });

    function setCollectionFileCount(collectionId, nextCount) {
        if (!collectionId || collectionId === 'uncategorized') return;
        const row = Array.from(document.querySelectorAll('.collection-row')).find(
            item => item.dataset.collectionId === collectionId
        );
        if (!row) return;

        const count = Math.max(0, Number(nextCount || 0));
        row.dataset.fileCount = String(count);
        const countBadge = row.querySelector('.collection-count');
        if (countBadge) countBadge.textContent = String(count);

        const deleteButton = row.querySelector('[data-delete-collection]');
        if (deleteButton) {
            deleteButton.disabled = count > 0;
            deleteButton.title = count > 0
                ? 'Delete all files in this collection first'
                : 'Delete collection';
        }
    }

    function adjustCollectionFileCount(collectionId, delta) {
        if (!collectionId || collectionId === 'uncategorized') return;
        const row = Array.from(document.querySelectorAll('.collection-row')).find(
            item => item.dataset.collectionId === collectionId
        );
        if (!row) return;
        setCollectionFileCount(collectionId, Number(row.dataset.fileCount || 0) + delta);
    }

    function removeCollectionOptions(collectionId) {
        ['uploadCollection', 'knowledgeSearchCollection'].forEach(selectId => {
            const select = document.getElementById(selectId);
            Array.from(select?.options || []).forEach(option => {
                if (option.value === collectionId) option.remove();
            });
        });
    }

    collectionList?.addEventListener('click', async event => {
        const deleteButton = event.target.closest('[data-delete-collection]');
        if (!deleteButton) return;

        event.preventDefault();
        event.stopPropagation();

        const row = deleteButton.closest('.collection-row');
        const collectionId = row?.dataset.collectionId || '';
        const collectionName = row?.querySelector('.collection-name')?.textContent?.trim() || 'this collection';
        const fileCount = Number(row?.dataset.fileCount || 0);
        if (!collectionId || state.pendingCollectionDeleteId) return;
        if (fileCount > 0) {
            showToast('Delete all files in this collection before deleting the collection.', true);
            return;
        }

        state.pendingCollectionDeleteId = collectionId;
        try {
            const confirmed = window.AppUI?.confirm
                ? await window.AppUI.confirm({
                    title: 'Delete collection?',
                    message: `Delete “${collectionName}”? This removes the empty collection only.`,
                    confirmLabel: 'Delete collection',
                    danger: true
                })
                : window.confirm(window.AppI18n?.t(`Delete the empty collection “${collectionName}”?`) || `Delete the empty collection “${collectionName}”?`);
            if (!confirmed) return;

            deleteButton.disabled = true;
            deleteButton.setAttribute('aria-busy', 'true');
            const response = await fetch(`${endpoints.collections}/${encodeURIComponent(collectionId)}`, {
                method: 'DELETE',
                headers: {'Accept': 'application/json'}
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The collection could not be deleted.');

            const wasSelected = state.selectedCollectionId === collectionId;
            row.remove();
            removeCollectionOptions(collectionId);

            if (wasSelected) {
                const allFilesButton = collectionList.querySelector('.collection-item[data-collection-id="all"]');
                if (allFilesButton) selectCollection(allFilesButton);
            }
            showToast('Collection deleted.');
        } catch (error) {
            deleteButton.disabled = false;
            showToast(error.message, true);
        } finally {
            deleteButton.removeAttribute('aria-busy');
            state.pendingCollectionDeleteId = null;
        }
    });

    function syncCollectionCounts() {
        const rows = Array.from(filesTableBody?.querySelectorAll('tr') || []);
        const counts = new Map();
        rows.forEach(row => {
            const collectionId = row.dataset.collectionId || 'uncategorized';
            counts.set(collectionId, (counts.get(collectionId) || 0) + 1);
        });

        const allFilesCount = document.getElementById('allFilesCount');
        if (allFilesCount) allFilesCount.textContent = String(rows.length);

        document.querySelectorAll('.collection-row[data-collection-id]').forEach(row => {
            const collectionId = row.dataset.collectionId || '';
            const fileCount = counts.get(collectionId) || 0;
            row.dataset.fileCount = String(fileCount);
            const countElement = row.querySelector('.collection-count');
            if (countElement) countElement.textContent = String(fileCount);

            const deleteButton = row.querySelector('[data-delete-collection]');
            if (deleteButton) {
                deleteButton.disabled = fileCount > 0;
                deleteButton.title = fileCount > 0
                    ? 'Delete all files in this collection first'
                    : 'Delete collection';
            }
        });
    }

    function filterLibraryFiles() {
        if (!filesTableBody || !fileSearch || !emptyState) return;
        const searchTerm = fileSearch.value.trim().toLowerCase();
        const rows = Array.from(filesTableBody.querySelectorAll('tr'));
        let visibleCount = 0;
        rows.forEach(row => {
            const matchesCollection = state.selectedCollectionId === 'all' || row.dataset.collectionId === state.selectedCollectionId;
            const matchesSearch = !searchTerm || (row.dataset.filename || '').includes(searchTerm);
            const visible = matchesCollection && matchesSearch;
            row.hidden = !visible;
            if (visible) visibleCount += 1;
        });
        emptyState.hidden = visibleCount > 0;
        syncCollectionCounts();
    }

    function selectCollection(button) {
        document.querySelectorAll('.collection-item').forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        state.selectedCollectionId = button.dataset.collectionId || 'all';
        state.selectedCollectionName = button.querySelector('.collection-name')?.textContent?.trim() || 'All Files';
        const description = document.getElementById('selectedCollectionDescription');
        if (description) {
            description.textContent = state.selectedCollectionId === 'all'
                ? 'All permanent files in your document library'
                : `Permanent files in ${state.selectedCollectionName}`;
        }
        filterLibraryFiles();
    }

    document.querySelectorAll('.collection-item').forEach(button => button.addEventListener('click', () => selectCollection(button)));
    fileSearch?.addEventListener('input', window.AppUI?.debounce(filterLibraryFiles, 180) || filterLibraryFiles);

    document.addEventListener('click', async event => {
        const menuButton = event.target.closest('[data-file-menu-button]');
        document.querySelectorAll('.file-menu').forEach(menu => {
            if (!menuButton || menu !== menuButton.nextElementSibling) {
                menu.hidden = true;
                menu.previousElementSibling?.setAttribute('aria-expanded', 'false');
            }
        });
        if (menuButton) {
            const menu = menuButton.nextElementSibling;
            menu.hidden = !menu.hidden;
            menuButton.setAttribute('aria-expanded', String(!menu.hidden));
            event.stopPropagation();
            return;
        }

        const actionButton = event.target.closest('[data-file-action]');
        if (!actionButton) return;
        const row = actionButton.closest('tr');
        const id = row?.dataset.fileId;
        if (!id) return;
        const fileBase = `${endpoints.files}/${encodeURIComponent(id)}`;
        const action = actionButton.dataset.fileAction;
        if (action === 'download') window.location.href = `${fileBase}/download`;
        if (action === 'preview') window.open(`${fileBase}/preview`, '_blank', 'noopener');
        if (action === 'delete') {
            const confirmed = window.AppUI?.confirm
                ? await window.AppUI.confirm({
                    title: 'Delete document?',
                    message: 'This permanent document will be removed from Career Evidence Library and from any application workspace that references it.',
                    confirmLabel: 'Delete document',
                    danger: true
                })
                : window.confirm(window.AppI18n?.t('Delete this document?') || 'Delete this document?');
            if (!confirmed) return;
            try {
                const response = await fetch(fileBase, {method: 'DELETE'});
                const result = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(result.error || 'The document could not be deleted.');
                const deletedCollectionId = row.dataset.collectionId || '';
                row.remove();
                adjustCollectionFileCount(deletedCollectionId, -1);
                filterLibraryFiles();
                showToast('Document deleted.');
            } catch (error) {
                showToast(error.message, true);
            }
        }
    });

    // Application Materials
    const materialsMeeting = document.getElementById('meetingMaterialsMeeting');
    const materialsCheckboxes = Array.from(document.querySelectorAll('.material-library-checkbox'));
    const materialsSearch = document.getElementById('materialsLibrarySearch');
    const materialsSelectedOnly = document.getElementById('materialsSelectedOnly');
    const temporaryFileInput = document.getElementById('temporaryFileInput');
    const temporaryDropZone = document.getElementById('temporaryDropZone');
    const saveMeetingMaterialsButton = document.getElementById('saveMeetingMaterials');
    let savedMaterialsSnapshot = null;
    let materialsLoadRequest = 0;
    let isSavingMeetingMaterials = false;

    function getLocalMaterialRecord(id) {
        return state.materials[id] || {library_file_ids: [], temporary_files: []};
    }

    function setMaterialRecord(id, record) {
        state.materials[id] = {
            library_file_ids: Array.from(new Set(record.library_file_ids || [])),
            temporary_files: Array.isArray(record.temporary_files) ? record.temporary_files : []
        };
        writeJsonStorage(meetingMaterialsStorageKey, state.materials);
    }

    function selectedMaterialFileIds() {
        return materialsCheckboxes.filter(input => input.checked).map(input => input.value);
    }

    function temporaryFileSnapshotKey(file) {
        const id = file?.id || file?.file_id || file?.key || file?.storage_key;
        if (id) return `id:${String(id)}`;
        return [
            'file',
            String(file?.name || file?.filename || ''),
            String(file?.size || file?.size_bytes || 0),
            String(file?.type || file?.content_type || '')
        ].join(':');
    }

    function currentMaterialsSnapshot() {
        return JSON.stringify({
            library_file_ids: selectedMaterialFileIds().map(String).sort(),
            temporary_files: state.currentTemporaryFiles.map(temporaryFileSnapshotKey).sort()
        });
    }

    function materialsHaveUnsavedChanges() {
        return Boolean(
            materialsMeeting?.value &&
            savedMaterialsSnapshot !== null &&
            currentMaterialsSnapshot() !== savedMaterialsSnapshot
        );
    }

    function updateSaveMeetingMaterialsButton() {
        if (!saveMeetingMaterialsButton) return;
        const disabled = isSavingMeetingMaterials || !materialsHaveUnsavedChanges();
        saveMeetingMaterialsButton.disabled = disabled;
        saveMeetingMaterialsButton.setAttribute('aria-disabled', String(disabled));
    }

    function markMeetingMaterialsSaved() {
        savedMaterialsSnapshot = materialsMeeting?.value ? currentMaterialsSnapshot() : null;
        updateSaveMeetingMaterialsButton();
    }

    function renderTemporaryFiles() {
        const list = document.getElementById('temporaryFileList');
        const empty = document.getElementById('temporaryFilesEmpty');
        if (!list || !empty) return;
        list.querySelectorAll('.temporary-file-item').forEach(item => item.remove());
        const allFiles = [...state.currentTemporaryFiles, ...state.pendingTemporaryFiles.map((file, index) => ({
            id: `pending-${index}`,
            name: file.name,
            size: file.size,
            pending: true
        }))];
        empty.hidden = allFiles.length > 0;
        allFiles.forEach(file => {
            const item = document.createElement('div');
            item.className = 'temporary-file-item';
            item.dataset.temporaryFileId = file.id || file.file_id || '';
            item.dataset.pending = file.pending ? 'true' : 'false';
            item.innerHTML = `
                <span class="temporary-file-icon" aria-hidden="true">⏱</span>
                <span class="temporary-file-copy"><strong>${escapeHtml(file.name || file.filename || 'Temporary file')}</strong><small>${formatBytes(file.size || file.size_bytes)}${file.pending ? ' · Ready to add' : ' · This application only'}</small></span>
                <button type="button" class="temporary-file-remove" aria-label="Remove ${escapeHtml(file.name || file.filename || 'temporary file')}">×</button>
            `;
            list.appendChild(item);
        });
        updateMaterialsSummary();
    }

    function updateMaterialsSummary() {
        const selectedCount = selectedMaterialFileIds().length;
        const temporaryCount = state.currentTemporaryFiles.length + state.pendingTemporaryFiles.length;
        const selectedCountElement = document.getElementById('selectedLibraryCount');
        const temporaryCountElement = document.getElementById('temporaryFileCount');
        const statusElement = document.getElementById('materialsSaveStatus');
        const summaryElement = document.getElementById('materialsPackageSummary');
        const hasMeeting = Boolean(materialsMeeting?.value);
        const hasUnsavedChanges = materialsHaveUnsavedChanges();
        if (selectedCountElement) selectedCountElement.textContent = String(selectedCount);
        if (temporaryCountElement) temporaryCountElement.textContent = String(temporaryCount);
        if (statusElement) {
            statusElement.textContent = !hasMeeting
                ? 'Not started'
                : (hasUnsavedChanges ? 'Unsaved changes' : 'Saved');
        }
        if (summaryElement) {
            summaryElement.textContent = hasMeeting
                ? `${selectedCount} library document${selectedCount === 1 ? '' : 's'} and ${temporaryCount} temporary file${temporaryCount === 1 ? '' : 's'} selected.`
                : 'Create or select an upcoming interview to begin.';
        }
        updateSaveMeetingMaterialsButton();
    }

    function filterMaterialLibrary() {
        const term = materialsSearch?.value.trim().toLowerCase() || '';
        const selectedOnly = materialsSelectedOnly?.checked || false;
        document.querySelectorAll('[data-material-file-row]').forEach(row => {
            const checkbox = row.querySelector('.material-library-checkbox');
            const matchesTerm = !term || (row.dataset.fileName || '').includes(term);
            const matchesSelected = !selectedOnly || checkbox?.checked;
            row.hidden = !(matchesTerm && matchesSelected);
        });
    }

    async function loadMeetingMaterials() {
        const requestId = ++materialsLoadRequest;
        const id = materialsMeeting?.value || '';
        savedMaterialsSnapshot = null;
        materialsCheckboxes.forEach(input => { input.checked = false; });
        state.pendingTemporaryFiles = [];
        state.currentTemporaryFiles = [];
        updateSaveMeetingMaterialsButton();
        if (!id) {
            renderTemporaryFiles();
            updateMaterialsSummary();
            return;
        }

        let record = getLocalMaterialRecord(id);
        try {
            const response = await fetch(`${endpoints.materials}?meeting_id=${encodeURIComponent(id)}`, {headers: {'Accept': 'application/json'}});
            if (response.ok) {
                const result = await response.json();
                const serverRecord = result.materials || result.meeting_materials || result;
                if (serverRecord && typeof serverRecord === 'object') {
                    record = {
                        library_file_ids: serverRecord.library_file_ids || serverRecord.file_ids || record.library_file_ids || [],
                        temporary_files: serverRecord.temporary_files || record.temporary_files || []
                    };
                    setMaterialRecord(id, record);
                }
            }
        } catch (error) {
            console.info('Application materials endpoint unavailable; using browser storage.', error);
        }

        if (requestId !== materialsLoadRequest || id !== (materialsMeeting?.value || '')) return;
        const selected = new Set(record.library_file_ids || []);
        materialsCheckboxes.forEach(input => { input.checked = selected.has(input.value); });
        state.currentTemporaryFiles = Array.isArray(record.temporary_files) ? record.temporary_files : [];
        renderTemporaryFiles();
        markMeetingMaterialsSaved();
        updateMaterialsSummary();
        filterMaterialLibrary();
    }

    async function saveMeetingMaterials() {
        const id = materialsMeeting?.value || '';
        if (!id) {
            showToast('Create or select an upcoming interview before saving materials.', true);
            materialsMeeting?.focus();
            return;
        }
        if (!materialsHaveUnsavedChanges() || isSavingMeetingMaterials) return;
        isSavingMeetingMaterials = true;
        if (saveMeetingMaterialsButton) saveMeetingMaterialsButton.textContent = 'Saving…';
        updateSaveMeetingMaterialsButton();
        const record = {
            library_file_ids: selectedMaterialFileIds(),
            temporary_files: state.currentTemporaryFiles
        };
        setMaterialRecord(id, record);
        try {
            const response = await fetch(endpoints.materials, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({meeting_id: id, ...record, activate: true})
            });
            if (!response.ok && ![404, 405].includes(response.status)) {
                const result = await response.json().catch(() => ({}));
                throw new Error(result.error || 'Application materials could not be saved on the server.');
            }
            showToast(response.ok ? 'Application materials saved.' : 'Application materials saved in this browser.');
        } catch (error) {
            console.info('Saving application materials in browser storage.', error);
            showToast('Application materials saved in this browser.');
        }
        markMeetingMaterialsSaved();
        isSavingMeetingMaterials = false;
        if (saveMeetingMaterialsButton) saveMeetingMaterialsButton.textContent = 'Save Application Materials';
        updateMaterialsSummary();
    }

    function setPendingTemporaryFiles(files) {
        state.pendingTemporaryFiles = Array.from(files || []);
        renderTemporaryFiles();
    }

    temporaryFileInput?.addEventListener('change', () => setPendingTemporaryFiles(temporaryFileInput.files));
    ['dragenter', 'dragover'].forEach(type => temporaryDropZone?.addEventListener(type, event => {
        event.preventDefault();
        temporaryDropZone.classList.add('dragging');
    }));
    ['dragleave', 'drop'].forEach(type => temporaryDropZone?.addEventListener(type, event => {
        event.preventDefault();
        temporaryDropZone.classList.remove('dragging');
    }));
    temporaryDropZone?.addEventListener('drop', event => setPendingTemporaryFiles(event.dataTransfer?.files || []));

    document.getElementById('uploadTemporaryFiles')?.addEventListener('click', async () => {
        const id = materialsMeeting?.value || '';
        if (!id) {
            showToast('Create or select an upcoming interview before adding temporary files.', true);
            materialsMeeting?.focus();
            return;
        }
        if (!state.pendingTemporaryFiles.length) {
            temporaryFileInput?.click();
            return;
        }

        const formData = new FormData();
        formData.append('meeting_id', id);
        state.pendingTemporaryFiles.forEach(file => formData.append('files', file));
        const localMetadata = state.pendingTemporaryFiles.map(file => ({
            id: `local-${Date.now()}-${Math.random().toString(16).slice(2)}`,
            name: file.name,
            size: file.size,
            type: file.type,
            added_at: new Date().toISOString(),
            local_only: true
        }));

        try {
            const response = await fetch(`${endpoints.materials}/${encodeURIComponent(id)}/temporary-files`, {method: 'POST', body: formData});
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'Temporary upload endpoint unavailable.');
            const uploaded = result.files || result.temporary_files || [];
            state.currentTemporaryFiles.push(...uploaded);
            showToast('Temporary files added to this application.');
        } catch (error) {
            state.currentTemporaryFiles.push(...localMetadata);
            showToast('Temporary file references saved in this browser for this application.');
            console.info('Temporary files stored as browser metadata because the upload endpoint is unavailable.', error);
        }

        state.pendingTemporaryFiles = [];
        if (temporaryFileInput) temporaryFileInput.value = '';
        setMaterialRecord(id, {
            library_file_ids: selectedMaterialFileIds(),
            temporary_files: state.currentTemporaryFiles
        });
        renderTemporaryFiles();
    });

    document.getElementById('temporaryFileList')?.addEventListener('click', async event => {
        const button = event.target.closest('.temporary-file-remove');
        if (!button) return;
        const item = button.closest('.temporary-file-item');
        const id = item?.dataset.temporaryFileId || '';
        if (item?.dataset.pending === 'true') {
            const index = Number(id.replace('pending-', ''));
            if (Number.isInteger(index)) state.pendingTemporaryFiles.splice(index, 1);
            renderTemporaryFiles();
            return;
        }
        state.currentTemporaryFiles = state.currentTemporaryFiles.filter(file => String(file.id || file.file_id || '') !== id);
        const meeting = materialsMeeting?.value || '';
        if (meeting) {
            setMaterialRecord(meeting, {library_file_ids: selectedMaterialFileIds(), temporary_files: state.currentTemporaryFiles});
            try {
                await fetch(`${endpoints.materials}/${encodeURIComponent(meeting)}/temporary-files/${encodeURIComponent(id)}`, {method: 'DELETE'});
            } catch (error) {
                console.info('Temporary file removed from browser record only.', error);
            }
        }
        renderTemporaryFiles();
    });

    document.getElementById('clearTemporaryFiles')?.addEventListener('click', async () => {
        if (!state.currentTemporaryFiles.length && !state.pendingTemporaryFiles.length) return;
        const confirmed = window.AppUI?.confirm
            ? await window.AppUI.confirm({
                title: 'Clear temporary files?',
                message: 'This removes all application-only files from the selected application workspace.',
                confirmLabel: 'Clear files',
                danger: true
            })
            : window.confirm(window.AppI18n?.t('Clear all temporary files?') || 'Clear all temporary files?');
        if (!confirmed) return;
        state.pendingTemporaryFiles = [];
        state.currentTemporaryFiles = [];
        const id = materialsMeeting?.value || '';
        if (id) {
            setMaterialRecord(id, {library_file_ids: selectedMaterialFileIds(), temporary_files: []});
            try { await fetch(`${endpoints.materials}/${encodeURIComponent(id)}/temporary-files`, {method: 'DELETE'}); } catch (error) { console.info('Temporary files cleared locally only.', error); }
        }
        renderTemporaryFiles();
    });

    materialsMeeting?.addEventListener('change', async () => {
        updateDeleteMeetingButton();
        renderUpcomingMeetingDetails(materialsMeeting.value || '');
        await setActiveMeeting(materialsMeeting.value || '');
        loadMeetingMaterials();
    });
    materialsCheckboxes.forEach(input => input.addEventListener('change', () => {
        updateMaterialsSummary();
        filterMaterialLibrary();
    }));
    materialsSearch?.addEventListener('input', window.AppUI?.debounce(filterMaterialLibrary, 150) || filterMaterialLibrary);
    materialsSelectedOnly?.addEventListener('change', filterMaterialLibrary);
    saveMeetingMaterialsButton?.addEventListener('click', saveMeetingMaterials);

    // Career Profile
    function seedContextFromTemplate() {
        return {
            enabled: root.dataset.contextEnabled !== 'false',
            professional_headline: root.dataset.profileProfessionalHeadline || '',
            current_role: root.dataset.profileCurrentRole || '',
            years_experience: root.dataset.profileYearsExperience || '',
            current_location: root.dataset.profileCurrentLocation || '',
            preferred_roles: root.dataset.profilePreferredRoles || '',
            industries: root.dataset.profileIndustries || '',
            core_skills: root.dataset.profileCoreSkills || '',
            key_accomplishments: root.dataset.profileKeyAccomplishments || '',
            countries_worked: root.dataset.profileCountriesWorked || '',
            languages: root.dataset.profileLanguages || '',
            target_country: root.dataset.profileTargetCountry || '',
            target_country_experience: root.dataset.profileTargetCountryExperience || '',
            international_credentials: root.dataset.profileInternationalCredentials || '',
            certifications: root.dataset.profileCertifications || '',
            titles_needing_translation: root.dataset.profileTitlesNeedingTranslation || '',
            career_transition: root.dataset.profileCareerTransition || '',
            work_preferences: root.dataset.profileWorkPreferences || '',
            relocation_preferences: root.dataset.profileRelocationPreferences || '',
            work_authorization: root.dataset.profileWorkAuthorization || '',
            career_goals: root.dataset.profileCareerGoals || '',
            constraints: root.dataset.profileConstraints || '',
            // Legacy values are retained while older consumers are migrated.
            company: root.dataset.contextCompany || '',
            reference_link: root.dataset.contextReferenceLink || '',
            role: root.dataset.contextRole || '',
            type: root.dataset.contextType || '',
            domain: root.dataset.contextDomain || '',
            objective: root.dataset.contextObjective || '',
            free_text: root.dataset.contextFreeText || ''
        };
    }

    function normalizeContext(raw = {}) {
        return {
            enabled: raw.enabled ?? raw.use_context ?? true,
            professional_headline: raw.professional_headline ?? raw.profile_professional_headline ?? '',
            current_role: raw.current_role ?? raw.profile_current_role ?? raw.role ?? raw.context_role ?? '',
            years_experience: raw.years_experience ?? raw.profile_years_experience ?? '',
            current_location: raw.current_location ?? raw.profile_current_location ?? '',
            preferred_roles: raw.preferred_roles ?? raw.profile_preferred_roles ?? '',
            industries: raw.industries ?? raw.profile_industries ?? raw.domain ?? raw.context_domain ?? '',
            core_skills: raw.core_skills ?? raw.profile_core_skills ?? '',
            key_accomplishments: raw.key_accomplishments ?? raw.profile_key_accomplishments ?? '',
            countries_worked: raw.countries_worked ?? raw.profile_countries_worked ?? '',
            languages: raw.languages ?? raw.profile_languages ?? '',
            target_country: raw.target_country ?? raw.profile_target_country ?? '',
            target_country_experience: raw.target_country_experience ?? raw.profile_target_country_experience ?? '',
            international_credentials: raw.international_credentials ?? raw.profile_international_credentials ?? '',
            certifications: raw.certifications ?? raw.profile_certifications ?? '',
            titles_needing_translation: raw.titles_needing_translation ?? raw.profile_titles_needing_translation ?? '',
            career_transition: raw.career_transition ?? raw.profile_career_transition ?? '',
            work_preferences: raw.work_preferences ?? raw.profile_work_preferences ?? '',
            relocation_preferences: raw.relocation_preferences ?? raw.profile_relocation_preferences ?? '',
            work_authorization: raw.work_authorization ?? raw.profile_work_authorization ?? '',
            career_goals: raw.career_goals ?? raw.profile_career_goals ?? raw.objective ?? raw.context_objective ?? '',
            constraints: raw.constraints ?? raw.profile_constraints ?? '',
            company: raw.company ?? raw.context_company ?? raw.assistant_context_company ?? '',
            reference_link: raw.reference_link ?? raw.context_reference_link ?? raw.assistant_context_reference_link ?? '',
            role: raw.role ?? raw.context_role ?? raw.assistant_context_role ?? '',
            type: raw.type ?? raw.context_type ?? raw.assistant_context_type ?? '',
            domain: raw.domain ?? raw.context_domain ?? raw.assistant_context_domain ?? '',
            objective: raw.objective ?? raw.context_objective ?? raw.assistant_context_objective ?? '',
            free_text: raw.free_text ?? raw.context_free_text ?? raw.assistant_context_free_text ?? ''
        };
    }

    function serializeContext(context) {
        const value = normalizeContext(context);
        return {
            ...value,
            use_context: value.enabled,
            profile_professional_headline: value.professional_headline,
            profile_current_role: value.current_role,
            profile_years_experience: value.years_experience,
            profile_current_location: value.current_location,
            profile_preferred_roles: value.preferred_roles,
            profile_industries: value.industries,
            profile_core_skills: value.core_skills,
            profile_key_accomplishments: value.key_accomplishments,
            profile_countries_worked: value.countries_worked,
            profile_languages: value.languages,
            profile_target_country: value.target_country,
            profile_target_country_experience: value.target_country_experience,
            profile_international_credentials: value.international_credentials,
            profile_certifications: value.certifications,
            profile_titles_needing_translation: value.titles_needing_translation,
            profile_career_transition: value.career_transition,
            profile_work_preferences: value.work_preferences,
            profile_relocation_preferences: value.relocation_preferences,
            profile_work_authorization: value.work_authorization,
            profile_career_goals: value.career_goals,
            profile_constraints: value.constraints,
            context_company: value.company,
            context_reference_link: value.reference_link,
            context_role: value.current_role || value.preferred_roles || value.role,
            context_type: value.type,
            context_domain: value.industries || value.domain,
            context_objective: value.career_goals || value.objective,
            context_free_text: value.free_text
        };
    }

    function profileContextForAI(context) {
        const value = normalizeContext(context || {});
        const keys = [
            'professional_headline', 'current_role', 'years_experience',
            'current_location', 'preferred_roles', 'industries', 'core_skills',
            'key_accomplishments', 'countries_worked', 'languages',
            'target_country', 'target_country_experience',
            'international_credentials', 'certifications',
            'titles_needing_translation', 'career_transition',
            'work_preferences', 'relocation_preferences', 'work_authorization',
            'career_goals', 'constraints'
        ];
        return Object.fromEntries(
            keys
                .map(key => [key, value[key]])
                .filter(([, fieldValue]) => String(fieldValue || '').trim())
        );
    }

    function hasContextDetails(context) {
        const value = normalizeContext(context || {});
        return Boolean(
            value.professional_headline ||
            value.current_role ||
            value.years_experience ||
            value.current_location ||
            value.preferred_roles ||
            value.industries ||
            value.core_skills ||
            value.key_accomplishments ||
            value.countries_worked ||
            value.languages ||
            value.target_country ||
            value.target_country_experience ||
            value.international_credentials ||
            value.certifications ||
            value.titles_needing_translation ||
            value.career_transition ||
            value.work_preferences ||
            value.relocation_preferences ||
            value.work_authorization ||
            value.career_goals ||
            value.constraints ||
            value.role ||
            value.domain ||
            value.objective ||
            value.free_text
        );
    }

    function readDefaultContextForm() {
        const enabled = document.getElementById('useAssistantContext');
        if (!enabled) return state.context || seedContextFromTemplate();
        const existing = normalizeContext(state.context || seedContextFromTemplate());
        return normalizeContext({
            ...existing,
            enabled: enabled.checked,
            professional_headline: document.getElementById('profileProfessionalHeadline')?.value.trim() || '',
            current_role: document.getElementById('profileCurrentRole')?.value.trim() || '',
            years_experience: document.getElementById('profileYearsExperience')?.value.trim() || '',
            current_location: document.getElementById('profileCurrentLocation')?.value.trim() || '',
            preferred_roles: document.getElementById('profilePreferredRoles')?.value.trim() || '',
            industries: document.getElementById('profileIndustries')?.value.trim() || '',
            core_skills: document.getElementById('profileCoreSkills')?.value.trim() || '',
            key_accomplishments: document.getElementById('profileKeyAccomplishments')?.value.trim() || '',
            countries_worked: document.getElementById('profileCountriesWorked')?.value.trim() || '',
            languages: document.getElementById('profileLanguages')?.value.trim() || '',
            target_country: document.getElementById('profileTargetCountry')?.value.trim() || '',
            target_country_experience: document.getElementById('profileTargetCountryExperience')?.value.trim() || '',
            international_credentials: document.getElementById('profileInternationalCredentials')?.value.trim() || '',
            certifications: document.getElementById('profileCertifications')?.value.trim() || '',
            titles_needing_translation: document.getElementById('profileTitlesNeedingTranslation')?.value.trim() || '',
            career_transition: document.getElementById('profileCareerTransition')?.value.trim() || '',
            work_preferences: document.getElementById('profileWorkPreferences')?.value || '',
            relocation_preferences: document.getElementById('profileRelocationPreferences')?.value.trim() || '',
            work_authorization: document.getElementById('profileWorkAuthorization')?.value.trim() || '',
            career_goals: document.getElementById('profileCareerGoals')?.value.trim() || '',
            constraints: document.getElementById('profileConstraints')?.value.trim() || ''
        });
    }

    function writeDefaultContextForm(context) {
        const map = {
            useAssistantContext: context.enabled !== false,
            profileProfessionalHeadline: context.professional_headline || '',
            profileCurrentRole: context.current_role || '',
            profileYearsExperience: context.years_experience || '',
            profileCurrentLocation: context.current_location || '',
            profilePreferredRoles: context.preferred_roles || '',
            profileIndustries: context.industries || '',
            profileCoreSkills: context.core_skills || '',
            profileKeyAccomplishments: context.key_accomplishments || '',
            profileCountriesWorked: context.countries_worked || '',
            profileLanguages: context.languages || '',
            profileTargetCountry: context.target_country || '',
            profileTargetCountryExperience: context.target_country_experience || '',
            profileInternationalCredentials: context.international_credentials || '',
            profileCertifications: context.certifications || '',
            profileTitlesNeedingTranslation: context.titles_needing_translation || '',
            profileCareerTransition: context.career_transition || '',
            profileWorkPreferences: context.work_preferences || '',
            profileRelocationPreferences: context.relocation_preferences || '',
            profileWorkAuthorization: context.work_authorization || '',
            profileCareerGoals: context.career_goals || '',
            profileConstraints: context.constraints || ''
        };
        Object.entries(map).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (!element) return;
            if (element.type === 'checkbox') element.checked = Boolean(value);
            else element.value = value;
        });
    }

    function legacyMeetingInstructions(context = {}) {
        const parts = [];
        const topics = String(context.topics || '').trim();
        const constraints = String(context.constraints || '').trim();
        const freeText = String(context.free_text || '').trim();
        if (topics) parts.push(`Topics to prioritize: ${topics}`);
        if (constraints) parts.push(`Constraints or sensitivities: ${constraints}`);
        if (freeText) parts.push(freeText);
        return parts.join('\n');
    }

    function normalizeMeetingContext(raw = {}) {
        const value = raw && typeof raw === 'object' ? raw : {};
        return {
            objective: String(value.objective || '').trim(),
            participants: String(value.participants || '').trim(),
            special_instructions: String(
                value.special_instructions ||
                value.meeting_instructions ||
                value.instructions ||
                legacyMeetingInstructions(value)
            ).trim()
        };
    }

    function normalizeMeetingContexts(raw = {}) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
        return Object.fromEntries(
            Object.entries(raw).map(([meetingId, context]) => [
                meetingId,
                normalizeMeetingContext(context)
            ])
        );
    }

    function hasMeetingContextDetails(context = {}) {
        const normalized = normalizeMeetingContext(context);
        return Boolean(
            normalized.objective ||
            normalized.participants ||
            normalized.special_instructions
        );
    }

    function readMeetingContextForm() {
        return normalizeMeetingContext({
            objective: document.getElementById('meetingContextObjective')?.value.trim() || '',
            participants: document.getElementById('meetingContextParticipants')?.value.trim() || '',
            special_instructions: document.getElementById('meetingContextSpecialInstructions')?.value.trim() || ''
        });
    }

    function setMeetingOverrideExpanded(expanded, {focus = false} = {}) {
        const toggle = document.getElementById('meetingOverrideToggle');
        const panel = document.getElementById('meetingOverridePanel');
        if (!toggle || !panel) return;
        const open = Boolean(expanded);
        toggle.setAttribute('aria-expanded', String(open));
        panel.hidden = !open;

        const label = toggle.querySelector('.meeting-override-toggle-label');
        if (label) label.textContent = open ? 'Hide application-specific context' : 'Add application-specific context';

        if (focus && open) {
            window.requestAnimationFrame(() => document.getElementById('meetingContextObjective')?.focus());
        }
    }

    function setMeetingInstructionsExpanded(expanded, {focus = false} = {}) {
        const toggle = document.getElementById('meetingInstructionsToggle');
        const panel = document.getElementById('meetingInstructionsPanel');
        if (!toggle || !panel) return;
        const open = Boolean(expanded);
        toggle.setAttribute('aria-expanded', String(open));
        panel.hidden = !open;
        if (focus && open) {
            window.requestAnimationFrame(() => document.getElementById('meetingContextSpecialInstructions')?.focus());
        }
    }

    function updateMeetingInstructionsControls() {
        const value = document.getElementById('meetingContextSpecialInstructions')?.value.trim() || '';
        const clearButton = document.getElementById('clearMeetingInstructions');
        if (clearButton) clearButton.hidden = !value;
    }

    function writeMeetingContextForm(context = {}) {
        const normalized = normalizeMeetingContext(context);
        const map = {
            meetingContextObjective: normalized.objective,
            meetingContextParticipants: normalized.participants,
            meetingContextSpecialInstructions: normalized.special_instructions
        };
        Object.entries(map).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) element.value = value;
        });
        setMeetingOverrideExpanded(hasMeetingContextDetails(normalized));
        setMeetingInstructionsExpanded(Boolean(normalized.special_instructions));
        updateMeetingInstructionsControls();
    }

    function contextSnapshot(value) {
        return JSON.stringify(value || {});
    }

    function markDefaultContextSaved(context) {
        savedDefaultContextSnapshot = contextSnapshot(normalizeContext(context));
        defaultContextLoaded = true;
    }

    function markMeetingContextSaved(meetingId, context) {
        if (!meetingId) return;
        savedMeetingContextSnapshots[meetingId] = contextSnapshot(normalizeMeetingContext(context));
    }

    function contextHasUnsavedChanges() {
        if (!defaultContextLoaded) return false;
        const defaultChanged = contextSnapshot(readDefaultContextForm()) !== savedDefaultContextSnapshot;
        const meetingId = document.getElementById('contextMeetingSelect')?.value || '';
        if (!meetingId) return defaultChanged;
        const baseline = savedMeetingContextSnapshots[meetingId] ?? contextSnapshot(normalizeMeetingContext(state.meetingContexts[meetingId] || {}));
        return defaultChanged || contextSnapshot(readMeetingContextForm()) !== baseline;
    }

    function updateSaveContextButton() {
        const button = document.getElementById('saveContextButton');
        if (!button) return;
        const enabled = !isSavingContext && contextHasUnsavedChanges();
        button.disabled = !enabled;
        button.setAttribute('aria-disabled', String(!enabled));
    }

    function cacheCurrentMeetingContextDraft() {
        const meetingId = activeContextMeetingId || document.getElementById('contextMeetingSelect')?.value || '';
        if (!meetingId) return;
        state.meetingContexts[meetingId] = readMeetingContextForm();
        writeJsonStorage(meetingContextStorageKey, state.meetingContexts);
    }

    function updateContextStatus(context) {
        const hasDetails = hasContextDetails(context);
        const enabled = context?.enabled !== false;
        const badge = document.getElementById('contextStatusBadge');
        if (badge) {
            badge.textContent = hasDetails ? (enabled ? 'Context Active' : 'Context Paused') : 'Optional';
            badge.classList.toggle('active', hasDetails && enabled);
            badge.classList.toggle('paused', hasDetails && !enabled);
        }
        const summary = document.getElementById('contextSummaryValues');
        if (summary) summary.textContent = hasDetails ? `${enabled ? 'Active' : 'Paused'} context configured.` : 'No context details added yet.';
        const searchStatus = document.getElementById('searchContextStatus');
        if (searchStatus) {
            searchStatus.textContent = hasDetails ? (enabled ? 'Context is active' : 'Saved context is paused') : 'No saved context yet';
            searchStatus.classList.toggle('is-paused', !enabled);
        }
    }

    function updateEffectiveContextPreview() {
        const profile = readDefaultContextForm();
        const preview = document.getElementById('effectiveContextPreview');
        const chips = document.getElementById('effectiveContextChips');
        const status = document.getElementById('effectiveContextStatus');
        if (!preview || !chips || !status) return;

        status.textContent = profile.enabled ? 'Profile is enabled' : 'Profile is paused';
        status.classList.toggle('is-paused', !profile.enabled);
        const chipValues = [
            profile.current_role,
            profile.target_country,
            profile.work_preferences,
            profile.years_experience
        ].filter(Boolean);
        chips.replaceChildren(...chipValues.map(value => {
            const chip = document.createElement('span');
            chip.textContent = value;
            return chip;
        }));

        const rows = [
            ['Professional headline', profile.professional_headline],
            ['Current role', profile.current_role],
            ['Preferred roles', profile.preferred_roles],
            ['Industries', profile.industries],
            ['Core skills', profile.core_skills],
            ['Key accomplishments', profile.key_accomplishments],
            ['Countries worked', profile.countries_worked],
            ['Languages', profile.languages],
            ['Target country', profile.target_country],
            ['International credentials', profile.international_credentials],
            ['Titles needing translation', profile.titles_needing_translation],
            ['Career transition', profile.career_transition],
            ['Location', profile.current_location],
            ['Work preference', profile.work_preferences],
            ['Relocation', profile.relocation_preferences],
            ['Career goals', profile.career_goals],
            ['Constraints', profile.constraints]
        ].filter(([, value]) => String(value || '').trim());
        preview.replaceChildren();
        if (!rows.length) {
            const empty = document.createElement('div');
            empty.className = 'context-preview-empty';
            empty.textContent = 'Add professional or international-background details to build your reusable profile.';
            preview.appendChild(empty);
            return;
        }
        rows.forEach(([label, value]) => {
            const term = document.createElement('dt');
            term.textContent = label;
            const detail = document.createElement('dd');
            detail.textContent = value;
            preview.append(term, detail);
        });
    }

    async function loadContext() {
        const local = normalizeContext(readJsonStorage(contextStorageKey, {}));
        let context = hasContextDetails(local) ? local : normalizeContext(seedContextFromTemplate());
        try {
            const response = await fetch(endpoints.context, {headers: {'Accept': 'application/json'}});
            if (response.ok) {
                const result = await response.json();
                const serverContext = result.context || result.assistant_context || result;
                if (serverContext && typeof serverContext === 'object') context = normalizeContext(serverContext);
            }
        } catch (error) {
            console.info('Context endpoint unavailable; using local context.', error);
        }
        state.context = context;
        writeDefaultContextForm(context);
        markDefaultContextSaved(context);
        updateContextStatus(context);
        updateEffectiveContextPreview();
        updateSaveContextButton();
    }

    async function persistContext(context) {
        writeJsonStorage(contextStorageKey, context);
        state.context = context;
        updateContextStatus(context);
        try {
            const response = await fetch(endpoints.context, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(serializeContext(context))
            });
            if (response.ok) return 'career profile saved.';
            if ([404, 405].includes(response.status)) return 'career profile saved in this browser.';
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || 'Context could not be saved on the server.');
        } catch (error) {
            console.info('Saving context locally.', error);
            return 'career profile saved in this browser.';
        }
    }

    async function loadMeetingContextForSelection(meetingId) {
        const normalizedId = String(meetingId || '');
        let context = normalizeMeetingContext(state.meetingContexts[normalizedId] || {});
        const baseline = savedMeetingContextSnapshots[normalizedId];
        const hasUnsavedDraft = Boolean(
            normalizedId &&
            baseline !== undefined &&
            contextSnapshot(context) !== baseline
        );

        if (normalizedId && !hasUnsavedDraft) {
            try {
                const response = await fetch(`${endpoints.materials}?meeting_id=${encodeURIComponent(normalizedId)}`);
                if (response.ok) {
                    const result = await response.json();
                    context = normalizeMeetingContext(result.materials?.meeting_context || context);
                    state.meetingContexts[normalizedId] = context;
                    writeJsonStorage(meetingContextStorageKey, state.meetingContexts);
                }
            } catch (error) {
                console.info('Application context could not be loaded from the server.', error);
            }
        }

        activeContextMeetingId = normalizedId;
        writeMeetingContextForm(context);
        if (normalizedId && !hasUnsavedDraft) markMeetingContextSaved(normalizedId, context);
        updateEffectiveContextPreview();
        updateSaveContextButton();
    }

    document.getElementById('contextMeetingSelect')?.addEventListener('change', async event => {
        cacheCurrentMeetingContextDraft();
        const meetingId = event.currentTarget.value || '';
        await setActiveMeeting(meetingId);
        await loadMeetingContextForSelection(meetingId);
    });

    document.getElementById('meetingOverrideToggle')?.addEventListener('click', event => {
        const expanded = event.currentTarget.getAttribute('aria-expanded') === 'true';
        setMeetingOverrideExpanded(!expanded, {focus: !expanded});
    });

    document.getElementById('meetingInstructionsToggle')?.addEventListener('click', event => {
        const expanded = event.currentTarget.getAttribute('aria-expanded') === 'true';
        setMeetingInstructionsExpanded(!expanded, {focus: !expanded});
    });

    document.querySelectorAll('[data-meeting-instruction]').forEach(button => {
        button.addEventListener('click', () => {
            const textarea = document.getElementById('meetingContextSpecialInstructions');
            if (!textarea) return;
            const suggestion = String(button.dataset.meetingInstruction || '').trim();
            if (!suggestion) return;
            const existing = textarea.value.trim();
            if (!existing.toLowerCase().includes(suggestion.toLowerCase())) {
                textarea.value = [existing, suggestion].filter(Boolean).join('\n');
            }
            setMeetingInstructionsExpanded(true);
            textarea.dispatchEvent(new Event('input', {bubbles: true}));
            textarea.focus();
        });
    });

    document.getElementById('clearMeetingInstructions')?.addEventListener('click', () => {
        const textarea = document.getElementById('meetingContextSpecialInstructions');
        if (!textarea) return;
        textarea.value = '';
        textarea.dispatchEvent(new Event('input', {bubbles: true}));
        textarea.focus();
    });

    document.getElementById('assistantContextForm')?.addEventListener('input', () => {
        cacheCurrentMeetingContextDraft();
        updateMeetingInstructionsControls();
        updateEffectiveContextPreview();
        updateSaveContextButton();
    });

    document.getElementById('assistantContextForm')?.addEventListener('submit', async event => {
        event.preventDefault();
        if (isSavingContext || !contextHasUnsavedChanges()) return;

        const button = document.getElementById('saveContextButton');
        isSavingContext = true;
        if (button) button.textContent = 'Saving…';
        updateSaveContextButton();

        const context = readDefaultContextForm();
        const selectedMeeting = document.getElementById('contextMeetingSelect')?.value || '';
        let meetingContext = null;

        if (selectedMeeting) {
            meetingContext = readMeetingContextForm();
            state.meetingContexts[selectedMeeting] = meetingContext;
            writeJsonStorage(meetingContextStorageKey, state.meetingContexts);
            try {
                const response = await fetch(`${endpoints.meetings}/${encodeURIComponent(selectedMeeting)}/context`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(meetingContext)
                });
                if (!response.ok && ![404, 405].includes(response.status)) {
                    const result = await response.json().catch(() => ({}));
                    throw new Error(result.error || 'Application-specific context could not be saved on the server.');
                }
                await setActiveMeeting(selectedMeeting);
            } catch (error) {
                console.info('Application-specific context saved in browser only.', error);
            }
        }

        const message = await persistContext(context);
        markDefaultContextSaved(context);
        if (selectedMeeting && meetingContext) {
            markMeetingContextSaved(selectedMeeting, meetingContext);
            if (!hasMeetingContextDetails(meetingContext)) setMeetingOverrideExpanded(false);
        }
        updateEffectiveContextPreview();
        showToast(message);

        isSavingContext = false;
        if (button) button.textContent = 'Save Career Profile';
        updateSaveContextButton();
    });

    // Unified Career Evidence Library
    const knowledgeForm = document.getElementById('knowledgeQuestionForm');
    const questionInput = document.getElementById('knowledgeQuestion');
    const answerPanel = document.getElementById('answerPanel');
    const answerContent = document.getElementById('answerContent');
    const sourcesSection = document.getElementById('sourcesSection');
    const sourceList = document.getElementById('sourceList');
    const answerLoadingState = document.getElementById('knowledge-answer-loading');
    const answerEmptyState = document.getElementById('knowledge-answer-empty');
    const answerErrorState = document.getElementById('knowledge-answer-error');
    const answerRetryButton = document.getElementById('knowledge-answer-retry');
    const copyAnswerButton = document.getElementById('copyAnswerButton');

    function selectedSearchScope() {
        return document.querySelector('input[name="search_scope"]:checked')?.value || 'current_meeting';
    }

    function updateSearchScopeUi() {
        const scope = selectedSearchScope();
        const labels = {
            current_meeting: 'Current Application Materials',
            library: 'Career Evidence Library',
            meetings: 'Previous Mock Interviews',
            all: 'All Career Sources'
        };
        const scopeLabel = document.getElementById('searchScopeLabel');
        if (scopeLabel) scopeLabel.textContent = labels[scope] || labels.current_meeting;

        document.querySelectorAll('[data-search-filter]').forEach(field => {
            const type = field.dataset.searchFilter;
            const visible = scope === 'all' ||
                (scope === 'current_meeting' && type === 'package') ||
                (scope === 'library' && type === 'collection') ||
                (scope === 'meetings' && ['meeting', 'date', 'participant', 'content'].includes(type));
            field.hidden = !visible;
        });
    }

    document.querySelectorAll('input[name="search_scope"]').forEach(input => input.addEventListener('change', updateSearchScopeUi));
    document.querySelectorAll('[data-knowledge-question]').forEach(button => button.addEventListener('click', () => {
        if (!questionInput) return;
        questionInput.value = button.dataset.knowledgeQuestion || '';
        questionInput.focus();
    }));

    function renderSources(sources) {
        if (!sourceList || !sourcesSection) return;
        sourceList.replaceChildren();

        // The backend searches document sections independently. Keep a small
        // client-side safeguard so one document or meeting is never rendered
        // repeatedly if several matching excerpts are returned.
        const uniqueSources = [];
        const seenSources = new Set();
        (Array.isArray(sources) ? sources : []).forEach(source => {
            const type = source.source_type || source.type || (source.meeting_name ? 'Previous Mock Interview' : 'Document');
            const title = source.filename || source.display_name || source.meeting_name || source.title || 'Source';
            const identity = source.file_id
                ? `${String(type).toLowerCase()}|file:${source.file_id}`
                : source.meeting_id
                    ? `${String(type).toLowerCase()}|meeting:${source.meeting_id}|${String(title).toLowerCase()}`
                    : `${String(type).toLowerCase()}|title:${String(title).toLowerCase()}|${String(source.collection_id || source.collection_name || '').toLowerCase()}`;
            if (seenSources.has(identity)) return;
            seenSources.add(identity);
            uniqueSources.push(source);
        });

        uniqueSources.forEach((source, index) => {
            const item = document.createElement('div');
            item.className = 'knowledge-source-item';
            const type = source.source_type || source.type || (source.meeting_name ? 'Previous Mock Interview' : 'Document');
            const title = source.filename || source.display_name || source.meeting_name || source.title || `Source ${index + 1}`;
            const detail = source.collection_name || source.meeting_date || source.date || source.section || '';
            item.innerHTML = `<span class="knowledge-source-number">${index + 1}</span><span><strong>${escapeHtml(title)}</strong><small>${escapeHtml(type)}${detail ? ` · ${escapeHtml(detail)}` : ''}</small></span>`;
            sourceList.appendChild(item);
        });
        sourcesSection.hidden = uniqueSources.length === 0;
    }

    function setAnswerState(state, message = '') {
        [answerLoadingState, answerEmptyState, answerErrorState].forEach(element => {
            if (element) element.hidden = true;
        });
        if (answerContent) answerContent.hidden = state !== 'answer';
        if (copyAnswerButton) copyAnswerButton.hidden = state !== 'answer';
        if (sourcesSection && state !== 'answer') sourcesSection.hidden = true;
        if (state === 'loading' && answerLoadingState) answerLoadingState.hidden = false;
        if (state === 'empty' && answerEmptyState) answerEmptyState.hidden = false;
        if (state === 'error' && answerErrorState) {
            window.AppUI?.showWorkspaceState(answerErrorState, {state: 'error', message});
        }
    }

    answerRetryButton?.addEventListener('click', () => knowledgeForm?.requestSubmit());

    knowledgeForm?.addEventListener('submit', async event => {
        event.preventDefault();
        const question = questionInput?.value.trim() || '';
        if (!question) return;
        const button = document.getElementById('askButton');
        const scope = selectedSearchScope();
        const selectedPackage = document.getElementById('knowledgeSearchPackage')?.value || '';
        const selectedMeeting = document.getElementById('knowledgeSearchMeeting')?.value || '';
        const contextKey = selectedPackage || selectedMeeting;
        const meetingContext = contextKey ? (state.meetingContexts[contextKey] || {}) : {};
        const materialRecord = selectedPackage ? getLocalMaterialRecord(selectedPackage) : {library_file_ids: [], temporary_files: []};
        const payload = {
            question,
            source_scope: scope,
            search_scope: scope,
            meeting_ids: (scope === 'meetings' || scope === 'all') && selectedMeeting ? [selectedMeeting] : [],
            meeting_package_id: (scope === 'current_meeting' || scope === 'all') ? selectedPackage : '',
            collection_ids: document.getElementById('knowledgeSearchCollection')?.value ? [document.getElementById('knowledgeSearchCollection').value] : [],
            library_file_ids: scope === 'current_meeting' || scope === 'all' ? materialRecord.library_file_ids : [],
            temporary_files: scope === 'current_meeting' || scope === 'all' ? materialRecord.temporary_files : [],
            filters: {
                date_from: document.getElementById('knowledgeSearchDateFrom')?.value || '',
                date_to: document.getElementById('knowledgeSearchDateTo')?.value || '',
                participant: document.getElementById('knowledgeSearchParticipant')?.value || '',
                content_type: document.getElementById('knowledgeSearchContentType')?.value || 'all'
            },
            use_context: state.context?.enabled !== false,
            assistant_context: state.context?.enabled !== false ? profileContextForAI(state.context || {}) : null,
            meeting_context: meetingContext
        };

        if (button) {
            button.disabled = true;
            button.textContent = 'Searching…';
        }
        if (answerPanel) {
            answerPanel.hidden = false;
            answerPanel.setAttribute('aria-busy', 'true');
        }
        setAnswerState('loading');

        try {
            const response = await fetch(endpoints.ask, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || 'The question could not be answered.');
            const answer = String(result.answer || '').trim();
            if (!answer) {
                setAnswerState('empty');
            } else {
                if (answerContent) answerContent.textContent = answer;
                setAnswerState('answer');
                renderSources(result.sources || result.meeting_sources || []);
            }
        } catch (error) {
            setAnswerState('error', error.message || 'The question could not be answered.');
        } finally {
            answerPanel?.setAttribute('aria-busy', 'false');
            if (button) {
                button.disabled = false;
                button.innerHTML = '<span aria-hidden="true">✦</span> Ask AI';
            }
        }
    });

    document.getElementById('copyAnswerButton')?.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(answerContent?.textContent || '');
            showToast('Answer copied.');
        } catch (error) {
            showToast('The answer could not be copied.', true);
        }
    });

    // Initialize the active workspace.
    Promise.resolve(loadUpcomingMeetingsFromServer()).then(async activeMeetingId => {
        if (activeMeetingId && materialsMeeting) {
            materialsMeeting.value = activeMeetingId;
            loadMeetingMaterials();
        }
        const contextMeeting = document.getElementById('contextMeetingSelect');
        if (contextMeeting && activeMeetingId) contextMeeting.value = activeMeetingId;
        await loadMeetingContextForSelection(contextMeeting?.value || '');
    });
    Promise.resolve(loadCompletedMeetings()).then(() => {
        if (materialsMeeting?.value) loadMeetingMaterials();
    });
    loadContext();
    filterLibraryFiles();
    filterMaterialLibrary();
    renderTemporaryFiles();
    updateSearchScopeUi();
});
