let meetingsData = [];
    let actionsData = [];
    let selectedMeetingIndex = null;
    let editingMeetingIndex = null;
    let savingMeetingIndex = null;
    let currentTab = 'summary';
    let currentTranscript = '';
    let meetingViewMode = 'date';
    let meetingSortDirection = 'newest';
    let topicManagerPreviousFocus = null;
    let topicManagerBusy = false;

    

    

    

    

    

    function getSearchableText(meeting, index) {
        return `${getMeetingSearchableText(meeting, index, {includeTranscript: true})} ${getMeetingTopics(meeting).join(' ')}`.toLowerCase();
    }

    document.addEventListener('DOMContentLoaded', async function() {
        const meetingList = document.getElementById('meeting-list');
        const searchInput = document.getElementById('meeting-search');
        const transcriptSearch = document.getElementById('transcript-search');
        const tabButtons = Array.from(document.querySelectorAll('[data-meeting-tab]'));

        searchInput?.addEventListener('input', window.AppUI?.debounce(renderMeetingList, 260) || renderMeetingList);
        transcriptSearch?.addEventListener('input', window.AppUI?.debounce(renderTranscriptContent, 120) || renderTranscriptContent);
        document.getElementById('copy-transcript-button')?.addEventListener('click', copyTranscript);
        document.getElementById('fullscreen-button')?.addEventListener('click', toggleTranscriptFullscreen);
        initializeMeetingOrganizer();
        initializeTopicManager();

        tabButtons.forEach((button, index) => {
            button.addEventListener('click', () => switchTab(button.dataset.meetingTab));
            button.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                let nextIndex = index;
                if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabButtons.length;
                if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabButtons.length) % tabButtons.length;
                if (event.key === 'Home') nextIndex = 0;
                if (event.key === 'End') nextIndex = tabButtons.length - 1;
                tabButtons[nextIndex].focus();
                switchTab(tabButtons[nextIndex].dataset.meetingTab);
            });
        });

        const urlParameters = new URLSearchParams(window.location.search);
        const requestedTab = urlParameters.get('tab');
        const requestedMeeting = urlParameters.get('meeting') || urlParameters.get('meeting_id');
        switchTab(['summary', 'scorecard', 'transcript', 'ask-ai'].includes(requestedTab) ? requestedTab : 'summary', false);

        try {
            const transcriptUrl = window.AppUI?.appUrl('/api/career/interview-reviews') || '/api/career/interview-reviews';
            const actionUrl = window.AppUI?.appUrl('/api/career/actions') || '/api/career/actions';
            const [response, actionResponse] = await Promise.all([
                fetch(transcriptUrl),
                fetch(actionUrl).catch(error => {
                    console.warn('Career Action Plan data is unavailable in Interview Review:', error);
                    return null;
                })
            ]);
            if (!response.ok) throw new Error('Network response was not ok');
            meetingsData = sortMeetingsByDate(ensureArrayPayload(await response.json()));
            if (actionResponse?.ok) {
                actionsData = ensureArrayPayload(await actionResponse.json());
            }
            meetingList?.setAttribute('aria-busy', 'false');
            refreshMeetingOrganizerControls();
            renderMeetingList();
            if (meetingsData.length > 0) {
                const requestedMeetingIndex = findRequestedMeetingIndex(requestedMeeting);
                updateMeetingReview(requestedMeetingIndex >= 0 ? requestedMeetingIndex : 0);
            }
        } catch (error) {
            console.error('Error loading interview reviews:', error);
            meetingList?.setAttribute('aria-busy', 'false');
            meetingList.innerHTML = `
                <div class="no-records-state">
                    <strong>Unable to load mock interviews</strong>
                    The mock-interview service returned an invalid response or is temporarily unavailable.
                </div>
            `;
        }
    });

    function normalizeTopicList(value) {
        const rawTopics = Array.isArray(unwrapDynamoDBValue(value))
            ? unwrapDynamoDBValue(value)
            : (typeof unwrapDynamoDBValue(value) === 'string' ? unwrapDynamoDBValue(value).split(',') : []);
        const topics = [];
        const seen = new Set();
        rawTopics.forEach(item => {
            const topic = String(item || '').trim().replace(/\s+/g, ' ').slice(0, 60).trim();
            const key = topic.toLocaleLowerCase();
            if (!topic || seen.has(key)) return;
            seen.add(key);
            topics.push(topic);
        });
        return topics.slice(0, 20);
    }

    function getMeetingTopics(meeting) {
        return normalizeTopicList(meeting?.topics);
    }

    function getTopicStats() {
        const stats = new Map();
        meetingsData.forEach(meeting => {
            getMeetingTopics(meeting).forEach(topic => {
                const key = topic.toLocaleLowerCase();
                const existing = stats.get(key) || {name: topic, count: 0};
                existing.count += 1;
                if (topic.localeCompare(existing.name, undefined, {sensitivity: 'base'}) < 0) {
                    existing.name = topic;
                }
                stats.set(key, existing);
            });
        });
        return Array.from(stats.values()).sort((a, b) =>
            a.name.localeCompare(b.name, window.AppI18n?.locale || undefined, {sensitivity: 'base'})
        );
    }

    function refreshMeetingOrganizerControls() {
        const stats = getTopicStats();
        const filter = document.getElementById('meeting-topic-filter');
        const suggestions = document.getElementById('meeting-topic-suggestions');
        const previousFilter = filter?.value || '';

        if (filter) {
            filter.replaceChildren();
            const allOption = document.createElement('option');
            allOption.value = '';
            allOption.textContent = 'All topics';
            filter.appendChild(allOption);
            stats.forEach(topic => {
                const option = document.createElement('option');
                option.value = topic.name;
                option.textContent = `${topic.name} (${topic.count})`;
                filter.appendChild(option);
            });
            const matchingFilter = stats.find(topic => topic.name.toLocaleLowerCase() === previousFilter.toLocaleLowerCase());
            filter.value = matchingFilter?.name || '';
        }

        if (suggestions) {
            suggestions.replaceChildren();
            stats.forEach(topic => {
                const option = document.createElement('option');
                option.value = topic.name;
                suggestions.appendChild(option);
            });
        }

        document.getElementById('meeting-manage-topics')?.toggleAttribute('disabled', stats.length === 0);
    }

    function initializeMeetingOrganizer() {
        document.querySelectorAll('[data-meeting-view]').forEach(button => {
            button.addEventListener('click', () => {
                meetingViewMode = button.dataset.meetingView === 'topic' ? 'topic' : 'date';
                document.querySelectorAll('[data-meeting-view]').forEach(item => {
                    const active = item.dataset.meetingView === meetingViewMode;
                    item.classList.toggle('active', active);
                    item.setAttribute('aria-pressed', String(active));
                });
                renderMeetingList();
            });
        });

        document.getElementById('meeting-topic-filter')?.addEventListener('change', renderMeetingList);
        document.getElementById('meeting-date-from')?.addEventListener('change', renderMeetingList);
        document.getElementById('meeting-date-to')?.addEventListener('change', renderMeetingList);
        document.getElementById('meeting-sort-direction')?.addEventListener('change', event => {
            meetingSortDirection = event.target.value === 'oldest' ? 'oldest' : 'newest';
            renderMeetingList();
        });
        document.getElementById('meeting-clear-filters')?.addEventListener('click', () => {
            const search = document.getElementById('meeting-search');
            const topic = document.getElementById('meeting-topic-filter');
            const from = document.getElementById('meeting-date-from');
            const to = document.getElementById('meeting-date-to');
            if (search) search.value = '';
            if (topic) topic.value = '';
            if (from) from.value = '';
            if (to) to.value = '';
            renderMeetingList();
        });
        document.getElementById('meeting-manage-topics')?.addEventListener('click', openTopicManager);
    }

    function getMeetingTimestamp(meeting) {
        const parsed = new Date(getMeetingDate(meeting));
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function getFilteredMeetingItems() {
        const searchValue = document.getElementById('meeting-search')?.value.toLowerCase().trim() || '';
        const selectedTopic = document.getElementById('meeting-topic-filter')?.value || '';
        const selectedTopicKey = selectedTopic.toLocaleLowerCase();
        const fromValue = document.getElementById('meeting-date-from')?.value || '';
        const toValue = document.getElementById('meeting-date-to')?.value || '';
        const fromDate = fromValue ? new Date(`${fromValue}T00:00:00`) : null;
        const toDate = toValue ? new Date(`${toValue}T23:59:59.999`) : null;

        return meetingsData
            .map((meeting, index) => ({meeting, index, date: getMeetingTimestamp(meeting)}))
            .filter(item => {
                if (searchValue && !getSearchableText(item.meeting, item.index).includes(searchValue)) return false;
                if (selectedTopic && !getMeetingTopics(item.meeting).some(topic => topic.toLocaleLowerCase() === selectedTopicKey)) return false;
                if (fromDate && (!item.date || item.date < fromDate)) return false;
                if (toDate && (!item.date || item.date > toDate)) return false;
                return true;
            })
            .sort((a, b) => {
                const timeA = a.date?.getTime() || 0;
                const timeB = b.date?.getTime() || 0;
                return meetingSortDirection === 'oldest' ? timeA - timeB : timeB - timeA;
            });
    }

    function getDateGroupLabel(date) {
        if (!date) return 'Unknown date';
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const meetingDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
        const dayDifference = Math.round((today - meetingDay) / 86400000);
        if (dayDifference === 0) return 'Today';
        if (dayDifference === 1) return 'Yesterday';
        return date.toLocaleDateString(window.AppI18n?.locale || undefined, {
            month: 'long',
            year: 'numeric'
        });
    }

    function renderMeetingGroup(meetingList, label, items) {
        const group = document.createElement('section');
        group.className = 'meeting-list-group';
        const header = document.createElement('div');
        header.className = 'meeting-list-group-header';
        header.innerHTML = `<span>${escapeHtml(label)}</span><span>${items.length}</span>`;
        group.appendChild(header);
        items.forEach(item => group.appendChild(createMeetingListItem(item.meeting, item.index)));
        meetingList.appendChild(group);
    }

    function renderMeetingList() {
        const meetingList = document.getElementById('meeting-list');
        if (!meetingList) return;

        if (!meetingsData || meetingsData.length === 0) {
            meetingList.innerHTML = `
                <div class="no-records-state">
                    <strong>No mock interviews found</strong>
                    Open Mock Interview to create your first interview review.
                </div>
            `;
            return;
        }

        const filteredMeetings = getFilteredMeetingItems();
        if (filteredMeetings.length === 0) {
            meetingList.innerHTML = `
                <div class="no-records-state">
                    <strong>No matching mock interviews</strong>
                    Adjust the search, topic, or date filters and try again.
                </div>
            `;
            return;
        }

        meetingList.replaceChildren();
        if (meetingViewMode === 'topic') {
            const selectedTopic = document.getElementById('meeting-topic-filter')?.value || '';
            const groups = new Map();
            filteredMeetings.forEach(item => {
                const topics = selectedTopic ? [selectedTopic] : getMeetingTopics(item.meeting);
                const groupTopics = topics.length > 0 ? topics : ['Untagged'];
                groupTopics.forEach(topic => {
                    if (!groups.has(topic)) groups.set(topic, []);
                    groups.get(topic).push(item);
                });
            });
            Array.from(groups.entries())
                .sort(([topicA], [topicB]) => {
                    if (topicA === 'Untagged') return 1;
                    if (topicB === 'Untagged') return -1;
                    return topicA.localeCompare(topicB, window.AppI18n?.locale || undefined, {sensitivity: 'base'});
                })
                .forEach(([topic, items]) => renderMeetingGroup(meetingList, topic, items));
        } else {
            const groups = new Map();
            filteredMeetings.forEach(item => {
                const label = getDateGroupLabel(item.date);
                if (!groups.has(label)) groups.set(label, []);
                groups.get(label).push(item);
            });
            groups.forEach((items, label) => renderMeetingGroup(meetingList, label, items));
        }

        if (editingMeetingIndex !== null) {
            requestAnimationFrame(() => {
                const activeInput = meetingList.querySelector(
                    `.meeting-list-item[data-index="${editingMeetingIndex}"] .meeting-name-input`
                );
                if (activeInput && document.activeElement !== activeInput && savingMeetingIndex === null) {
                    activeInput.focus();
                    activeInput.select();
                }
            });
        }
    }

    function createTopicChip(topic, removable = false) {
        const chip = document.createElement(removable ? 'button' : 'span');
        chip.className = `meeting-topic-chip${removable ? ' is-removable' : ''}`;
        chip.dataset.topic = topic;
        chip.textContent = removable ? `${topic} ×` : topic;
        if (removable) {
            chip.type = 'button';
            chip.setAttribute('aria-label', `Remove topic ${topic}`);
        }
        return chip;
    }

    function addTopicToEditor(editor, rawTopic) {
        const topic = String(rawTopic || '').trim().replace(/\s+/g, ' ').slice(0, 60).trim();
        if (!topic) return false;
        const chips = editor.querySelector('.meeting-topic-edit-chips');
        const existing = Array.from(chips.querySelectorAll('[data-topic]'))
            .some(chip => chip.dataset.topic.toLocaleLowerCase() === topic.toLocaleLowerCase());
        if (existing || chips.children.length >= 20) return false;
        const chip = createTopicChip(topic, true);
        chip.addEventListener('click', () => chip.remove());
        chips.appendChild(chip);
        return true;
    }

    function setupTopicEditor(editor) {
        const input = editor.querySelector('.meeting-topic-add-input');
        const addButton = editor.querySelector('.meeting-topic-add-button');
        editor.querySelectorAll('.meeting-topic-chip.is-removable').forEach(chip => {
            chip.addEventListener('click', () => chip.remove());
        });
        const commit = () => {
            const values = input.value.split(',');
            let added = false;
            values.forEach(value => { added = addTopicToEditor(editor, value) || added; });
            if (added || values.some(value => value.trim())) input.value = '';
            input.focus();
        };
        addButton.addEventListener('click', commit);
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.ctrlKey) {
                event.preventDefault();
                commit();
            } else if (event.key === ',') {
                event.preventDefault();
                commit();
            }
        });
    }

    function collectTopicsFromEditor(row) {
        const editor = row.querySelector('[data-topic-editor]');
        if (!editor) return [];
        const pending = editor.querySelector('.meeting-topic-add-input')?.value || '';
        const topics = Array.from(editor.querySelectorAll('.meeting-topic-edit-chips [data-topic]'))
            .map(chip => chip.dataset.topic);
        pending.split(',').forEach(value => topics.push(value));
        return normalizeTopicList(topics);
    }

    function createMeetingListItem(meeting, index) {
        const listItem = document.createElement('div');
        listItem.className = 'meeting-list-item';
        listItem.setAttribute('data-index', index);
        if (index === selectedMeetingIndex) listItem.classList.add('selected-row');

        const row = document.createElement('div');
        row.className = 'meeting-row';
        const displayDate = formatUserFriendlyDate(getMeetingDate(meeting));
        const meetingName = getMeetingName(meeting, index);
        const topics = getMeetingTopics(meeting);
        const score = getFinalScore(meeting);
        const scoreText = getScoreText(score);
        const scoreClass = getScoreClass(score);
        const isEditing = editingMeetingIndex === index;
        const isSaving = savingMeetingIndex === index;
        const previewText =
            getMeetingSummary(meeting) ||
            getValue(getMeetingFormMetrics(meeting).overall_assessment, '') ||
            normalizeDynamoDBList(meeting.key_wins)[0] ||
            normalizeDynamoDBList(meeting.improvement_areas)[0] ||
            getMeetingTranscript(meeting) ||
            'No preview available.';

        const topicEditorHtml = topics.map(topic =>
            `<button type="button" class="meeting-topic-chip is-removable" data-topic="${escapeHtml(topic)}" aria-label="Remove topic ${escapeHtml(topic)}">${escapeHtml(topic)} ×</button>`
        ).join('');
        const topicChipsHtml = topics.length
            ? `<span class="meeting-topic-chips">${topics.map(topic => `<span class="meeting-topic-chip" data-topic-filter="${escapeHtml(topic)}">${escapeHtml(topic)}</span>`).join('')}</span>`
            : '<span class="meeting-topic-empty">No topics</span>';

        row.innerHTML = `
            <span class="meeting-topline">
                <span class="meeting-date">${escapeHtml(displayDate)}</span>
                <span class="score-mini ${scoreClass}">${escapeHtml(scoreText)}</span>
            </span>
            ${isEditing
                ? `<label class="meeting-edit-field">
                        <span class="meeting-edit-label">Mock interview name</span>
                        <input class="meeting-name-input" type="text" maxlength="200" value="${escapeHtml(meetingName)}" aria-label="Edit mock-interview name" ${isSaving ? 'disabled' : ''}>
                   </label>
                   <label class="meeting-edit-field">
                        <span class="meeting-edit-label">Summary</span>
                        <textarea class="meeting-summary-input" maxlength="5000" aria-label="Edit interview summary" ${isSaving ? 'disabled' : ''}>${escapeHtml(getMeetingSummary(meeting))}</textarea>
                   </label>
                   <div class="meeting-edit-field meeting-topic-editor" data-topic-editor>
                        <span class="meeting-edit-label">Topics</span>
                        <div class="meeting-topic-edit-chips">${topicEditorHtml}</div>
                        <div class="meeting-topic-add-row">
                            <input class="meeting-topic-add-input" type="text" maxlength="60" list="meeting-topic-suggestions" placeholder="Add a topic" aria-label="Add a topic" ${isSaving ? 'disabled' : ''}>
                            <button class="meeting-topic-add-button" type="button" ${isSaving ? 'disabled' : ''}>Add</button>
                        </div>
                        <span class="meeting-edit-help">Use one to three focused topics when possible. Press Enter or comma to add.</span>
                   </div>
                   <span class="meeting-edit-help">Press Ctrl+Enter to save or Escape to cancel.</span>`
                : `<span class="meeting-name">${escapeHtml(meetingName)}</span>
                   ${topicChipsHtml}
                   <span class="meeting-preview">${escapeHtml(truncateText(previewText, 95))}</span>`
            }
        `;

        const actions = document.createElement('div');
        actions.className = 'meeting-list-actions';
        if (isEditing) {
            row.classList.add('editing');
            const nameInput = row.querySelector('.meeting-name-input');
            const summaryInput = row.querySelector('.meeting-summary-input');
            const topicEditor = row.querySelector('[data-topic-editor]');
            setupTopicEditor(topicEditor);
            topicEditor.querySelector('.meeting-topic-add-input')?.addEventListener('keydown', event => {
                if (event.key === 'Enter' && event.ctrlKey) {
                    event.preventDefault();
                    saveMeetingDetails(index, nameInput, summaryInput, row);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    cancelMeetingEdit();
                }
            });
            [nameInput, summaryInput].forEach(input => {
                input.addEventListener('click', event => event.stopPropagation());
                input.addEventListener('keydown', event => {
                    if (event.key === 'Enter' && event.ctrlKey) {
                        event.preventDefault();
                        saveMeetingDetails(index, nameInput, summaryInput, row);
                    } else if (event.key === 'Escape') {
                        event.preventDefault();
                        cancelMeetingEdit();
                    }
                });
            });

            const saveButton = document.createElement('button');
            saveButton.type = 'button';
            saveButton.className = 'meeting-action-button meeting-save-button';
            saveButton.textContent = isSaving ? 'Saving…' : 'Save';
            saveButton.disabled = isSaving;
            saveButton.addEventListener('click', event => {
                event.stopPropagation();
                saveMeetingDetails(index, nameInput, summaryInput, row);
            });

            const cancelButton = document.createElement('button');
            cancelButton.type = 'button';
            cancelButton.className = 'meeting-action-button meeting-cancel-button';
            cancelButton.textContent = 'Cancel';
            cancelButton.disabled = isSaving;
            cancelButton.addEventListener('click', event => {
                event.stopPropagation();
                cancelMeetingEdit();
            });
            actions.append(saveButton, cancelButton);
        } else {
            row.setAttribute('role', 'button');
            row.setAttribute('tabindex', '0');
            row.setAttribute('aria-label', `Open ${meetingName}`);
            row.addEventListener('click', () => updateMeetingReview(index));
            row.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    updateMeetingReview(index);
                }
            });
            row.querySelectorAll('[data-topic-filter]').forEach(chip => {
                chip.addEventListener('click', event => {
                    event.stopPropagation();
                    selectTopicFilter(chip.dataset.topicFilter);
                });
            });

            const editButton = document.createElement('button');
            editButton.type = 'button';
            editButton.className = 'meeting-action-button meeting-edit-button';
            editButton.innerHTML = '&#9998;';
            editButton.title = `Edit name, summary, and topics for ${meetingName}`;
            editButton.setAttribute('aria-label', `Edit name, summary, and topics for ${meetingName}`);
            editButton.addEventListener('click', event => {
                event.stopPropagation();
                startMeetingEdit(index);
            });

            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'meeting-action-button meeting-delete-button';
            deleteButton.innerHTML = '&#128465;';
            deleteButton.title = `Delete ${meetingName}`;
            deleteButton.setAttribute('aria-label', `Delete ${meetingName}`);
            deleteButton.addEventListener('click', event => {
                event.stopPropagation();
                deleteMeeting(index, deleteButton);
            });
            actions.append(editButton, deleteButton);
        }

        listItem.append(row, actions);
        return listItem;
    }

    function startMeetingEdit(index) {
        if (!meetingsData[index] || savingMeetingIndex !== null) return;
        editingMeetingIndex = index;
        renderMeetingList();
    }

    function cancelMeetingEdit() {
        if (savingMeetingIndex !== null) return;
        editingMeetingIndex = null;
        renderMeetingList();
    }

    function setMeetingNameValue(meeting, newName) {
        if (meeting.meeting_name && typeof meeting.meeting_name === 'object' && 'S' in meeting.meeting_name) {
            meeting.meeting_name.S = newName;
        } else {
            meeting.meeting_name = newName;
        }
    }

    function setMeetingSummaryValue(meeting, newSummary) {
        if (meeting.summary && typeof meeting.summary === 'object' && 'S' in meeting.summary) {
            meeting.summary.S = newSummary;
        } else {
            meeting.summary = newSummary;
        }
    }

    function setMeetingTopicsValue(meeting, topics) {
        meeting.topics = normalizeTopicList(topics);
    }

    function getMeetingUpdatePayload(meeting, index, newName, newSummary, newTopics) {
        return {
            meeting_id:
                getValue(meeting.meeting_id, '') ||
                getValue(meeting.transcript_id, '') ||
                getValue(meeting.id, ''),
            timestamp: getMeetingDate(meeting),
            original_meeting_name: getMeetingName(meeting, index),
            original_summary: getMeetingSummary(meeting),
            meeting_name: newName,
            summary: newSummary,
            topics: newTopics
        };
    }

    async function readApiError(response, fallbackMessage) {
        const responseText = await response.text();
        if (!responseText) return fallbackMessage;

        try {
            const errorData = JSON.parse(responseText);
            return errorData.error || errorData.message || fallbackMessage;
        } catch (parseError) {
            return responseText;
        }
    }

    async function updateMeetingDetailsOnServer(payload) {
        const response = await fetch(window.AppUI?.appUrl('/api/career/interview-reviews') || '/api/career/interview-reviews', {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            throw new Error(await readApiError(response, 'The mock-interview details could not be updated.'));
        }
    }

    async function saveMeetingDetails(index, nameInput, summaryInput, row) {
        const meeting = meetingsData[index];
        if (!meeting || savingMeetingIndex !== null) return;

        const newName = nameInput.value.trim();
        const newSummary = summaryInput.value.trim();
        const currentName = getMeetingName(meeting, index);
        const currentSummary = getMeetingSummary(meeting).trim();
        const newTopics = collectTopicsFromEditor(row);
        const currentTopics = getMeetingTopics(meeting);

        if (!newName) {
            window.AppUI?.showToast('The mock-interview name cannot be empty.', {type: 'error'});
            nameInput.focus();
            return;
        }

        const topicsUnchanged = JSON.stringify(newTopics.map(topic => topic.toLocaleLowerCase())) ===
            JSON.stringify(currentTopics.map(topic => topic.toLocaleLowerCase()));
        if (newName === currentName && newSummary === currentSummary && topicsUnchanged) {
            editingMeetingIndex = null;
            renderMeetingList();
            return;
        }

        savingMeetingIndex = index;
        renderMeetingList();

        try {
            const payload = getMeetingUpdatePayload(meeting, index, newName, newSummary, newTopics);
            await updateMeetingDetailsOnServer(payload);

            setMeetingNameValue(meeting, newName);
            setMeetingSummaryValue(meeting, newSummary);
            setMeetingTopicsValue(meeting, newTopics);
            editingMeetingIndex = null;
            savingMeetingIndex = null;

            if (selectedMeetingIndex === index) {
                document.getElementById('summary-content').textContent =
                    newSummary || 'No interview summary provided.';
            }

            refreshMeetingOrganizerControls();
            renderMeetingList();
            if (selectedMeetingIndex === index) renderSelectedMeetingTopics(meeting);
        } catch (error) {
            console.error('Error updating mock-interview details:', error);
            savingMeetingIndex = null;
            renderMeetingList();
            window.AppUI?.showToast(`Unable to update the mock-interview details: ${error.message}`, {type: 'error'});
        }
    }

    function getMeetingDeletePayload(meeting, index) {
        return {
            meeting_id:
                getValue(meeting.meeting_id, '') ||
                getValue(meeting.transcript_id, '') ||
                getValue(meeting.id, ''),
            timestamp: getMeetingDate(meeting),
            meeting_name: getMeetingName(meeting, index)
        };
    }

    async function deleteMeeting(index, deleteButton) {
        const meeting = meetingsData[index];
        if (!meeting) return;

        const meetingName = getMeetingName(meeting, index);
        const meetingDate = formatUserFriendlyDate(getMeetingDate(meeting));
        const confirmed = await window.AppUI.confirm({
            title: 'Delete mock interview?',
            message: `Delete "${meetingName}" from ${meetingDate}? This action cannot be undone.`,
            confirmLabel: 'Delete mock interview',
            danger: true
        });

        if (!confirmed) return;

        const originalButtonContent = deleteButton.innerHTML;
        deleteButton.disabled = true;
        deleteButton.textContent = '…';

        try {
            const payload = getMeetingDeletePayload(meeting, index);

            const response = await fetch(window.AppUI?.appUrl('/api/career/interview-reviews') || '/api/career/interview-reviews', {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                let errorMessage = 'The mock interview could not be deleted.';

                // A Fetch response body can only be consumed once. Read it as
                // text first, then parse that text as JSON when possible.
                const responseText = await response.text();

                if (responseText) {
                    try {
                        const errorData = JSON.parse(responseText);
                        errorMessage = errorData.error || errorData.message || errorMessage;
                    } catch (parseError) {
                        errorMessage = responseText;
                    }
                }

                throw new Error(errorMessage);
            }

            const deletedSelectedMeeting = selectedMeetingIndex === index;

            meetingsData.splice(index, 1);
            refreshMeetingOrganizerControls();

            if (editingMeetingIndex === index) {
                editingMeetingIndex = null;
            } else if (editingMeetingIndex !== null && editingMeetingIndex > index) {
                editingMeetingIndex -= 1;
            }

            if (meetingsData.length === 0) {
                selectedMeetingIndex = null;
                clearMeetingReview();
                renderMeetingList();
                return;
            }

            if (deletedSelectedMeeting) {
                selectedMeetingIndex = Math.min(index, meetingsData.length - 1);
                updateMeetingReview(selectedMeetingIndex);
            } else {
                if (selectedMeetingIndex !== null && selectedMeetingIndex > index) {
                    selectedMeetingIndex -= 1;
                }
                renderMeetingList();
            }
        } catch (error) {
            console.error('Error deleting mock interview:', error);
            window.AppUI?.showToast(`Unable to delete the mock interview: ${error.message}`, {type: 'error'});
            deleteButton.disabled = false;
            deleteButton.innerHTML = originalButtonContent;
        }
    }

    function selectTopicFilter(topic) {
        const filter = document.getElementById('meeting-topic-filter');
        if (!filter) return;
        const requestedKey = String(topic || '').toLocaleLowerCase();
        const option = Array.from(filter.options).find(item =>
            item.value.toLocaleLowerCase() === requestedKey
        );
        filter.value = option?.value || '';
        renderMeetingList();
    }

    function renderSelectedMeetingTopics(meeting) {
        const container = document.getElementById('selected-meeting-topics');
        if (!container) return;
        const topics = meeting ? getMeetingTopics(meeting) : [];
        container.replaceChildren();
        if (topics.length === 0) {
            container.hidden = true;
            return;
        }

        const label = document.createElement('span');
        label.className = 'selected-meeting-topics-label';
        label.textContent = 'Topics';
        container.appendChild(label);
        topics.forEach(topic => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'meeting-topic-chip';
            chip.textContent = topic;
            chip.setAttribute('aria-label', `Filter mock interviews by ${topic}`);
            chip.addEventListener('click', () => selectTopicFilter(topic));
            container.appendChild(chip);
        });
        container.hidden = false;
    }

    function initializeTopicManager() {
        document.getElementById('meeting-topic-close')?.addEventListener('click', closeTopicManager);
        document.getElementById('meeting-topic-modal')?.addEventListener('click', event => {
            if (event.target.id === 'meeting-topic-modal') closeTopicManager();
        });
        document.getElementById('meeting-topic-merge-button')?.addEventListener('click', async () => {
            const source = document.getElementById('meeting-topic-merge-source')?.value || '';
            const target = document.getElementById('meeting-topic-merge-target')?.value || '';
            if (!source || !target || source.toLocaleLowerCase() === target.toLocaleLowerCase()) {
                window.AppUI?.showToast('Choose two different topics to merge.', {type: 'error'});
                return;
            }
            const confirmed = await window.AppUI.confirm({
                title: 'Merge topics?',
                message: `Replace “${source}” with “${target}” across all matching mock interviews?`,
                confirmLabel: 'Merge topics'
            });
            if (confirmed) await applyTopicOperation('merge', source, target);
        });
        document.addEventListener('keydown', event => {
            const modal = document.getElementById('meeting-topic-modal');
            if (event.key === 'Escape' && modal && !modal.hidden) closeTopicManager();
        });
    }

    function openTopicManager() {
        const stats = getTopicStats();
        if (stats.length === 0) {
            window.AppUI?.showToast('Add a topic to a mock interview before opening topic management.', {type: 'error'});
            return;
        }
        const modal = document.getElementById('meeting-topic-modal');
        if (!modal) return;
        topicManagerPreviousFocus = document.activeElement;
        renderTopicManager();
        modal.hidden = false;
        document.body.classList.add('meeting-topic-modal-open');
        modal.querySelector('.meeting-topic-dialog')?.focus();
    }

    function closeTopicManager() {
        if (topicManagerBusy) return;
        const modal = document.getElementById('meeting-topic-modal');
        if (!modal) return;
        modal.hidden = true;
        document.body.classList.remove('meeting-topic-modal-open');
        topicManagerPreviousFocus?.focus?.();
        topicManagerPreviousFocus = null;
    }

    function setTopicManagerBusy(busy) {
        topicManagerBusy = busy;
        const modal = document.getElementById('meeting-topic-modal');
        modal?.querySelectorAll('button, input, select').forEach(control => {
            control.disabled = busy;
        });
        if (!busy) {
            document.getElementById('meeting-topic-merge-button')?.toggleAttribute(
                'disabled',
                getTopicStats().length < 2
            );
        }
        modal?.setAttribute('aria-busy', String(busy));
    }

    function populateTopicSelect(select, stats, selected = '') {
        if (!select) return;
        select.replaceChildren();
        stats.forEach(topic => {
            const option = document.createElement('option');
            option.value = topic.name;
            option.textContent = `${topic.name} (${topic.count})`;
            select.appendChild(option);
        });
        if (selected && stats.some(topic => topic.name === selected)) select.value = selected;
    }

    function renderTopicManager() {
        const stats = getTopicStats();
        const list = document.getElementById('meeting-topic-list');
        const sourceSelect = document.getElementById('meeting-topic-merge-source');
        const targetSelect = document.getElementById('meeting-topic-merge-target');
        if (!list) return;

        populateTopicSelect(sourceSelect, stats, stats[0]?.name || '');
        populateTopicSelect(targetSelect, stats, stats[1]?.name || stats[0]?.name || '');
        document.getElementById('meeting-topic-merge-button')?.toggleAttribute('disabled', stats.length < 2);

        list.replaceChildren();
        if (stats.length === 0) {
            list.innerHTML = '<p class="meeting-topic-empty-state">No topics are currently assigned.</p>';
            return;
        }

        stats.forEach(topic => {
            const row = document.createElement('div');
            row.className = 'meeting-topic-manager-row';

            const count = document.createElement('span');
            count.className = 'meeting-topic-count';
            count.textContent = `${topic.count} ${topic.count === 1 ? 'mock interview' : 'mock interviews'}`;

            const input = document.createElement('input');
            input.type = 'text';
            input.maxLength = 60;
            input.value = topic.name;
            input.setAttribute('aria-label', `Rename ${topic.name}`);

            const rename = document.createElement('button');
            rename.type = 'button';
            rename.className = 'meeting-topic-rename';
            rename.textContent = 'Rename';
            rename.addEventListener('click', async () => {
                const target = input.value.trim().replace(/\s+/g, ' ');
                if (!target || target.toLocaleLowerCase() === topic.name.toLocaleLowerCase()) {
                    input.focus();
                    return;
                }
                await applyTopicOperation('rename', topic.name, target);
            });

            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'meeting-topic-delete';
            remove.textContent = 'Delete';
            remove.addEventListener('click', async () => {
                const confirmed = await window.AppUI.confirm({
                    title: 'Delete topic?',
                    message: `Remove “${topic.name}” from ${topic.count} ${topic.count === 1 ? 'mock interview' : 'mock interviews'}? The mock interviews will not be deleted.`,
                    confirmLabel: 'Delete topic',
                    danger: true
                });
                if (confirmed) await applyTopicOperation('delete', topic.name, '');
            });

            const field = document.createElement('div');
            field.className = 'meeting-topic-manager-field';
            field.append(input, count);
            const actions = document.createElement('div');
            actions.className = 'meeting-topic-manager-actions';
            actions.append(rename, remove);
            row.append(field, actions);
            list.appendChild(row);
        });
    }

    function applyTopicOperationLocally(operation, source, target) {
        const sourceKey = source.toLocaleLowerCase();
        meetingsData.forEach(meeting => {
            const topics = getMeetingTopics(meeting);
            if (!topics.some(topic => topic.toLocaleLowerCase() === sourceKey)) return;
            const transformed = [];
            topics.forEach(topic => {
                if (topic.toLocaleLowerCase() !== sourceKey) transformed.push(topic);
                else if (operation !== 'delete') transformed.push(target);
            });
            setMeetingTopicsValue(meeting, transformed);
        });
    }

    async function applyTopicOperation(operation, source, target) {
        if (topicManagerBusy) return;
        let closeAfterUpdate = false;
        setTopicManagerBusy(true);
        try {
            const response = await fetch(window.AppUI?.appUrl('/api/transcript-topics') || '/api/transcript-topics', {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({operation, source, target})
            });
            if (!response.ok) {
                throw new Error(await readApiError(response, 'The topic update could not be completed.'));
            }
            const result = await response.json().catch(() => ({}));
            applyTopicOperationLocally(operation, source, target);
            refreshMeetingOrganizerControls();
            renderMeetingList();
            if (selectedMeetingIndex !== null) renderSelectedMeetingTopics(meetingsData[selectedMeetingIndex]);
            renderTopicManager();
            window.AppUI?.showToast(
                `${result.updated_meetings || 0} ${Number(result.updated_meetings) === 1 ? 'mock interview updated' : 'mock interviews updated'}.`,
                {type: 'success'}
            );
            closeAfterUpdate = getTopicStats().length === 0;
        } catch (error) {
            window.AppUI?.showToast(error.message || 'The topic update could not be completed.', {type: 'error'});
        } finally {
            setTopicManagerBusy(false);
            if (closeAfterUpdate) closeTopicManager();
        }
    }

    function findRequestedMeetingIndex(requestedMeeting) {
        if (!requestedMeeting) return -1;
        const normalizedRequest = String(requestedMeeting).trim().toLowerCase();

        return meetingsData.findIndex((meeting, index) => {
            const referenceId = getMeetingKnowledgeId(meeting, index).toLowerCase();
            const meetingName = getMeetingName(meeting, index).toLowerCase();
            const meetingDate = String(getMeetingDate(meeting) || '').toLowerCase();
            return referenceId === normalizedRequest || meetingName === normalizedRequest || meetingDate === normalizedRequest;
        });
    }

    function updateActionCenterLink(meeting = null, index = null) {
        const link = document.getElementById('action-center-link');
        if (!link) return;

        const baseUrl = window.AppUI?.appUrl('/career-action-plan') || '/career-action-plan';
        if (!meeting || index === null) {
            link.href = baseUrl;
            return;
        }

        const applicationId = String(getValue(meeting?.career_application_id, '') || '').trim();
        if (applicationId) {
            link.href = `${baseUrl}?application_id=${encodeURIComponent(applicationId)}`;
            link.setAttribute('aria-label', `Manage application actions from ${getMeetingName(meeting, index)} in Career Action Plan`);
        } else {
            link.href = baseUrl;
            link.setAttribute('aria-label', 'Open Career Action Plan');
        }
    }

    function clearMeetingReview() {
        currentTranscript = '';
        updateActionCenterLink();
        document.getElementById('summary-content').textContent =
            'Select a mock interview from the library to view its interview summary, key wins, improvement areas, practice actions, and follow-up questions.';

        document.getElementById('final-weighted-grade').textContent = '-';
        document.getElementById('overall-score-title').textContent = 'Overall Interview Score';
        document.getElementById('overall-evidence-badge').textContent = 'Insufficient evidence';
        document.getElementById('overall-evidence-badge').className = 'score-evidence-badge evidence-insufficient';
        document.getElementById('score-circle-label').textContent = 'out of 100';
        document.getElementById('score-description').textContent =
            'Select a mock interview to view the eight interview-specific criteria and answer-by-answer coaching.';

        const scoreCard = document.getElementById('overall-score-card');
        scoreCard.classList.remove('score-green', 'score-orange', 'score-neutral');
        scoreCard.classList.add('score-neutral');
        updateScoreCircle(null);

        clearInterviewScorecard();

        document.getElementById('transcript-search').value = '';
        document.getElementById('transcript-content').textContent =
            'Select a mock interview from the library to view the full transcript.';
        clearMeetingAskContext();
        renderSelectedMeetingTopics(null);
    }

    function updateMeetingReview(index) {
        selectedMeetingIndex = index;

        const meeting = meetingsData[index];
        const finalScore = getFinalScore(meeting);
        updateActionCenterLink(meeting, index);

        const scoreCard = document.getElementById('overall-score-card');
        scoreCard.classList.remove('score-green', 'score-orange', 'score-red', 'score-neutral');
        scoreCard.classList.add(getScoreCardClass(finalScore));

        document.getElementById('final-weighted-grade').textContent = getScoreText(finalScore);
        renderInterviewScorecard(meeting);
        document.getElementById('score-description').textContent = getScoreDescription(finalScore, meeting);
        updateScoreCircle(finalScore);

        document.getElementById('summary-content').textContent = getMeetingSummary(meeting) || 'No interview summary provided.';
        renderSelectedMeetingTopics(meeting);

        currentTranscript = getMeetingTranscript(meeting) || 'No transcript available.';
        document.getElementById('transcript-search').value = '';
        renderTranscriptContent();

        renderMeetingList();
        renderKeyWins(meeting);
        renderImprovementAreas(meeting);
        renderActionItems(meeting, index);
        renderOpenQuestions(meeting);
        updateMeetingAskContext(meeting, index);
    }

    function switchTab(tabName, updateUrl = true) {
        currentTab = tabName;
        ['summary', 'scorecard', 'transcript', 'ask-ai'].forEach(name => {
            const tab = document.getElementById(`${name}-tab`);
            const button = document.getElementById(`${name}-tab-button`);
            const selected = name === tabName;
            tab.classList.toggle('active', selected);
            tab.hidden = !selected;
            button.classList.toggle('active', selected);
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
        });

        if (updateUrl) {
            const url = new URL(window.location.href);
            if (tabName === 'summary') url.searchParams.delete('tab');
            else url.searchParams.set('tab', tabName);
            window.history.replaceState({}, '', url);
        }
    }

    

    

    function renderKeyWins(meeting) {
        const winsEl = document.getElementById('key-wins-list');
        renderSimpleList(winsEl, meeting.key_wins, 'No key wins documented.');
    }

    function renderImprovementAreas(meeting) {
        const improvementEl = document.getElementById('improvement-areas-list');
        renderSimpleList(improvementEl, meeting.improvement_areas, 'No improvement areas documented.');
    }

    function renderActionItems(meeting, meetingIndex) {
        const actionsEl = document.getElementById('action-items-list');
        actionsEl.innerHTML = '';

        const meetingId = getMeetingKnowledgeId(meeting, meetingIndex);
        const managedActions = actionsData.filter(action =>
            String(getValue(action?.meeting_id, '')) === meetingId
        );
        const actions = managedActions.length > 0
            ? managedActions
            : normalizeDynamoDBList(meeting.action_items);

        if (actions.length === 0) {
            actionsEl.innerHTML = `<li class="empty-message">No practice actions were generated.</li>`;
            return;
        }

        actions.forEach(item => {
            const li = document.createElement('li');
            li.className = 'meeting-action-summary-item';
            const itemData = item && item.S ? item.S : item;
            const objectValue = itemData && typeof itemData === 'object' ? itemData : null;
            const task = objectValue
                ? (getValue(objectValue.description, '') || getValue(objectValue.task, '') || getValue(objectValue.text, '') || getValue(objectValue.action, '') || JSON.stringify(objectValue))
                : String(itemData);
            const dueDate = objectValue
                ? (getValue(objectValue.due_date, '') || getValue(objectValue.deadline, '') || getValue(objectValue.due, ''))
                : '';
            const priority = objectValue ? String(getValue(objectValue.priority, '')).toLowerCase() : '';
            const status = objectValue ? String(getValue(objectValue.status, '')).toLowerCase() : '';
            const actionId = objectValue
                ? String(getValue(objectValue.action_id, '') || getValue(objectValue.id, ''))
                : '';

            const taskRow = document.createElement('span');
            taskRow.className = 'meeting-action-task-row';

            if (actionId) {
                const completion = document.createElement('input');
                completion.type = 'checkbox';
                completion.className = 'meeting-action-completion';
                completion.checked = status === 'done';
                completion.setAttribute('aria-label', `${completion.checked ? 'Reopen' : 'Complete'} ${task}`);
                completion.addEventListener('change', () => {
                    updateMeetingActionStatus(actionId, completion.checked ? 'done' : 'not_started', meeting, meetingIndex, completion);
                });
                taskRow.appendChild(completion);
            }

            const taskElement = document.createElement('span');
            taskElement.className = `meeting-action-task${status === 'done' ? ' is-complete' : ''}`;
            taskElement.textContent = task;
            taskRow.appendChild(taskElement);
            li.appendChild(taskRow);

            const metadata = document.createElement('span');
            metadata.className = 'meeting-action-meta';


            if (dueDate) {
                const dueBadge = document.createElement('span');
                dueBadge.className = 'meeting-action-detail-badge';
                dueBadge.textContent = `Due ${formatUserFriendlyDate(dueDate)}`;
                metadata.appendChild(dueBadge);
            }

            if (priority && priority !== 'none' && priority !== 'n/a') {
                const priorityBadge = document.createElement('span');
                priorityBadge.className = `meeting-action-detail-badge meeting-action-priority-${priority}`;
                priorityBadge.textContent = `${priority.charAt(0).toUpperCase()}${priority.slice(1)} priority`;
                metadata.appendChild(priorityBadge);
            }

            if (status && status !== 'n/a') {
                const statusBadge = document.createElement('span');
                statusBadge.className = 'meeting-action-detail-badge';
                statusBadge.textContent = status.replace(/_/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase());
                metadata.appendChild(statusBadge);
            }

            li.appendChild(metadata);
            actionsEl.appendChild(li);
        });
    }

    async function updateMeetingActionStatus(actionId, status, meeting, meetingIndex, checkbox) {
        const action = actionsData.find(item => String(getValue(item?.action_id, '') || getValue(item?.id, '')) === actionId);
        if (!action) return;

        const previousStatus = String(getValue(action.status, '') || 'not_started');
        action.status = status;
        checkbox.disabled = true;
        renderActionItems(meeting, meetingIndex);

        try {
            const response = await fetch(
                `${window.AppUI?.appUrl('/api/career/actions') || '/api/career/actions'}/${encodeURIComponent(actionId)}`,
                {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                    body: JSON.stringify({status})
                }
            );
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.error || 'The action status could not be updated.');
            }
            const updated = await response.json();
            const index = actionsData.indexOf(action);
            if (index >= 0) actionsData[index] = updated;
            renderActionItems(meeting, meetingIndex);
            window.AppUI?.showToast(status === 'done' ? 'Action completed.' : 'Action reopened.', {type: 'success'});
        } catch (error) {
            action.status = previousStatus;
            renderActionItems(meeting, meetingIndex);
            window.AppUI?.showToast(error.message || 'The action status could not be updated.', {type: 'error'});
        }
    }

    function renderOpenQuestions(meeting) {
        const questionsEl = document.getElementById('open-questions-list');
        questionsEl.innerHTML = '';

        const questions = normalizeDynamoDBList(meeting.open_questions);

        if (questions.length === 0) {
            questionsEl.innerHTML = `<li class="empty-message">No remaining open questions found.</li>`;
            return;
        }

        questions.forEach(q => {
            const li = document.createElement('li');
            const questionData = q && q.S ? q.S : q;

            li.textContent = typeof questionData === 'object'
                ? (getValue(questionData.question, '') || getValue(questionData.text, '') || JSON.stringify(questionData))
                : questionData;

            questionsEl.appendChild(li);
        });
    }

    function renderSimpleList(container, field, emptyMessage) {
        container.innerHTML = '';

        const items = normalizeDynamoDBList(field);

        if (items.length === 0) {
            container.innerHTML = `<li class="empty-message">${escapeHtml(emptyMessage)}</li>`;
            return;
        }

        items.forEach(item => {
            const li = document.createElement('li');
            const itemData = item && item.S ? item.S : item;

            li.textContent = typeof itemData === 'object'
                ? (getValue(itemData.text, '') || getValue(itemData.description, '') || JSON.stringify(itemData))
                : itemData;

            container.appendChild(li);
        });
    }

    function renderTranscriptContent() {
        const transcriptEl = document.getElementById('transcript-content');
        const searchValue = document.getElementById('transcript-search').value.trim();

        if (!currentTranscript) {
            transcriptEl.textContent = 'No transcript available.';
            return;
        }

        if (!searchValue) {
            transcriptEl.textContent = currentTranscript;
            return;
        }

        const escapedTranscript = escapeHtml(currentTranscript);
        const escapedSearch = escapeHtml(searchValue);
        const safeRegexText = escapedSearch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(safeRegexText, 'gi');

        transcriptEl.innerHTML = escapedTranscript.replace(regex, match => `<mark>${match}</mark>`);
    }

    function copyTranscript(event) {
        if (!currentTranscript || currentTranscript === 'No transcript available.') return;

        navigator.clipboard.writeText(currentTranscript).then(() => {
            const button = event.currentTarget;
            const originalText = button.textContent;
            button.textContent = 'Copied';

            setTimeout(() => {
                button.textContent = originalText;
            }, 1400);
        }).catch(error => {
            console.error('Unable to copy transcript:', error);
        });
    }

    function toggleTranscriptFullscreen() {
        const transcriptCard = document.getElementById('transcript-card');
        const button = document.getElementById('fullscreen-button');

        transcriptCard.classList.toggle('fullscreen-active');
        button.setAttribute('aria-pressed', String(transcriptCard.classList.contains('fullscreen-active')));

        if (transcriptCard.classList.contains('fullscreen-active')) {
            button.textContent = '✕ Close Fullscreen';
        } else {
            button.textContent = '⛶ Fullscreen';
        }
    }

    function getMeetingKnowledgeId(meeting, index) {
        return String(
            getValue(meeting?.meeting_id, '') ||
            getValue(meeting?.transcript_id, '') ||
            getValue(meeting?.id, '') ||
            getMeetingDate(meeting) ||
            `meeting-${index}`
        );
    }

    function clearMeetingAskContext() {
        const name = document.getElementById('ask-ai-meeting-name');
        const date = document.getElementById('ask-ai-meeting-date');
        const question = document.getElementById('meeting-ask-question');
        const answerPanel = document.getElementById('meeting-answer-panel');
        if (name) name.textContent = 'No mock interview selected';
        if (date) date.textContent = 'Select a mock interview from the library before asking a question.';
        if (question) question.placeholder = 'Select a mock interview before asking a question...';
        if (answerPanel) answerPanel.hidden = true;
    }

    function updateMeetingAskContext(meeting, index) {
        const name = document.getElementById('ask-ai-meeting-name');
        const date = document.getElementById('ask-ai-meeting-date');
        const question = document.getElementById('meeting-ask-question');
        const meetingName = getMeetingName(meeting, index);
        const meetingDate = getMeetingDate(meeting);
        if (name) name.textContent = meetingName;
        if (date) date.textContent = meetingDate ? `Recorded ${formatUserFriendlyDate(meetingDate)}` : 'Ready for contextual questions.';
        if (question) question.placeholder = `Ask a question about ${meetingName}...`;
    }

    function getMeetingAskScopeSummary(includeRelated) {
        return includeRelated
            ? 'Using the selected mock interview plus Career Evidence Library documents and previous practice sessions.'
            : 'Using only the selected mock interview.';
    }

    function renderMeetingAnswerSources(sources) {
        const sourcesSection = document.getElementById('meeting-answer-sources');
        const sourceList = document.getElementById('meeting-source-list');
        if (!sourcesSection || !sourceList) return;

        sourceList.replaceChildren();
        const normalized = Array.isArray(sources) ? sources : [];
        normalized.forEach((source, index) => {
            const chip = document.createElement('span');
            chip.className = 'meeting-source-chip';
            const title = source.meeting_name || source.title || source.filename || `Source ${index + 1}`;
            const detail = source.date || source.timestamp || source.section || '';
            chip.textContent = `${index + 1}. ${title}${detail ? ` · ${detail}` : ''}`;
            sourceList.appendChild(chip);
        });
        sourcesSection.hidden = normalized.length === 0;
    }

    document.addEventListener('DOMContentLoaded', () => {
        const form = document.getElementById('meeting-ask-form');
        const question = document.getElementById('meeting-ask-question');
        const submit = document.getElementById('meeting-ask-submit');
        const scopeSummary = document.getElementById('meeting-ask-scope-summary');
        const answerPanel = document.getElementById('meeting-answer-panel');
        const answerContent = document.getElementById('meeting-answer-content');

        const includeRelated = document.getElementById('meeting-include-related');
        includeRelated?.addEventListener('change', () => {
            if (scopeSummary) {
                scopeSummary.textContent = getMeetingAskScopeSummary(includeRelated.checked);
            }
        });

        document.querySelectorAll('[data-meeting-ask-suggestion]').forEach(button => {
            button.addEventListener('click', () => {
                if (!question) return;
                question.value = button.dataset.meetingAskSuggestion || '';
                question.focus();
            });
        });

        form?.addEventListener('submit', async event => {
            event.preventDefault();
            const prompt = question?.value.trim();
            const useRelatedKnowledge = Boolean(includeRelated?.checked);
            const scope = useRelatedKnowledge ? 'meeting_review_related' : 'this_meeting';

            if (!prompt || !submit) return;
            if (selectedMeetingIndex === null) {
                window.AppUI?.showToast('Select a mock interview before asking a question.', {type: 'error'});
                return;
            }

            const meeting = meetingsData[selectedMeetingIndex];
            const meetingId = getMeetingKnowledgeId(meeting, selectedMeetingIndex);
            const payload = {
                question: prompt,
                source_scope: scope,
                search_scope: scope,
                meeting_id: meetingId,
                meeting_ids: [meetingId],
                include_related_knowledge: useRelatedKnowledge
            };

            submit.disabled = true;
            submit.textContent = 'Searching...';
            answerPanel.hidden = false;
            answerPanel.setAttribute('aria-busy', 'true');
            answerContent.textContent = useRelatedKnowledge
                ? 'Searching this mock interview and related knowledge...'
                : 'Searching the selected mock interview...';
            document.getElementById('meeting-answer-sources').hidden = true;

            try {
                const endpoint = window.AppUI?.appUrl('/api/career/evidence/search') || '/api/career/evidence/search';
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const result = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(result.error || 'The interview question could not be answered.');
                answerContent.textContent = result.answer || 'No answer was returned.';
                renderMeetingAnswerSources(result.sources || result.meeting_sources || []);
            } catch (error) {
                answerContent.textContent = error.message;
                window.AppUI?.showToast(error.message, {type: 'error'});
            } finally {
                answerPanel.setAttribute('aria-busy', 'false');
                submit.disabled = false;
                submit.textContent = 'Ask about interview';
            }
        });

        document.getElementById('meeting-answer-copy')?.addEventListener('click', async () => {
            try {
                await navigator.clipboard.writeText(answerContent?.textContent || '');
                window.AppUI?.showToast('Answer copied.', {type: 'success'});
            } catch {
                window.AppUI?.showToast('The answer could not be copied.', {type: 'error'});
            }
        });

        clearMeetingAskContext();
    });
