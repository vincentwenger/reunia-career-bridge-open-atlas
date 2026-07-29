'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const appUrl = window.AppUI?.appUrl || (path => path);
    const statusElement = document.getElementById('streamStatus');
    const tableContainer = document.getElementById('table-container');
    const fullscreenWrapper = document.getElementById('fullscreen-wrapper');
    const newEntryJumpButton = document.getElementById('new-entry-jump');
    const newEntryStatus = document.getElementById('new-entry-status');

    const NEW_ENTRY_HIGHLIGHT_MS = 8000;
    const VIEWED_DELAY_MS = 1400;
    const LATEST_POSITION_THRESHOLD_PX = 56;

    let lastStreamedEntries = [];
    let fullscreenTrigger = null;
    let hasReceivedInitialPayload = false;
    let rowVisibilityObserver = null;
    let eventSource = null;

    const knownEntryIds = new Set();
    const unreadEntryIds = new Set();
    const highlightUntilById = new Map();
    const highlightTimersById = new Map();
    const viewedTimersById = new Map();

    function setStatus(message, state = 'connecting') {
        if (!statusElement) return;
        const icon = state === 'connected'
            ? '🟢'
            : state === 'error'
                ? '🔴'
                : state === 'paused'
                    ? '⚪'
                    : '🟡';
        statusElement.textContent = `${icon} ${message}`;
        statusElement.dataset.state = state;
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function entryId(entry, index = 0) {
        const explicitId = String(entry?.id || entry?.entry_id || '').trim();
        if (explicitId) return explicitId;

        return [
            entry?.timestamp || '',
            entry?.origin || '',
            entry?.content || '',
            index,
        ].join('|');
    }

    function formatEntryTime(value) {
        if (!value) return '';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';

        return new Intl.DateTimeFormat(window.AppI18n?.locale || undefined, {
            hour: 'numeric',
            minute: '2-digit',
        }).format(date);
    }

    function findRenderedRow(id) {
        return Array.from(tableContainer.querySelectorAll('tr[data-entry-id]'))
            .find(row => row.dataset.entryId === id) || null;
    }

    function clearViewedTimer(id) {
        const timer = viewedTimersById.get(id);
        if (timer) window.clearTimeout(timer);
        viewedTimersById.delete(id);
    }

    function markEntryViewed(id) {
        clearViewedTimer(id);
        if (!unreadEntryIds.delete(id)) return;

        const row = findRenderedRow(id);
        row?.classList.remove('is-unread');
        row?.querySelector('[data-new-entry-badge]')?.remove();
        updateNewEntryJumpButton();
    }

    function startViewedTimer(id) {
        if (!unreadEntryIds.has(id) || viewedTimersById.has(id) || document.hidden) return;

        const timer = window.setTimeout(() => {
            markEntryViewed(id);
        }, VIEWED_DELAY_MS);
        viewedTimersById.set(id, timer);
    }

    function resetUnreadRowObserver() {
        rowVisibilityObserver?.disconnect();
        rowVisibilityObserver = null;
        viewedTimersById.forEach(timer => window.clearTimeout(timer));
        viewedTimersById.clear();

        const unreadRows = Array.from(tableContainer.querySelectorAll('tr.is-unread[data-entry-id]'));
        if (unreadRows.length === 0 || !('IntersectionObserver' in window)) return;

        rowVisibilityObserver = new IntersectionObserver(observations => {
            observations.forEach(observation => {
                const id = observation.target.dataset.entryId;
                if (!id) return;

                if (observation.isIntersecting && observation.intersectionRatio >= 0.65) {
                    startViewedTimer(id);
                } else {
                    clearViewedTimer(id);
                }
            });
        }, {
            root: tableContainer,
            threshold: [0.65],
        });

        unreadRows.forEach(row => rowVisibilityObserver.observe(row));
    }

    function scheduleHighlightRemoval(id) {
        const existingTimer = highlightTimersById.get(id);
        if (existingTimer) window.clearTimeout(existingTimer);

        const expiresAt = highlightUntilById.get(id) || Date.now();
        const delay = Math.max(0, expiresAt - Date.now());
        const timer = window.setTimeout(() => {
            highlightUntilById.delete(id);
            highlightTimersById.delete(id);
            findRenderedRow(id)?.classList.remove('is-new-highlight');
        }, delay + 25);

        highlightTimersById.set(id, timer);
    }

    function registerStreamEntries(entries) {
        const currentIds = new Set(entries.map((entry, index) => entryId(entry, index)));

        unreadEntryIds.forEach(id => {
            if (!currentIds.has(id)) unreadEntryIds.delete(id);
        });

        if (!hasReceivedInitialPayload) {
            currentIds.forEach(id => knownEntryIds.add(id));
            hasReceivedInitialPayload = true;
            return [];
        }

        const newlyReceivedIds = [];
        currentIds.forEach(id => {
            if (knownEntryIds.has(id)) return;
            knownEntryIds.add(id);
            unreadEntryIds.add(id);
            highlightUntilById.set(id, Date.now() + NEW_ENTRY_HIGHLIGHT_MS);
            scheduleHighlightRemoval(id);
            newlyReceivedIds.push(id);
        });

        if (newlyReceivedIds.length > 0 && newEntryStatus) {
            const count = newlyReceivedIds.length;
            newEntryStatus.textContent = `${count} new Live Q&A response${count === 1 ? '' : 's'} received.`;
        }

        return newlyReceivedIds;
    }

    function isNearLatestEntry() {
        return tableContainer.scrollTop <= LATEST_POSITION_THRESHOLD_PX;
    }

    function captureScrollState() {
        const containerRect = tableContainer.getBoundingClientRect();
        const rows = Array.from(tableContainer.querySelectorAll('tbody tr[data-entry-id]'));
        const anchorRow = rows.find(row => row.getBoundingClientRect().bottom > containerRect.top + 1);

        return {
            nearLatest: isNearLatestEntry(),
            scrollTop: tableContainer.scrollTop,
            anchorId: anchorRow?.dataset.entryId || '',
            anchorOffset: anchorRow
                ? anchorRow.getBoundingClientRect().top - containerRect.top
                : 0,
        };
    }

    function restoreScrollState(scrollState, hasNewEntries) {
        if (hasNewEntries && scrollState.nearLatest) {
            tableContainer.scrollTo({
                top: 0,
                behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
                    ? 'auto'
                    : 'smooth',
            });
            return;
        }

        if (scrollState.anchorId) {
            const anchorRow = findRenderedRow(scrollState.anchorId);
            if (anchorRow) {
                const containerRect = tableContainer.getBoundingClientRect();
                const currentOffset = anchorRow.getBoundingClientRect().top - containerRect.top;
                tableContainer.scrollTop += currentOffset - scrollState.anchorOffset;
                return;
            }
        }

        tableContainer.scrollTop = scrollState.scrollTop;
    }

    function visibleUnreadCount() {
        return tableContainer.querySelectorAll('tr.is-unread[data-entry-id]').length;
    }

    function updateNewEntryJumpButton() {
        if (!newEntryJumpButton) return;

        const count = visibleUnreadCount();
        const shouldShow = count > 0 && !isNearLatestEntry();
        newEntryJumpButton.hidden = !shouldShow;
        newEntryJumpButton.textContent = `↑ ${count} new response${count === 1 ? '' : 's'}`;
        newEntryJumpButton.setAttribute(
            'aria-label',
            `Go to ${count} new Live Q&A response${count === 1 ? '' : 's'}`,
        );
    }

    function renderData(entries, {hasNewEntries = false} = {}) {
        const scrollState = captureScrollState();
        tableContainer.setAttribute('aria-busy', 'false');

        if (!Array.isArray(entries) || entries.length === 0) {
            tableContainer.innerHTML = `
                <div class="no-data">
                    <strong>Ready for action</strong>
                    Use the Browser Recorder or a connected live source to watch questions and AI answers appear here.
                </div>
            `;
            updateNewEntryJumpButton();
            return;
        }

        const checkedOrigins = Array.from(document.querySelectorAll('[data-source-filter]:checked'))
            .map(checkbox => checkbox.value.toLowerCase());
        const filteredEntries = [...entries].reverse().filter(entry => {
            const originValue = String(entry?.origin || '').toLowerCase().trim();
            return checkedOrigins.includes(originValue);
        });

        if (filteredEntries.length === 0) {
            tableContainer.innerHTML = `
                <div class="no-data">
                    <strong>No matching entries</strong>
                    Adjust your source filters to display more live meeting content.
                </div>
            `;
            updateNewEntryJumpButton();
            return;
        }

        const now = Date.now();
        const rows = filteredEntries.map((entry, index) => {
            const id = entryId(entry, index);
            const source = entry?.answer_source || {};
            const sourceLabel = source.name
                ? `${source.name}${source.detail ? ` · ${source.detail}` : ''}`
                : '';
            const sourceDescription = source.type === 'prepared_answer'
                ? 'Prepared answer source'
                : 'Application material source';
            const sourceIcon = sourceLabel
                ? `<span class="answer-source-icon" tabindex="0" role="img" aria-label="${sourceDescription}: ${escapeHtml(sourceLabel)}" data-tooltip="${escapeHtml(sourceLabel)}">📎</span>`
                : '';
            const isUnread = unreadEntryIds.has(id);
            const isHighlighted = (highlightUntilById.get(id) || 0) > now;
            const rowClasses = [
                'feed-entry',
                isUnread ? 'is-unread' : '',
                isHighlighted ? 'is-new-highlight' : '',
            ].filter(Boolean).join(' ');
            const timeLabel = formatEntryTime(entry?.timestamp);
            const newBadge = isUnread
                ? '<span class="new-entry-badge" data-new-entry-badge>New</span>'
                : '';

            return `
                <tr class="${rowClasses}" data-entry-id="${escapeHtml(id)}" tabindex="-1">
                    <td class="source-col">
                        <div class="source-meta">
                            <span class="source-badge">${escapeHtml(entry.origin)}</span>
                            <span class="entry-received-meta">${timeLabel ? `<time datetime="${escapeHtml(entry.timestamp)}">${escapeHtml(timeLabel)}</time>` : ''}${newBadge}</span>
                        </div>
                    </td>
                    <td class="question-col">${escapeHtml(entry.content)}</td>
                    <td class="answer-cell answer-col"><span class="answer-text">${escapeHtml(entry.chatgpt_answer)}</span>${sourceIcon}</td>
                </tr>
            `;
        }).join('');

        tableContainer.innerHTML = `
            <table>
                <thead>
                    <tr>
                        <th scope="col" class="source-col">Source</th>
                        <th scope="col" class="question-col">Question / Input</th>
                        <th scope="col" class="answer-col">AI Answer</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;

        restoreScrollState(scrollState, hasNewEntries);
        resetUnreadRowObserver();
        updateNewEntryJumpButton();
    }

    function toggleFullscreen(event) {
        fullscreenTrigger = event?.currentTarget || fullscreenTrigger;
        const isFullscreen = fullscreenWrapper.classList.toggle('fullscreen-active');
        document.body.classList.toggle('fullscreen-mode', isFullscreen);
        fullscreenWrapper.setAttribute('aria-expanded', String(isFullscreen));
        if (isFullscreen) {
            fullscreenWrapper.querySelector('.fullscreen-close-btn')?.focus();
        } else {
            fullscreenTrigger?.focus?.();
        }
        window.requestAnimationFrame(() => {
            resetUnreadRowObserver();
            updateNewEntryJumpButton();
        });
    }

    document.querySelectorAll('[data-source-filter]').forEach(checkbox => {
        checkbox.addEventListener('change', () => renderData(lastStreamedEntries));
    });
    document.querySelectorAll('[data-toggle-fullscreen]').forEach(button => {
        button.addEventListener('click', toggleFullscreen);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && fullscreenWrapper.classList.contains('fullscreen-active')) {
            toggleFullscreen();
        }
    });

    tableContainer.addEventListener('scroll', updateNewEntryJumpButton, {passive: true});
    newEntryJumpButton?.addEventListener('click', () => {
        tableContainer.scrollTo({
            top: 0,
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
                ? 'auto'
                : 'smooth',
        });
        tableContainer.querySelector('tr.is-unread[data-entry-id]')?.focus?.({preventScroll: true});
    });

    function closeLiveStream() {
        if (!eventSource) return;
        eventSource.close();
        eventSource = null;
    }

    function connectLiveStream() {
        if (document.hidden || eventSource) return;

        setStatus('Connecting to live interview assistance...', 'connecting');
        const source = new EventSource(appUrl('/stream-ui'));
        eventSource = source;

        source.onopen = () => {
            if (source !== eventSource) return;
            setStatus('Live assistance connected. Waiting for interview audio...', 'connected');
        };
        source.onmessage = event => {
            if (source !== eventSource) return;
            try {
                const parsed = JSON.parse(event.data);
                if (!Array.isArray(parsed)) throw new TypeError('The stream payload is not a list.');
                const newlyReceivedIds = registerStreamEntries(parsed);
                lastStreamedEntries = parsed;
                renderData(lastStreamedEntries, {hasNewEntries: newlyReceivedIds.length > 0});
                setStatus(`Live stream connected. ${parsed.length} update${parsed.length === 1 ? '' : 's'} received.`, 'connected');
            } catch (error) {
                console.error('Ignoring malformed live-stream message:', error);
                setStatus('A malformed update was ignored. The stream is still connected.', 'error');
            }
        };
        source.onerror = error => {
            if (source !== eventSource) return;
            console.error('SSE connection error. Reconnecting...', error);
            setStatus('Connection interrupted. Reconnecting automatically...', 'error');
        };
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            closeLiveStream();
            setStatus('Live updates paused while this tab is hidden.', 'paused');
            return;
        }

        connectLiveStream();
        resetUnreadRowObserver();
    });

    window.addEventListener('beforeunload', closeLiveStream);
    renderData([]);
    connectLiveStream();
});
