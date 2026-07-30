(function () {
    'use strict';

    const page = document.getElementById('mockInterviewPage');
    if (!page) return;

    const setupPanel = document.getElementById('mockSetupPanel');
    const sessionLayout = document.getElementById('mockSessionLayout');
    const completePanel = document.getElementById('mockCompletePanel');
    const workspaceSelect = document.getElementById('applicationWorkspaceSelect');
    const workspaceHelp = document.getElementById('applicationWorkspaceHelp');
    const questionCountSelect = document.getElementById('questionCountSelect');
    const readQuestionsAloud = document.getElementById('readQuestionsAloud');
    const customFocusField = document.getElementById('customFocusField');
    const customFocusInput = document.getElementById('customFocusInput');
    const startInterviewButton = document.getElementById('startMockInterviewButton');
    const endInterviewButton = document.getElementById('endInterviewButton');
    const repeatQuestionButton = document.getElementById('repeatQuestionButton');
    const startAnswerButton = document.getElementById('startAnswerButton');
    const finishAnswerButton = document.getElementById('finishAnswerButton');
    const retryAnswerButton = document.getElementById('retryAnswerButton');
    const nextQuestionButton = document.getElementById('nextQuestionButton');
    const practiceAgainButton = document.getElementById('practiceAgainButton');
    const openReviewLink = document.getElementById('openInterviewReviewLink');

    const statusBadge = document.getElementById('mockStatusBadge');
    const statusText = document.getElementById('mockStatusText');
    const sessionFormat = document.getElementById('mockSessionFormat');
    const questionProgress = document.getElementById('mockQuestionProgress');
    const progressFill = document.getElementById('mockProgressFill');
    const questionHeading = document.getElementById('mock-question-heading');
    const questionContext = document.getElementById('mockQuestionContext');
    const answerStateTitle = document.getElementById('answerStateTitle');
    const answerTimer = document.getElementById('answerTimer');
    const microphoneCard = document.getElementById('microphoneCard');
    const microphoneState = document.getElementById('microphoneState');
    const microphoneMeter = document.getElementById('microphoneMeter');
    const processingPanel = document.getElementById('mockProcessingPanel');
    const processingTitle = processingPanel?.querySelector('[data-state-title]');
    const processingMessage = processingPanel?.querySelector('[data-state-message]');
    const evaluationPanel = document.getElementById('mockEvaluationPanel');
    const evaluationScore = document.getElementById('evaluationScore');
    const evaluationEvidenceBadge = document.getElementById('evaluationEvidenceBadge');
    const evaluationSummary = document.getElementById('evaluationSummary');
    const evaluationStrengths = document.getElementById('evaluationStrengths');
    const evaluationImprovements = document.getElementById('evaluationImprovements');
    const errorPanel = document.getElementById('mockErrorPanel');
    const errorMessage = errorPanel?.querySelector('[data-state-message]');
    const historyCount = document.getElementById('mockHistoryCount');
    const historyList = document.getElementById('mockHistoryList');
    const completeMessage = document.getElementById('mockCompleteMessage');

    const storageScope = encodeURIComponent(page.dataset.storageScope || 'default');
    const ACTIVE_SESSION_KEY = `careerBridge.activeMockInterview.v2.${storageScope}`;
    const reviewUrl = page.dataset.reviewUrl || '/interview-review';

    let currentSession = null;
    let pendingNextSession = null;
    let microphoneStream = null;
    let mediaRecorder = null;
    let recordedChunks = [];
    let answerStartedAt = 0;
    let timerId = null;
    let audioContext = null;
    let meterAnimation = null;
    let lastAnswerBlob = null;
    let lastAnswerFilename = 'mock-interview-answer.webm';
    let lastAnswerDurationSeconds = null;
    let phase = 'ready';

    document.querySelectorAll('input[name="interviewType"]').forEach(function (input) {
        input.addEventListener('change', updateCustomFocusVisibility);
    });
    startInterviewButton.addEventListener('click', startInterview);
    startAnswerButton.addEventListener('click', startAnswerRecording);
    finishAnswerButton.addEventListener('click', finishAnswerRecording);
    retryAnswerButton.addEventListener('click', retryLastAnswer);
    nextQuestionButton.addEventListener('click', continueToNextQuestion);
    repeatQuestionButton.addEventListener('click', function () {
        speakQuestion(currentSession?.current_question || pendingNextSession?.current_question || '');
    });
    endInterviewButton.addEventListener('click', discardCurrentInterview);
    practiceAgainButton.addEventListener('click', resetForNewInterview);

    window.addEventListener('beforeunload', function (event) {
        if (!['recording', 'submitting', 'completing'].includes(phase)) return;
        event.preventDefault();
        event.returnValue = '';
    });
    window.addEventListener('pagehide', cleanupMicrophone);

    updateCustomFocusVisibility();
    initialize();

    async function initialize() {
        await loadApplicationWorkspaces();
        await resumeActiveSession();
        ensureBrowserSupport();
    }

    async function loadApplicationWorkspaces() {
        if (!workspaceSelect) return;
        try {
            const response = await fetch(AppUI.appUrl('/api/career/mock-interviews/applications'), {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'},
                cache: 'no-store'
            });
            if (!response.ok) throw new Error('Application workspaces could not be loaded.');
            const payload = await response.json();
            const workspaces = Array.isArray(payload.applications)
                ? payload.applications
                : [];
            const activeId = String(payload.active_application_context_id || '');
            workspaceSelect.replaceChildren(new Option('Practice without linking a job application', ''));
            workspaces.forEach(function (workspace) {
                const title = String(workspace.title || 'Untitled application');
                const purpose = String(workspace.purpose || '').trim();
                const label = purpose ? `${title} · ${truncate(purpose, 58)}` : title;
                workspaceSelect.add(new Option(label, String(workspace.id || '')));
            });
            if (activeId && workspaces.some((item) => String(item.id || '') === activeId)) {
                workspaceSelect.value = activeId;
            }
            workspaceHelp.textContent = workspaces.length
                ? 'Questions can use the selected role, application context, and verified career evidence.'
                : 'No job application is available yet. You can still practice, or create one in Application Builder.';
        } catch (error) {
            workspaceHelp.textContent = 'Job applications are temporarily unavailable. You can still practice without linking one.';
        }
    }

    async function resumeActiveSession() {
        const sessionId = readActiveSessionId();
        if (!sessionId) return;
        try {
            const response = await fetch(AppUI.appUrl(`/api/career/mock-interviews/adaptive/sessions/${encodeURIComponent(sessionId)}`), {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'},
                cache: 'no-store'
            });
            if (!response.ok) {
                clearActiveSessionId();
                return;
            }
            const session = await response.json();
            if (session.status === 'complete') {
                showCompletion(session);
                return;
            }
            if (['ready_for_review', 'processing_review'].includes(session.status)) {
                currentSession = session;
                showSession(session);
                await completeInterview();
                return;
            }
            if (session.status === 'active') {
                currentSession = session;
                showSession(session);
                AppUI.showToast('Your active mock interview was restored.', {type: 'info'});
                return;
            }
            clearActiveSessionId();
        } catch (error) {
            // A temporary status failure should not block starting a new session.
        }
    }

    function updateCustomFocusVisibility() {
        const selected = selectedInterviewType();
        customFocusField.hidden = selected !== 'custom';
        customFocusInput.required = selected === 'custom';
    }

    function selectedInterviewType() {
        return document.querySelector('input[name="interviewType"]:checked')?.value || 'recruiter_screening';
    }

    async function startInterview() {
        if (phase !== 'ready') return;
        if (selectedInterviewType() === 'custom' && !customFocusInput.value.trim()) {
            customFocusInput.focus();
            AppUI.showToast('Describe what the custom practice session should focus on.', {type: 'error'});
            return;
        }
        if (readActiveSessionId()) {
            const confirmed = await AppUI.confirm({
                title: 'Replace the saved practice session?',
                message: 'Starting a new mock interview will replace the browser link to the previous unfinished session.',
                confirmLabel: 'Start New Interview',
                cancelLabel: 'Keep Existing Session',
                danger: true
            });
            if (!confirmed) return;
        }

        phase = 'starting';
        setStatus('processing', 'Preparing interviewer');
        startInterviewButton.disabled = true;
        hideError();
        try {
            const response = await fetch(AppUI.appUrl('/api/career/mock-interviews/adaptive/sessions'), {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Accept': 'application/json', 'Content-Type': 'application/json'},
                body: JSON.stringify({
                    application_workspace_id: workspaceSelect.value,
                    interview_type: selectedInterviewType(),
                    question_count: Number(questionCountSelect.value),
                    custom_focus: customFocusInput.value.trim(),
                    language: window.AppI18n?.language || document.documentElement.lang || 'en'
                })
            });
            const payload = await readJson(response);
            if (!response.ok) throw apiError(payload, 'The mock interview could not be started.');
            currentSession = payload;
            pendingNextSession = null;
            writeActiveSessionId(payload.session_id);
            showSession(payload);
            speakQuestion(payload.current_question);
        } catch (error) {
            phase = 'ready';
            setStatus('error', 'Unable to start');
            startInterviewButton.disabled = false;
            AppUI.showToast(error.message, {type: 'error', duration: 8000});
        }
    }

    function showSession(session) {
        currentSession = session;
        setupPanel.hidden = true;
        completePanel.hidden = true;
        sessionLayout.hidden = false;
        phase = 'question';
        setStatus('active', 'Interview active');
        renderSession(session);
        resetAnswerControls();
        hideError();
        evaluationPanel.hidden = true;
        processingPanel.hidden = true;
    }

    function renderSession(session) {
        const total = Number(session.question_count || 0);
        const answered = Number(session.answered_count ?? (session.answers || []).length);
        const number = Math.min(total, Math.max(1, Number(session.current_question_number || answered + 1)));
        sessionFormat.textContent = session.interview_type_label || 'Mock Interview';
        questionProgress.textContent = `Question ${number} of ${total}`;
        progressFill.style.width = `${Math.max(0, Math.min(100, ((number - 1) / Math.max(1, total)) * 100))}%`;
        questionHeading.textContent = session.current_question || 'Preparing the next question…';
        questionContext.textContent = questionTypeDescription(session.current_question_type, session.current_question_rationale);
        renderHistory(session.answers || []);
    }

    function questionTypeDescription(type, rationale) {
        const labels = {
            opening: 'Opening question selected for this interview format.',
            challenge: 'This question challenges a vague or unsupported part of your previous answer.',
            follow_up: 'This follow-up deepens the evidence or reasoning in your previous answer.',
            new_topic: 'The interviewer is moving to another important competency.'
        };
        return String(rationale || labels[type] || 'The next question adapts to your previous answer.');
    }

    async function startAnswerRecording() {
        if (phase !== 'question') return;
        const problem = recorderSupportProblem();
        if (problem) {
            showSessionError(problem, false);
            return;
        }
        cancelSpeech();
        hideError();
        evaluationPanel.hidden = true;
        pendingNextSession = null;
        lastAnswerBlob = null;
        lastAnswerDurationSeconds = null;
        startAnswerButton.disabled = true;
        answerStateTitle.textContent = 'Connecting to your microphone…';
        try {
            microphoneStream = await navigator.mediaDevices.getUserMedia({
                audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1},
                video: false
            });
            const mimeType = chooseMimeType();
            recordedChunks = [];
            mediaRecorder = mimeType
                ? new MediaRecorder(microphoneStream, {mimeType: mimeType})
                : new MediaRecorder(microphoneStream);
            mediaRecorder.addEventListener('dataavailable', function (event) {
                if (event.data && event.data.size > 0) recordedChunks.push(event.data);
            });
            mediaRecorder.addEventListener('error', function () {
                showSessionError('The browser could not record this answer. Check the microphone and try again.', false);
            });
            mediaRecorder.start(500);
            answerStartedAt = Date.now();
            startAnswerTimer();
            startMicrophoneMeter(microphoneStream);
            phase = 'recording';
            microphoneCard.dataset.state = 'recording';
            microphoneState.textContent = 'Recording';
            answerStateTitle.textContent = 'Answer naturally—the interviewer is listening';
            startAnswerButton.hidden = true;
            finishAnswerButton.hidden = false;
            finishAnswerButton.disabled = false;
            setStatus('active', 'Recording answer');
        } catch (error) {
            cleanupMicrophone();
            phase = 'question';
            startAnswerButton.disabled = false;
            answerStateTitle.textContent = 'Take a moment, then begin when ready';
            showSessionError(readableMediaError(error), false);
        }
    }

    async function finishAnswerRecording() {
        if (phase !== 'recording' || !mediaRecorder) return;
        const durationMs = Date.now() - answerStartedAt;
        if (durationMs < 1200) {
            AppUI.showToast('Keep recording long enough to provide a complete answer.', {type: 'warning'});
            return;
        }
        phase = 'stopping';
        finishAnswerButton.disabled = true;
        stopAnswerTimer();
        try {
            await stopRecorder(mediaRecorder);
            const mimeType = mediaRecorder.mimeType || chooseMimeType() || 'audio/webm';
            lastAnswerBlob = new Blob(recordedChunks, {type: mimeType});
            lastAnswerFilename = `mock-interview-answer.${mimeExtension(mimeType)}`;
            lastAnswerDurationSeconds = Math.max(0.1, durationMs / 1000);
            cleanupMicrophone();
            if (lastAnswerBlob.size < 700) throw new Error('The recorded answer was empty or too short to transcribe.');
            await submitAnswerBlob(lastAnswerBlob, lastAnswerFilename);
        } catch (error) {
            cleanupMicrophone();
            phase = 'answer_error';
            showSessionError(error.message || 'The answer could not be processed.', Boolean(lastAnswerBlob));
        }
    }

    async function retryLastAnswer() {
        hideError();
        if (lastAnswerBlob) {
            await submitAnswerBlob(lastAnswerBlob, lastAnswerFilename);
            return;
        }
        phase = 'question';
        resetAnswerControls();
    }

    async function submitAnswerBlob(blob, filename) {
        if (!currentSession?.session_id) throw new Error('The mock interview session is unavailable.');
        phase = 'submitting';
        setStatus('processing', 'Evaluating answer');
        processingPanel.hidden = false;
        evaluationPanel.hidden = true;
        errorPanel.hidden = true;
        processingTitle.textContent = 'Transcribing your answer';
        processingMessage.textContent = 'Réunia is evaluating the content and preparing an adaptive follow-up.';
        startAnswerButton.hidden = true;
        finishAnswerButton.hidden = true;

        const formData = new FormData();
        formData.append('answer_audio', blob, filename);
        formData.append('language', window.AppI18n?.language || document.documentElement.lang || 'en');
        if (Number.isFinite(lastAnswerDurationSeconds)) formData.append('duration_seconds', String(lastAnswerDurationSeconds));
        try {
            const response = await fetch(AppUI.appUrl(currentSession.answer_url || `/api/career/mock-interviews/adaptive/sessions/${encodeURIComponent(currentSession.session_id)}/answers`), {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'},
                body: formData
            });
            const payload = await readJson(response);
            if (!response.ok) throw apiError(payload, 'The answer could not be transcribed and evaluated.');
            processingPanel.hidden = true;
            currentSession = payload;
            lastAnswerBlob = null;
            lastAnswerDurationSeconds = null;
            renderHistory(payload.answers || []);
            showEvaluation(payload.latest_answer?.evaluation || {});
            progressFill.style.width = `${Math.min(100, (Number(payload.answered_count || 0) / Math.max(1, Number(payload.question_count || 1))) * 100)}%`;
            if (payload.complete) {
                nextQuestionButton.textContent = 'Generate interview review';
                nextQuestionButton.dataset.action = 'complete';
                pendingNextSession = payload;
                phase = 'evaluation';
                setStatus('active', 'Answers complete');
            } else {
                nextQuestionButton.textContent = 'Continue to next question';
                nextQuestionButton.dataset.action = 'next';
                pendingNextSession = payload;
                phase = 'evaluation';
                setStatus('active', 'Answer evaluated');
            }
        } catch (error) {
            processingPanel.hidden = true;
            phase = 'answer_error';
            setStatus('error', 'Answer failed');
            showSessionError(error.message, true);
        }
    }

    function showEvaluation(evaluation) {
        evaluationPanel.hidden = false;
        evaluationScore.textContent = Number.isFinite(Number(evaluation.score)) ? String(Math.round(Number(evaluation.score))) : '—';
        const evidence = ['supported', 'partial', 'unsupported'].includes(evaluation.evidence_status)
            ? evaluation.evidence_status
            : 'partial';
        evaluationEvidenceBadge.dataset.evidence = evidence;
        evaluationEvidenceBadge.textContent = evidence === 'supported'
            ? 'Supported evidence'
            : evidence === 'unsupported'
                ? 'Evidence missing'
                : 'Partial evidence';
        evaluationSummary.textContent = evaluation.summary || 'Réunia evaluated the answer and prepared the next question.';
        renderList(evaluationStrengths, evaluation.strengths, 'No specific strength was detected yet.');
        renderList(evaluationImprovements, evaluation.improvements, 'Keep the answer specific and evidence-based.');
        evaluationPanel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    async function continueToNextQuestion() {
        if (phase !== 'evaluation' || !pendingNextSession) return;
        if (nextQuestionButton.dataset.action === 'complete' || pendingNextSession.complete) {
            await completeInterview();
            return;
        }
        currentSession = pendingNextSession;
        pendingNextSession = null;
        evaluationPanel.hidden = true;
        renderSession(currentSession);
        resetAnswerControls();
        phase = 'question';
        setStatus('active', 'Interview active');
        questionHeading.scrollIntoView({behavior: 'smooth', block: 'center'});
        speakQuestion(currentSession.current_question);
    }

    async function completeInterview() {
        if (!currentSession?.session_id) return;
        phase = 'completing';
        evaluationPanel.hidden = true;
        errorPanel.hidden = true;
        processingPanel.hidden = false;
        processingTitle.textContent = 'Generating your interview review';
        processingMessage.textContent = 'Réunia is analyzing the full conversation across relevance, evidence, structure, clarity, and delivery.';
        setStatus('processing', 'Generating review');
        try {
            const response = await fetch(AppUI.appUrl(currentSession.complete_url || `/api/career/mock-interviews/adaptive/sessions/${encodeURIComponent(currentSession.session_id)}/complete`), {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
            const payload = await readJson(response);
            if (!response.ok) throw apiError(payload, 'The interview review could not be generated.');
            currentSession = payload;
            processingPanel.hidden = true;
            clearActiveSessionId();
            showCompletion(payload);
        } catch (error) {
            processingPanel.hidden = true;
            phase = 'evaluation';
            nextQuestionButton.textContent = 'Retry interview review';
            nextQuestionButton.dataset.action = 'complete';
            evaluationPanel.hidden = false;
            setStatus('error', 'Review failed');
            showSessionError(error.message, true);
        }
    }

    function showCompletion(session) {
        cancelSpeech();
        cleanupMicrophone();
        currentSession = session;
        setupPanel.hidden = true;
        sessionLayout.hidden = true;
        completePanel.hidden = false;
        phase = 'complete';
        setStatus('complete', 'Review ready');
        const count = Number(session.answered_count || (session.answers || []).length || 0);
        completeMessage.textContent = `Réunia saved ${count} interview answers and generated coaching across relevance, evidence, structure, clarity, and delivery.`;
        openReviewLink.href = AppUI.appUrl(session.review_url || reviewUrl);
        clearActiveSessionId();
    }

    async function discardCurrentInterview() {
        if (!currentSession?.session_id) {
            resetForNewInterview();
            return;
        }
        const confirmed = await AppUI.confirm({
            title: 'Discard this mock interview?',
            message: 'The current questions and recorded answers will be deleted. No interview review will be generated.',
            confirmLabel: 'Discard Interview',
            cancelLabel: 'Continue Interview',
            danger: true
        });
        if (!confirmed) return;
        cancelSpeech();
        cleanupMicrophone();
        try {
            await fetch(AppUI.appUrl(currentSession.discard_url || `/api/career/mock-interviews/adaptive/sessions/${encodeURIComponent(currentSession.session_id)}`), {
                method: 'DELETE',
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
        } catch (error) {
            // Local reset remains available even when server cleanup cannot be confirmed.
        }
        clearActiveSessionId();
        resetForNewInterview();
        AppUI.showToast('Mock interview discarded.', {type: 'info'});
    }

    function resetForNewInterview() {
        cancelSpeech();
        cleanupMicrophone();
        currentSession = null;
        pendingNextSession = null;
        lastAnswerBlob = null;
        lastAnswerDurationSeconds = null;
        phase = 'ready';
        sessionLayout.hidden = true;
        completePanel.hidden = true;
        setupPanel.hidden = false;
        startInterviewButton.disabled = false;
        setStatus('ready', 'Ready');
        hideError();
        evaluationPanel.hidden = true;
        processingPanel.hidden = true;
        resetAnswerControls();
        window.scrollTo({top: 0, behavior: 'smooth'});
    }

    function resetAnswerControls() {
        answerTimer.textContent = '00:00';
        answerStateTitle.textContent = 'Take a moment, then begin when ready';
        microphoneCard.dataset.state = 'idle';
        microphoneState.textContent = 'Not connected';
        microphoneMeter.style.width = '0%';
        startAnswerButton.hidden = false;
        startAnswerButton.disabled = false;
        finishAnswerButton.hidden = true;
        finishAnswerButton.disabled = false;
    }

    function renderHistory(answers) {
        const records = Array.isArray(answers) ? answers : [];
        historyCount.textContent = `${records.length} ${records.length === 1 ? 'answer' : 'answers'} completed`;
        historyList.replaceChildren();
        if (!records.length) {
            const empty = document.createElement('li');
            empty.className = 'mock-history-empty app-state app-state--empty app-state--compact';
            empty.dataset.uiState = 'empty';
            const icon = document.createElement('span');
            icon.className = 'app-state__icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.textContent = '○';
            const body = document.createElement('div');
            body.className = 'app-state__body';
            const title = document.createElement('strong');
            title.className = 'app-state__title';
            title.textContent = 'No answers completed yet';
            const message = document.createElement('p');
            message.className = 'app-state__message';
            message.textContent = 'Your completed questions and coaching signals will appear here.';
            body.append(title, message);
            empty.append(icon, body);
            historyList.appendChild(empty);
            return;
        }
        records.forEach(function (record) {
            const item = document.createElement('li');
            const title = document.createElement('strong');
            title.textContent = `${record.question_number}. ${record.question || 'Interview question'}`;
            const answer = document.createElement('p');
            answer.textContent = record.answer || '';
            const score = document.createElement('span');
            const value = Number(record.evaluation?.score);
            score.textContent = Number.isFinite(value) ? `${Math.round(value)}/100` : 'Evaluated';
            item.append(title, answer, score);
            historyList.appendChild(item);
        });
    }

    function renderList(container, items, fallback) {
        container.replaceChildren();
        const values = Array.isArray(items) && items.length ? items : [fallback];
        values.slice(0, 4).forEach(function (item) {
            const li = document.createElement('li');
            li.textContent = String(item || '');
            container.appendChild(li);
        });
    }

    function startAnswerTimer() {
        stopAnswerTimer();
        updateAnswerTimer();
        timerId = window.setInterval(updateAnswerTimer, 250);
    }

    function updateAnswerTimer() {
        const seconds = Math.max(0, Math.floor((Date.now() - answerStartedAt) / 1000));
        const minutes = Math.floor(seconds / 60);
        const remainder = seconds % 60;
        answerTimer.textContent = `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
    }

    function stopAnswerTimer() {
        if (timerId !== null) window.clearInterval(timerId);
        timerId = null;
    }

    function startMicrophoneMeter(stream) {
        stopMicrophoneMeter();
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        audioContext = new AudioContextClass();
        const source = audioContext.createMediaStreamSource(stream);
        const analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        const values = new Uint8Array(analyser.frequencyBinCount);
        function draw() {
            analyser.getByteFrequencyData(values);
            const average = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
            microphoneMeter.style.width = `${Math.min(100, Math.max(2, average * 1.55))}%`;
            meterAnimation = window.requestAnimationFrame(draw);
        }
        draw();
    }

    function stopMicrophoneMeter() {
        if (meterAnimation !== null) window.cancelAnimationFrame(meterAnimation);
        meterAnimation = null;
        if (audioContext) void audioContext.close().catch(function () {});
        audioContext = null;
        microphoneMeter.style.width = '0%';
    }

    function cleanupMicrophone() {
        stopAnswerTimer();
        stopMicrophoneMeter();
        if (mediaRecorder?.state && mediaRecorder.state !== 'inactive') {
            try { mediaRecorder.stop(); } catch (error) {}
        }
        mediaRecorder = null;
        if (microphoneStream) microphoneStream.getTracks().forEach((track) => track.stop());
        microphoneStream = null;
        microphoneCard.dataset.state = 'idle';
    }

    function stopRecorder(recorder) {
        return new Promise(function (resolve, reject) {
            if (!recorder || recorder.state === 'inactive') {
                resolve();
                return;
            }
            recorder.addEventListener('stop', resolve, {once: true});
            recorder.addEventListener('error', function (event) {
                reject(event.error || new Error('The answer recording could not be finalized.'));
            }, {once: true});
            recorder.stop();
        });
    }

    function speakQuestion(question) {
        const text = String(question || '').trim();
        if (!text || !readQuestionsAloud.checked || !('speechSynthesis' in window)) return;
        cancelSpeech();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = (window.AppI18n?.language || document.documentElement.lang || 'en').startsWith('fr') ? 'fr-FR' : 'en-US';
        utterance.rate = 0.96;
        window.speechSynthesis.speak(utterance);
    }

    function cancelSpeech() {
        if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    }

    function setStatus(state, text) {
        statusBadge.dataset.state = state;
        statusText.textContent = text;
    }

    function showSessionError(message, canRetry) {
        errorPanel.hidden = false;
        errorMessage.textContent = message || 'An unexpected error occurred.';
        retryAnswerButton.hidden = !canRetry;
        if (!canRetry) {
            startAnswerButton.hidden = false;
            startAnswerButton.disabled = false;
            finishAnswerButton.hidden = true;
        }
        errorPanel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }

    function hideError() {
        errorPanel.hidden = true;
        errorMessage.textContent = '';
    }

    function ensureBrowserSupport() {
        const problem = recorderSupportProblem();
        if (!problem) return;
        startAnswerButton.disabled = true;
        answerStateTitle.textContent = problem;
    }

    function recorderSupportProblem() {
        if (!navigator.mediaDevices?.getUserMedia) return 'This browser cannot access the microphone. Use a current version of Chrome or Edge.';
        if (typeof window.MediaRecorder !== 'function') return 'This browser cannot record audio. Use a current version of Chrome or Edge.';
        return '';
    }

    function chooseMimeType() {
        const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
        return candidates.find((type) => MediaRecorder.isTypeSupported?.(type)) || '';
    }

    function mimeExtension(type) {
        const normalized = String(type || '').toLowerCase();
        if (normalized.includes('ogg')) return 'ogg';
        if (normalized.includes('mp4')) return 'm4a';
        return 'webm';
    }

    function readableMediaError(error) {
        const name = String(error?.name || '');
        if (name === 'NotAllowedError' || name === 'PermissionDeniedError') return 'Microphone access was blocked. Allow microphone access in the browser and try again.';
        if (name === 'NotFoundError' || name === 'DevicesNotFoundError') return 'No microphone was found. Connect a microphone and try again.';
        if (name === 'NotReadableError' || name === 'TrackStartError') return 'The microphone is being used by another application or is unavailable.';
        return error?.message || 'The microphone could not be opened.';
    }

    async function readJson(response) {
        const text = await response.text();
        if (!text) return {};
        try { return JSON.parse(text); } catch (error) { return {error: text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()}; }
    }

    function apiError(payload, fallback) {
        return new Error(String(payload?.error || payload?.message || fallback));
    }

    function writeActiveSessionId(sessionId) {
        try { window.localStorage.setItem(ACTIVE_SESSION_KEY, String(sessionId || '')); } catch (error) {}
    }

    function readActiveSessionId() {
        try { return String(window.localStorage.getItem(ACTIVE_SESSION_KEY) || ''); } catch (error) { return ''; }
    }

    function clearActiveSessionId() {
        try { window.localStorage.removeItem(ACTIVE_SESSION_KEY); } catch (error) {}
    }

    function truncate(value, maximum) {
        const text = String(value || '');
        return text.length > maximum ? `${text.slice(0, maximum - 1)}…` : text;
    }
})();
