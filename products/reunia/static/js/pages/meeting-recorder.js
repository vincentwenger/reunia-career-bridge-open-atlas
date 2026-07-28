(function () {
    'use strict';

    const recorderPage = document.getElementById('browserRecorderPage');
    const captureMeetingAudioInput = document.getElementById('captureMeetingAudio');
    const enableLiveQAInput = document.getElementById('enableLiveQA');
    const liveQASourceOptions = document.getElementById('liveQASourceOptions');
    const liveQASpeakerInput = document.getElementById('liveQASpeaker');
    const liveQAMicrophoneInput = document.getElementById('liveQAMicrophone');
    const preparedMeetingSelect = document.getElementById('preparedMeetingSelect');
    const preparedMeetingHelp = document.getElementById('preparedMeetingHelp');
    const startButton = document.getElementById('startRecordingButton');
    const stopButton = document.getElementById('stopRecordingButton');
    const discardButton = document.getElementById('discardRecordingButton');
    const openLiveQALink = document.getElementById('openLiveQALink');
    const resetButton = document.getElementById('resetRecordingButton');
    const retryButton = document.getElementById('retryProcessingButton');
    const sendErrorButton = document.getElementById('sendErrorToSupportButton');
    const discardFailedButton = document.getElementById('discardFailedRecordingButton');
    const timerElement = document.getElementById('recorderTimer');
    const statusBadge = document.getElementById('recorderStatusBadge');
    const statusText = document.getElementById('recorderStatusText');
    const stageTitle = document.getElementById('recorderStageTitle');
    const stageDescription = document.getElementById('recorderStageDescription');
    const microphoneState = document.getElementById('microphoneState');
    const speakerState = document.getElementById('speakerState');
    const microphoneMuteButton = document.getElementById('microphoneMuteButton');
    const speakerMuteButton = document.getElementById('speakerMuteButton');
    const microphoneMeter = document.getElementById('microphoneMeter');
    const speakerMeter = document.getElementById('speakerMeter');
    const microphoneSourceCard = document.getElementById('microphoneSourceCard');
    const speakerSourceCard = document.getElementById('speakerSourceCard');
    const progressPanel = document.getElementById('recorderProgress');
    const progressTitle = document.getElementById('recorderProgressTitle');
    const progressMessage = document.getElementById('recorderProgressMessage');
    const errorPanel = document.getElementById('recorderErrorPanel');
    const errorMessage = document.getElementById('recorderErrorMessage');
    const errorReference = document.getElementById('recorderErrorReference');
    const errorStatus = document.getElementById('recorderErrorStatus');
    const errorStage = document.getElementById('recorderErrorStage');
    const errorRecording = document.getElementById('recorderErrorRecording');
    const errorDetails = document.getElementById('recorderErrorDetails');
    const errorRetention = document.getElementById('recorderErrorRetention');
    const supportStatus = document.getElementById('recorderSupportStatus');
    const resultPanel = document.getElementById('recorderResult');
    const resultTitle = document.getElementById('recorderResultTitle');
    const resultMessage = document.getElementById('recorderResultMessage');
    const qualityWarning = document.getElementById('recorderQualityWarning');
    const reviewLink = document.getElementById('reviewMeetingLink');

    if (!startButton) return;

    let microphoneStream = null;
    let displayStream = null;
    let speakerAudioStream = null;
    let microphoneRecorder = null;
    let speakerRecorder = null;
    let microphoneChunks = [];
    let speakerChunks = [];
    let startedAt = null;
    let timerInterval = null;
    let audioContexts = [];
    let meterAnimations = [];
    let phase = 'ready';
    let recordingHeartbeatInterval = null;
    let pendingRecording = null;
    let lastErrorDiagnostics = '';
    let lastProcessingError = null;
    let lastSupportRequestId = '';
    let pollGeneration = 0;
    let preparedMeetingsCache = [];
    let microphoneMuted = false;
    let speakerMuted = false;
    let liveQASession = null;
    let recordingUploadSession = null;
    let finalSegmentSources = {};
    let finalSegmentRotationTimer = null;
    let finalSegmentRotationPromise = Promise.resolve();
    let finalSegmentUploadTail = Promise.resolve();
    const audioActivity = {
        microphone: {frames: 0, speechFrames: 0, maxLevel: 0},
        speaker: {frames: 0, speechFrames: 0, maxLevel: 0}
    };
    const trackMetric = (metric, metadata = {}, eventId = '') => {
        window.ReuniaAnalytics?.track?.(metric, metadata, eventId);
    };

    const ACTIVE_RECORDING_KEY = 'meetingAssistant.activeBrowserRecording';
    const storageScope = encodeURIComponent(recorderPage?.dataset.storageScope || 'default');
    const UPCOMING_MEETINGS_KEY = `meetingAssistant.upcomingMeetings.v1.${storageScope}`;
    const MEETING_MATERIALS_KEY = `meetingAssistant.meetingMaterials.v1.${storageScope}`;
    const MEETING_CONTEXTS_KEY = `meetingAssistant.meetingContexts.v1.${storageScope}`;
    const recorderInstanceId = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    const supportedMimeType = chooseMimeType();
    const sharedAudioSupported = typeof navigator.mediaDevices?.getDisplayMedia === 'function';
    const liveChunkWindowMs = Math.max(4000, Number(recorderPage?.dataset.liveChunkWindowMs) || 10000);
    const configuredLiveIntervalMs = Math.max(2000, Number(recorderPage?.dataset.liveChunkIntervalMs) || 8000);
    const liveChunkIntervalMs = Math.min(configuredLiveIntervalMs, liveChunkWindowMs - 500);
    const liveQueueLimit = Math.max(1, Number(recorderPage?.dataset.liveQueueLimit) || 3);
    const liveRetryCount = Math.max(0, Number(recorderPage?.dataset.liveRetryCount) || 2);
    const liveRetryBaseMs = Math.max(100, Number(recorderPage?.dataset.liveRetryBaseMs) || 600);
    const liveMinChunkBytes = Math.max(1, Number(recorderPage?.dataset.liveMinChunkBytes) || 800);
    const liveMaxMinutes = Math.max(0, Number(recorderPage?.dataset.liveMaxMinutes) || 60);
    const liveSpeechLevelThreshold = Math.max(0, Number(recorderPage?.dataset.liveSpeechLevelThreshold) || 8);
    const liveMinSpeechRatio = Math.min(1, Math.max(0, Number(recorderPage?.dataset.liveMinSpeechRatio) || 0.08));
    const finalSegmentDurationMs = Math.max(60000, Number(recorderPage?.dataset.finalSegmentDurationMs) || 600000);
    const finalSegmentRetryCount = Math.max(0, Number(recorderPage?.dataset.finalSegmentRetryCount) || 3);
    const finalSegmentRetryBaseMs = Math.max(100, Number(recorderPage?.dataset.finalSegmentRetryBaseMs) || 1000);
    const finalMinSegmentBytes = Math.max(1, Number(recorderPage?.dataset.finalMinSegmentBytes) || 800);
    const finalMaxSegmentBytes = Math.max(1024, Number(recorderPage?.dataset.finalMaxSegmentBytes) || 24000000);

    progressPanel.hidden = true;
    errorPanel.hidden = true;
    resultPanel.hidden = true;
    initializePreparedMeetings();
    initializeBrowserSupport();

    captureMeetingAudioInput.addEventListener('change', function () {
        updateSharedAudioPresentation();
        updateLiveQASourceOptions();
    });
    enableLiveQAInput?.addEventListener('change', updateLiveQASourceOptions);
    startButton.addEventListener('click', startRecording);
    stopButton.addEventListener('click', stopRecording);
    discardButton.addEventListener('click', requestDiscardRecording);
    resetButton.addEventListener('click', resetRecorder);
    retryButton.addEventListener('click', retryProcessing);
    sendErrorButton?.addEventListener('click', sendDiagnosticToSupport);
    discardFailedButton.addEventListener('click', discardFailedRecording);
    microphoneMuteButton?.addEventListener('click', function () {
        toggleAudioSourceMute('microphone');
    });
    speakerMuteButton?.addEventListener('click', function () {
        toggleAudioSourceMute('speaker');
    });

    window.addEventListener('beforeunload', function (event) {
        if (phase !== 'recording' && phase !== 'processing' && !pendingRecording) return;
        event.preventDefault();
        event.returnValue = '';
    });

    window.addEventListener('pagehide', function () {
        clearRecordingActivity();
        if (liveQASession) {
            void cancelLiveQASession(liveQASession, {notifyServer: true, keepalive: true});
        }
    });

    async function startRecording() {
        const supportProblem = getRecorderSupportProblem();
        if (supportProblem) {
            showCompatibilityProblem(supportProblem);
            return;
        }

        pollGeneration += 1;
        pendingRecording = null;
        lastErrorDiagnostics = '';
        setPhase('connecting');
        startButton.disabled = true;
        captureMeetingAudioInput.disabled = true;
        if (enableLiveQAInput) enableLiveQAInput.disabled = true;
        if (liveQASpeakerInput) liveQASpeakerInput.disabled = true;
        if (liveQAMicrophoneInput) liveQAMicrophoneInput.disabled = true;
        if (preparedMeetingSelect) preparedMeetingSelect.disabled = true;
        hideOutcomePanels();
        microphoneChunks = [];
        speakerChunks = [];
        Object.values(audioActivity).forEach(function (activity) {
            activity.frames = 0;
            activity.speechFrames = 0;
            activity.maxLevel = 0;
        });
        updateMuteControls();

        try {
            microphoneStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    channelCount: 1
                },
                video: false
            });
            applyMutePreference('microphone');
            microphoneState.textContent = microphoneMuted ? 'Muted' : 'Connected';
            microphoneState.classList.toggle('is-live', !microphoneMuted);
            microphoneState.classList.toggle('is-muted', microphoneMuted);
            startMeter(microphoneStream, microphoneMeter, 'microphone');
            updateMuteControls();

            if (captureMeetingAudioInput.checked) {
                if (!navigator.mediaDevices.getDisplayMedia) {
                    throw new Error('Screen audio sharing is not supported by this browser.');
                }

                displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: true,
                    audio: {
                        echoCancellation: false,
                        noiseSuppression: false,
                        autoGainControl: false,
                        channelCount: 2
                    },
                    systemAudio: 'include',
                    surfaceSwitching: 'include',
                    selfBrowserSurface: 'exclude'
                });

                const speakerTracks = displayStream.getAudioTracks();
                if (!speakerTracks.length) {
                    throw new Error('No interviewer prompt audio was received. Select the prompt tab or screen and enable “Share tab audio” or “Share system audio”.');
                }
                speakerAudioStream = new MediaStream(speakerTracks);
                applyMutePreference('speaker');
                speakerState.textContent = speakerMuted ? 'Muted' : 'Connected';
                speakerState.classList.toggle('is-live', !speakerMuted);
                speakerState.classList.toggle('is-muted', speakerMuted);
                startMeter(speakerAudioStream, speakerMeter, 'speaker');
                updateMuteControls();

                displayStream.getVideoTracks().forEach(function (track) {
                    track.addEventListener('ended', function () {
                        if (phase === 'recording') stopRecording();
                    }, {once: true});
                });
            } else {
                speakerState.textContent = 'Disabled';
            }

            startedAt = new Date();
            const preparedMeeting = getSelectedPreparedMeeting();
            recordingUploadSession = await createFinalUploadSession(startedAt, preparedMeeting);
            pendingRecording = {
                mode: 'segmented',
                session: recordingUploadSession,
                startedAt: new Date(startedAt),
                durationSeconds: 0,
                capturedAt: null,
                preparedMeeting: preparedMeeting,
                jobFailed: false
            };
            initializeFinalSegmentSources();
            startFinalSegmentSource('MICROPHONE');
            if (speakerAudioStream) startFinalSegmentSource('SPEAKER');
            scheduleFinalSegmentRotation();
            startTimer();
            setPhase('recording');
            updateMuteControls();
            startButton.hidden = true;
            stopButton.hidden = false;
            stopButton.disabled = false;
            discardButton.hidden = false;
            discardButton.disabled = false;
            startLiveQAStreaming();
            trackMetric('recording_started', {
                shared_audio: Boolean(captureMeetingAudioInput.checked),
                source: 'browser_recorder'
            }, `recording-start-${recorderInstanceId}-${startedAt.getTime()}`);
        } catch (error) {
            if (recordingUploadSession) {
                void discardServerRecording(recordingUploadSession, {keepalive: true});
            }
            recordingUploadSession = null;
            pendingRecording = null;
            resetFinalSegmentState();
            cleanupStreams();
            microphoneState.textContent = 'Not connected';
            captureMeetingAudioInput.disabled = !sharedAudioSupported;
            if (enableLiveQAInput) enableLiveQAInput.disabled = false;
            updateLiveQASourceOptions();
            if (preparedMeetingSelect) preparedMeetingSelect.disabled = false;
            updateSharedAudioPresentation();
            startButton.hidden = false;
            startButton.disabled = false;
            stopButton.hidden = true;
            discardButton.hidden = true;
            setPhase('ready');
            AppUI.showToast(readableMediaError(error), {type: 'error', duration: 8000});
        }
    }

    async function stopRecording() {
        if (phase !== 'recording') return;
        setPhase('stopping');
        updateMuteControls();
        stopButton.disabled = true;
        discardButton.disabled = true;
        stopButton.hidden = true;
        discardButton.hidden = true;
        stopTimer();
        clearFinalSegmentRotation();

        try {
            await finalSegmentRotationPromise;
            await stopLiveQAStreaming({discard: false, flushPartial: true});
            await stopAndQueueAllFinalSegments();

            const durationSeconds = Math.max(
                0,
                Math.floor((Date.now() - (startedAt?.getTime() || Date.now())) / 1000)
            );
            pendingRecording = pendingRecording || {
                mode: 'segmented',
                session: recordingUploadSession,
                startedAt: startedAt ? new Date(startedAt) : new Date(),
                preparedMeeting: getSelectedPreparedMeeting(),
                jobFailed: false
            };
            pendingRecording.durationSeconds = durationSeconds;
            pendingRecording.capturedAt = new Date();

            trackMetric('recording_completed', {
                duration_seconds: durationSeconds,
                shared_audio: Boolean(finalSegmentSources.SPEAKER),
                source: 'browser_recorder'
            }, `recording-complete-${recorderInstanceId}-${pendingRecording.startedAt.getTime()}`);
            cleanupStreams();
            microphoneState.textContent = 'Captured';
            speakerState.textContent = finalSegmentSources.SPEAKER ? 'Captured' : 'Disabled';
            await uploadRecording(pendingRecording);
        } catch (error) {
            cleanupStreams();
            showFailure(normalizeProcessingError(error, {stage: 'finalizing_audio'}));
        }
    }

    async function requestDiscardRecording() {
        if (phase !== 'recording') return;

        const confirmed = await AppUI.confirm({
            title: 'Discard this recording?',
            message: 'The current recording will be permanently deleted and will not be processed or saved.',
            confirmLabel: 'Discard Recording',
            cancelLabel: 'Keep Recording',
            danger: true
        });

        if (!confirmed || phase !== 'recording') return;

        setPhase('discarding');
        updateMuteControls();
        stopButton.disabled = true;
        discardButton.disabled = true;
        stopButton.hidden = true;
        discardButton.hidden = true;
        stopTimer();
        clearFinalSegmentRotation();

        await finalSegmentRotationPromise;
        await stopLiveQAStreaming({discard: true, flushPartial: false});
        await stopAllFinalSegmentRecorders(false);
        if (recordingUploadSession) {
            try {
                await discardServerRecording(recordingUploadSession);
            } catch (error) {
                AppUI.showToast('The local recording was discarded, but the server cleanup could not be confirmed.', {
                    type: 'warning',
                    duration: 7000
                });
            }
        }

        microphoneChunks = [];
        speakerChunks = [];
        microphoneMuted = false;
        speakerMuted = false;
        pendingRecording = null;
        recordingUploadSession = null;
        resetFinalSegmentState();
        cleanupStreams();
        resetRecorder();
        AppUI.showToast('Recording discarded. No mock interview was saved.', {type: 'info'});
    }

    async function retryProcessing() {
        if (!pendingRecording || phase === 'processing') return;
        await uploadRecording(pendingRecording);
    }

    async function discardFailedRecording() {
        if (!pendingRecording) {
            resetRecorder();
            return;
        }
        const confirmed = await AppUI.confirm({
            title: 'Discard the captured audio?',
            message: 'The saved recording segments will be deleted and it will no longer be possible to retry processing them.',
            confirmLabel: 'Discard Audio',
            cancelLabel: 'Keep Audio',
            danger: true
        });
        if (!confirmed) return;
        try {
            if (pendingRecording.session) {
                await discardServerRecording(pendingRecording.session);
            }
        } catch (error) {
            AppUI.showToast('The recording could not be removed from the server. It will expire automatically.', {
                type: 'warning',
                duration: 7000
            });
        }
        pendingRecording = null;
        recordingUploadSession = null;
        microphoneChunks = [];
        speakerChunks = [];
        resetFinalSegmentState();
        resetRecorder();
        AppUI.showToast('Captured audio discarded.', {type: 'info'});
    }

    async function createFinalUploadSession(recordingStartedAt, preparedMeeting) {
        const referenceId = createReferenceId();
        const formData = new FormData();
        formData.append('client_reference_id', referenceId);
        formData.append('started_at', recordingStartedAt.toISOString());
        formData.append('language', window.AppI18n?.language || document.documentElement.lang || 'en');
        appendPreparedMeetingFields(formData, preparedMeeting);

        let response;
        let parsed;
        try {
            response = await fetch(AppUI.appUrl('/api/career/mock-interviews/sessions'), {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {'X-Recorder-Reference': referenceId, 'Accept': 'application/json'}
            });
            parsed = await readResponse(response);
        } catch (error) {
            throw createProcessingError(
                'The recorder could not create a secure upload session. Check your connection and try again.',
                {
                    referenceId: referenceId,
                    stage: 'creating_upload_session',
                    cause: error?.message || String(error)
                }
            );
        }
        if (!response.ok) {
            throw errorFromHttpResponse(response, parsed, {
                referenceId: parsed.payload?.reference_id || referenceId,
                stage: parsed.payload?.stage || 'creating_upload_session'
            });
        }

        const payload = parsed.payload || {};
        const sessionId = payload.job_id || payload.reference_id || referenceId;
        return {
            id: sessionId,
            referenceId: sessionId,
            segmentUrl: payload.segment_url || AppUI.appUrl(`/api/career/mock-interviews/sessions/${encodeURIComponent(sessionId)}/segments`),
            finalizeUrl: payload.finalize_url || AppUI.appUrl(`/api/career/mock-interviews/sessions/${encodeURIComponent(sessionId)}/finalize`),
            discardUrl: payload.discard_url || AppUI.appUrl(`/api/career/mock-interviews/sessions/${encodeURIComponent(sessionId)}`),
            statusUrl: payload.status_url || AppUI.appUrl(`/api/career/mock-interviews/jobs/${encodeURIComponent(sessionId)}`),
            retryUrl: AppUI.appUrl(`/api/career/mock-interviews/jobs/${encodeURIComponent(sessionId)}/retry`),
            preparedMeeting: preparedMeeting || null,
            failedSegments: new Map(),
            capturedBytes: {MICROPHONE: 0, SPEAKER: 0},
            uploadedSegments: 0,
            lastUploadError: null
        };
    }

    function appendPreparedMeetingFields(formData, preparedMeeting) {
        if (!preparedMeeting) return;
        formData.append('prepared_meeting_id', preparedMeeting.id || '');
        formData.append('prepared_meeting_title', preparedMeeting.title || '');
        formData.append('prepared_meeting_scheduled_at', preparedMeeting.scheduled_at || '');
        formData.append('prepared_meeting_participants', JSON.stringify(preparedMeeting.participants || []));
        formData.append('prepared_meeting_purpose', preparedMeeting.purpose || '');
    }

    function initializeFinalSegmentSources() {
        resetFinalSegmentState();
        finalSegmentSources = {
            MICROPHONE: createFinalSegmentSource('MICROPHONE', microphoneStream)
        };
        if (speakerAudioStream) {
            finalSegmentSources.SPEAKER = createFinalSegmentSource('SPEAKER', speakerAudioStream);
        }
    }

    function createFinalSegmentSource(source, stream) {
        return {
            source: source,
            stream: stream,
            recorder: null,
            chunks: [],
            sequence: 0,
            startedAt: null
        };
    }

    function startFinalSegmentSource(source) {
        const state = finalSegmentSources[source];
        if (!state?.stream || phase === 'stopping' || phase === 'discarding') return;
        state.chunks = [];
        state.recorder = buildRecorder(state.stream, state.chunks);
        state.startedAt = new Date();
        state.recorder.start(1000);
        if (source === 'MICROPHONE') {
            microphoneRecorder = state.recorder;
            microphoneChunks = state.chunks;
        } else {
            speakerRecorder = state.recorder;
            speakerChunks = state.chunks;
        }
    }

    function scheduleFinalSegmentRotation() {
        clearFinalSegmentRotation();
        finalSegmentRotationTimer = window.setTimeout(function rotateWhenDue() {
            finalSegmentRotationTimer = null;
            finalSegmentRotationPromise = finalSegmentRotationPromise
                .then(async function () {
                    if (phase !== 'recording') return;
                    await rotateFinalSegments();
                })
                .catch(function (error) {
                    if (phase === 'recording') {
                        showFailure(normalizeProcessingError(error, {stage: 'saving_segments'}));
                    }
                })
                .finally(function () {
                    if (phase === 'recording') scheduleFinalSegmentRotation();
                });
        }, finalSegmentDurationMs);
    }

    function clearFinalSegmentRotation() {
        if (finalSegmentRotationTimer !== null) {
            window.clearTimeout(finalSegmentRotationTimer);
            finalSegmentRotationTimer = null;
        }
    }

    async function rotateFinalSegments() {
        const segments = await stopAllFinalSegmentRecorders(true);
        if (phase !== 'recording') return segments;
        Object.keys(finalSegmentSources).forEach(startFinalSegmentSource);
        return segments;
    }

    async function stopAndQueueAllFinalSegments() {
        await stopAllFinalSegmentRecorders(true);
        await waitForFinalSegmentUploads();
    }

    async function stopAllFinalSegmentRecorders(queueSegments) {
        const states = Object.values(finalSegmentSources);
        const segments = await Promise.all(states.map(stopFinalSegmentSource));
        const captured = segments.filter(Boolean);
        if (queueSegments) captured.forEach(queueFinalSegmentUpload);
        return captured;
    }

    async function stopFinalSegmentSource(state) {
        if (!state?.recorder) return null;
        const recorder = state.recorder;
        const segmentStartedAt = state.startedAt || new Date();
        await stopMediaRecorder(recorder);
        const endedAt = new Date();
        const blob = new Blob(state.chunks, {
            type: recorder.mimeType || supportedMimeType || 'audio/webm'
        });
        const segment = {
            source: state.source,
            sequence: state.sequence,
            offsetSeconds: Math.max(0, (segmentStartedAt.getTime() - (startedAt?.getTime() || segmentStartedAt.getTime())) / 1000),
            durationSeconds: Math.max(0, (endedAt.getTime() - segmentStartedAt.getTime()) / 1000),
            blob: blob
        };
        state.sequence += 1;
        state.recorder = null;
        state.chunks = [];
        state.startedAt = null;
        if (state.source === 'MICROPHONE') {
            microphoneRecorder = null;
            microphoneChunks = [];
        } else {
            speakerRecorder = null;
            speakerChunks = [];
        }
        if (!blob.size || (blob.size < finalMinSegmentBytes && segment.durationSeconds < 0.25)) return null;
        if (recordingUploadSession) {
            recordingUploadSession.capturedBytes[state.source] =
                (recordingUploadSession.capturedBytes[state.source] || 0) + blob.size;
        }
        return segment;
    }

    function queueFinalSegmentUpload(segment) {
        const session = recordingUploadSession;
        if (!session || !segment?.blob?.size) return;
        const key = `${segment.source}:${segment.sequence}`;
        session.failedSegments.delete(key);
        const task = finalSegmentUploadTail
            .then(function () {
                return uploadFinalSegmentWithRetry(session, segment);
            })
            .then(function () {
                session.uploadedSegments += 1;
                segment.blob = null;
                session.lastUploadError = null;
            })
            .catch(function (error) {
                session.failedSegments.set(key, segment);
                session.lastUploadError = error;
            });
        finalSegmentUploadTail = task.then(function () {}, function () {});
    }

    async function uploadFinalSegmentWithRetry(session, segment) {
        if (segment.blob.size > finalMaxSegmentBytes) {
            throw createProcessingError(
                'One audio segment is larger than the safe transcription limit. Keep this tab open, retry, or record a shorter mock-interview segment.',
                {
                    referenceId: session.id,
                    httpStatus: 413,
                    stage: 'uploading_segment'
                }
            );
        }

        let lastError = null;
        for (let attempt = 0; attempt <= finalSegmentRetryCount; attempt += 1) {
            const formData = new FormData();
            formData.append('source', segment.source);
            formData.append('sequence', String(segment.sequence));
            formData.append('offset_seconds', String(segment.offsetSeconds));
            formData.append('duration_seconds', String(segment.durationSeconds));
            formData.append(
                'audio_segment',
                segment.blob,
                `${segment.source.toLowerCase()}-${String(segment.sequence).padStart(4, '0')}${mimeExtension(segment.blob.type)}`
            );
            try {
                const response = await fetch(session.segmentUrl, {
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin',
                    headers: {
                        'X-Recorder-Reference': session.id,
                        'Accept': 'application/json'
                    }
                });
                const parsed = await readResponse(response);
                if (!response.ok) {
                    throw errorFromHttpResponse(response, parsed, {
                        referenceId: session.id,
                        stage: parsed.payload?.stage || 'uploading_segment'
                    });
                }
                return parsed.payload || {};
            } catch (error) {
                lastError = error;
                const status = Number(error?.httpStatus) || 0;
                const retryable = !status || status >= 500;
                if (!retryable || attempt >= finalSegmentRetryCount) break;
                await delay(finalSegmentRetryBaseMs * Math.pow(2, attempt));
            }
        }
        throw lastError || createProcessingError('An audio segment could not be uploaded.', {
            referenceId: session.id,
            stage: 'uploading_segment'
        });
    }

    async function retryFailedFinalSegments(session) {
        const failed = Array.from(session.failedSegments.values());
        session.failedSegments.clear();
        failed.forEach(queueFinalSegmentUpload);
        await waitForFinalSegmentUploads();
    }

    async function waitForFinalSegmentUploads() {
        await finalSegmentUploadTail;
        const session = recordingUploadSession;
        if (session?.failedSegments?.size) {
            throw session.lastUploadError || createProcessingError(
                'One or more audio segments could not be uploaded. Check your connection and retry.',
                {referenceId: session.id, stage: 'uploading_segment'}
            );
        }
    }

    async function discardServerRecording(session, options = {}) {
        if (!session?.discardUrl) return;
        const response = await fetch(session.discardUrl, {
            method: 'DELETE',
            credentials: 'same-origin',
            keepalive: Boolean(options.keepalive),
            headers: {'Accept': 'application/json', 'X-Recorder-Reference': session.id}
        });
        if (!response.ok) {
            const parsed = await readResponse(response);
            throw errorFromHttpResponse(response, parsed, {
                referenceId: session.id,
                stage: 'discarding'
            });
        }
    }

    function resetFinalSegmentState() {
        clearFinalSegmentRotation();
        finalSegmentSources = {};
        finalSegmentRotationPromise = Promise.resolve();
        finalSegmentUploadTail = Promise.resolve();
    }

    async function uploadRecording(recording) {
        const session = recording?.session || recordingUploadSession;
        if (!session) {
            showFailure(createProcessingError('The secure recording session is unavailable.', {
                stage: 'finalizing_upload'
            }));
            return;
        }

        const generation = ++pollGeneration;
        setPhase('processing');
        configureProcessingPanel(
            recording.jobFailed ? 'Retrying mock interview processing' : 'Finishing audio uploads',
            recording.jobFailed
                ? 'The saved audio segments are being queued again.'
                : 'Keep this page open while the remaining audio segments are uploaded securely.'
        );
        errorPanel.hidden = true;
        resultPanel.hidden = true;
        retryButton.disabled = true;
        discardFailedButton.disabled = true;

        if (recording.jobFailed) {
            await retryBackgroundJob(recording, generation);
            return;
        }

        try {
            await retryFailedFinalSegments(session);
        } catch (error) {
            showFailure(normalizeProcessingError(error, {
                referenceId: session.id,
                stage: 'uploading_segment'
            }));
            return;
        }

        const formData = new FormData();
        formData.append('duration_seconds', String(recording.durationSeconds || 0));
        let response;
        let parsed;
        try {
            response = await fetch(session.finalizeUrl, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {'X-Recorder-Reference': session.id, 'Accept': 'application/json'}
            });
            parsed = await readResponse(response);
        } catch (error) {
            showFailure(createProcessingError(
                'The final upload could not reach the server. Check your connection and retry.',
                {
                    referenceId: session.id,
                    stage: 'finalizing_upload',
                    cause: error?.message || String(error)
                }
            ));
            return;
        }

        if (!response.ok) {
            showFailure(errorFromHttpResponse(response, parsed, {
                referenceId: parsed.payload?.reference_id || session.id,
                stage: parsed.payload?.stage || 'finalizing_upload'
            }));
            return;
        }

        const payload = parsed.payload || {};
        trackMetric('recording_uploaded', {
            duration_seconds: recording.durationSeconds,
            segment_count: session.uploadedSegments,
            source: 'browser_recorder'
        }, `recording-upload-${session.id}`);
        trackMetric('meeting_processing_started', {source: 'browser_recorder'}, `processing-${session.id}`);
        configureProcessingPanel(
            'Audio segments saved',
            payload.stage_message || 'Processing has started. The page will update automatically.'
        );
        if (payload.status === 'complete') {
            showSuccess(payload);
            return;
        }
        await pollRecordingJob(payload.status_url || session.statusUrl, session.id, generation);
    }

    async function retryBackgroundJob(recording, generation) {
        const session = recording.session;
        let response;
        let parsed;
        try {
            response = await fetch(session.retryUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'X-Recorder-Reference': session.id, 'Accept': 'application/json'}
            });
            parsed = await readResponse(response);
        } catch (error) {
            showFailure(createProcessingError('The retry request could not reach the server.', {
                referenceId: session.id,
                stage: 'retrying',
                cause: error?.message || String(error)
            }));
            return;
        }
        if (!response.ok) {
            showFailure(errorFromHttpResponse(response, parsed, {
                referenceId: session.id,
                stage: parsed.payload?.stage || 'retrying'
            }));
            return;
        }
        recording.jobFailed = false;
        const payload = parsed.payload || {};
        if (payload.status === 'complete') {
            showSuccess(payload);
            return;
        }
        await pollRecordingJob(payload.status_url || session.statusUrl, session.id, generation);
    }

    async function pollRecordingJob(statusUrl, referenceId, generation) {
        let consecutiveFailures = 0;

        while (generation === pollGeneration && phase === 'processing') {
            await delay(1800);
            if (generation !== pollGeneration || phase !== 'processing') return;

            let response;
            let parsed;
            try {
                response = await fetch(statusUrl, {
                    credentials: 'same-origin',
                    headers: {'Accept': 'application/json', 'X-Recorder-Reference': referenceId},
                    cache: 'no-store'
                });
                parsed = await readResponse(response);
            } catch (error) {
                consecutiveFailures += 1;
                configureProcessingPanel(
                    'Reconnecting to processing status',
                    `The status connection was interrupted. Retrying automatically (${consecutiveFailures}/5).`
                );
                if (consecutiveFailures >= 5) {
                    showFailure(createProcessingError(
                        'The browser lost contact with the processing job. The captured audio is still available and can be retried.',
                        {
                            referenceId: referenceId,
                            stage: 'checking_status',
                            cause: error?.message || String(error)
                        }
                    ));
                    return;
                }
                continue;
            }

            if (!response.ok) {
                showFailure(errorFromHttpResponse(response, parsed, {
                    referenceId: parsed.payload?.reference_id || referenceId,
                    stage: parsed.payload?.stage || 'checking_status'
                }));
                return;
            }

            consecutiveFailures = 0;
            const job = parsed.payload || {};
            const jobReference = job.reference_id || job.job_id || referenceId;
            updateProcessingFromJob(job, jobReference);

            if (job.status === 'complete') {
                showSuccess(job);
                return;
            }
            if (job.status === 'failed') {
                if (pendingRecording) pendingRecording.jobFailed = true;
                showFailure(createProcessingError(
                    job.error || 'The mock interview could not be processed.',
                    {
                        referenceId: jobReference,
                        httpStatus: job.failure_status_code || 'Background job',
                        stage: job.stage || 'processing',
                        events: job.events || [],
                        serverPayload: job
                    }
                ));
                return;
            }
        }
    }

    function updateProcessingFromJob(job, referenceId) {
        const stageTitles = {
            recording: 'Saving recording segments',
            uploading_segments: 'Saving recording segments',
            queued: 'Waiting to process your mock interview',
            transcribing_microphone: 'Transcribing microphone audio',
            transcribing_speaker: 'Transcribing interviewer prompt audio',
            cleaning_transcript: 'Improving transcript quality',
            analyzing: 'Generating interview coaching',
            saving: 'Saving Interview Review'
        };
        configureProcessingPanel(
            stageTitles[job.stage] || 'Processing your mock interview',
            job.stage_message || 'Your mock interview is being processed.'
        );
    }

    function showSuccess(payload) {
        pollGeneration += 1;
        progressPanel.hidden = true;
        errorPanel.hidden = true;
        resultPanel.hidden = false;
        resultTitle.textContent = 'Mock interview saved';
        const linkedMeeting = pendingRecording?.preparedMeeting || null;
        finalizePreparedMeeting(linkedMeeting, payload);
        resultMessage.textContent = linkedMeeting
            ? `${payload.message || 'Your transcript and analysis are ready.'} The application workspace “${linkedMeeting.title}” was linked to this mock interview.`
            : (payload.message || 'Your transcript and analysis are ready.');
        qualityWarning.textContent = payload.quality_warning || '';
        qualityWarning.hidden = !payload.quality_warning;
        reviewLink.href = payload.review_url || AppUI.appUrl('/interview-review');
        pendingRecording = null;
        recordingUploadSession = null;
        microphoneChunks = [];
        speakerChunks = [];
        resetFinalSegmentState();
        lastErrorDiagnostics = '';
        startButton.hidden = true;
        stopButton.hidden = true;
        discardButton.hidden = true;
        resetButton.hidden = false;
        setPhase('complete');
        trackMetric('meeting_processing_succeeded', {
            source: 'browser_recorder',
            duration_seconds: payload.duration_seconds || 0
        }, `meeting-success-${payload.job_id || payload.reference_id || payload.meeting_id || Date.now()}`);
        AppUI.showToast('Mock interview saved successfully.', {type: 'success'});
    }

    function showFailure(error) {
        pollGeneration += 1;
        const normalized = normalizeProcessingError(error);
        trackMetric('meeting_processing_failed', {
            source: 'browser_recorder',
            stage: normalized.stage || 'processing',
            http_status: normalized.httpStatus || '',
            status_text: normalized.statusText || '',
            reference_id: normalized.referenceId || '',
            error_summary: truncate(normalized.message || 'The recording could not be processed.', 240)
        });
        progressPanel.hidden = true;
        resultPanel.hidden = true;
        errorPanel.hidden = false;
        startButton.hidden = true;
        stopButton.hidden = true;
        discardButton.hidden = true;
        resetButton.hidden = true;
        retryButton.disabled = !pendingRecording;
        discardFailedButton.disabled = !pendingRecording;
        captureMeetingAudioInput.disabled = true;

        const recordingText = pendingRecording ? describeRecording(pendingRecording) : 'Audio is not available for retry.';
        errorMessage.textContent = normalized.message;
        errorReference.textContent = normalized.referenceId || 'Unavailable';
        errorStatus.textContent = formatHttpStatus(normalized.httpStatus, normalized.statusText);
        errorStage.textContent = humanizeStage(normalized.stage || 'unknown');
        errorRecording.textContent = recordingText;
        errorRetention.textContent = pendingRecording
            ? 'The recording segments are saved securely and can be retried from this browser tab. Do not refresh or close the page.'
            : 'The browser no longer has a copy of this audio. Start a new recording after reviewing the diagnostic details.';

        lastProcessingError = normalized;
        lastErrorDiagnostics = buildDiagnosticDetails(normalized, pendingRecording);
        errorDetails.textContent = lastErrorDiagnostics;
        resetSupportSubmission();
        setPhase('error');
        AppUI.showToast(normalized.message, {type: 'error', duration: 10000});
    }

    function configureProcessingPanel(title, message) {
        progressPanel.hidden = false;
        progressTitle.textContent = title;
        progressMessage.textContent = message;
    }

    function hideOutcomePanels() {
        progressPanel.hidden = true;
        errorPanel.hidden = true;
        resultPanel.hidden = true;
        qualityWarning.textContent = '';
        qualityWarning.hidden = true;
        resetButton.hidden = true;
        resetSupportSubmission();
    }

    function resetSupportSubmission() {
        lastSupportRequestId = '';
        if (sendErrorButton) {
            sendErrorButton.disabled = false;
            sendErrorButton.dataset.sent = 'false';
            sendErrorButton.textContent = 'Send Error to Support';
        }
        if (supportStatus) {
            supportStatus.hidden = true;
            supportStatus.textContent = '';
            supportStatus.className = 'recorder-support-status';
        }
    }

    async function sendDiagnosticToSupport() {
        if (!sendErrorButton || !lastErrorDiagnostics || !lastProcessingError) return;
        if (sendErrorButton.dataset.sent === 'true') return;

        sendErrorButton.disabled = true;
        sendErrorButton.textContent = 'Sending to Support…';
        if (supportStatus) {
            supportStatus.hidden = false;
            supportStatus.className = 'recorder-support-status is-sending';
            supportStatus.textContent = 'Sending error details securely…';
        }

        const error = normalizeProcessingError(lastProcessingError);
        const payload = {
            reference_id: error.referenceId || '',
            error_message: error.message || '',
            http_status: error.httpStatus || '',
            status_text: error.statusText || '',
            stage: error.stage || 'unknown',
            recording: pendingRecording ? describeRecording(pendingRecording) : 'Audio is not available for retry.',
            occurred_at: new Date().toISOString(),
            page_url: window.location.href,
            diagnostic_details: lastErrorDiagnostics
        };

        try {
            const response = await fetch(AppUI.appUrl('/api/support/recorder-error'), {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            const parsed = await readResponse(response);
            if (!response.ok) {
                throw new Error(
                    parsed.payload?.error
                    || parsed.payload?.message
                    || `The error details could not be sent (${response.status}).`
                );
            }

            lastSupportRequestId = parsed.payload?.request_id || '';
            sendErrorButton.dataset.sent = 'true';
            sendErrorButton.textContent = 'Error Sent to Support';
            if (supportStatus) {
                supportStatus.hidden = false;
                supportStatus.className = 'recorder-support-status is-success';
                supportStatus.textContent = lastSupportRequestId
                    ? `Error details sent to support. Reference: ${lastSupportRequestId}.`
                    : 'Error details sent to support.';
            }
            AppUI.showToast('Error report sent to support.', {type: 'success'});
            trackMetric('recorder_error_support_sent', {
                source: 'browser_recorder',
                stage: error.stage || 'unknown',
                http_status: error.httpStatus || ''
            });
        } catch (error) {
            sendErrorButton.disabled = false;
            sendErrorButton.textContent = 'Send Error to Support';
            if (supportStatus) {
                supportStatus.hidden = false;
                supportStatus.className = 'recorder-support-status is-error';
                supportStatus.textContent = error?.message || 'The error details could not be sent. Please try again.';
            }
            AppUI.showToast('The error details could not be sent.', {type: 'error'});
        }
    }


    function buildDiagnosticDetails(error, recording) {
        const lines = [
            'Mock Interview Recorder Processing Error',
            `Reference ID: ${error.referenceId || 'Unavailable'}`,
            `Time: ${new Date().toISOString()}`,
            `Message: ${error.message}`,
            `HTTP status: ${formatHttpStatus(error.httpStatus, error.statusText)}`,
            `Failed stage: ${humanizeStage(error.stage || 'unknown')}`,
            `Page: ${window.location.href}`,
            `Browser: ${navigator.userAgent}`
        ];

        if (recording) {
            lines.push(`Recording duration: ${formatDuration(recording.durationSeconds)}`);
            if (recording.session?.capturedBytes) {
                lines.push(`Microphone: ${formatBytes(recording.session.capturedBytes.MICROPHONE || 0)} across secure segments`);
                lines.push(recording.session.capturedBytes.SPEAKER
                    ? `Shared audio: ${formatBytes(recording.session.capturedBytes.SPEAKER)} across secure segments`
                    : 'Shared audio: not captured');
                lines.push(`Uploaded segments: ${recording.session.uploadedSegments || 0}`);
                lines.push(`Segments awaiting retry: ${recording.session.failedSegments?.size || 0}`);
            } else {
                lines.push(`Microphone: ${formatBytes(recording.microphoneBlob?.size || 0)} (${recording.microphoneBlob?.type || 'unknown type'})`);
                lines.push(recording.speakerBlob?.size
                    ? `Shared audio: ${formatBytes(recording.speakerBlob.size)} (${recording.speakerBlob.type || 'unknown type'})`
                    : 'Shared audio: not captured');
            }
        }

        if (error.responseText) lines.push(`Server response: ${truncate(error.responseText, 4000)}`);
        if (error.cause) lines.push(`Browser/network detail: ${error.cause}`);
        if (Array.isArray(error.events) && error.events.length) {
            lines.push('', 'Processing timeline:');
            error.events.forEach(function (event) {
                lines.push(`- ${event.timestamp || 'unknown time'} | ${humanizeStage(event.stage || '')} | ${event.message || ''}`);
            });
        }
        return lines.join('\n');
    }

    async function readResponse(response) {
        const responseText = await response.text();
        let payload = null;
        if (responseText) {
            try {
                payload = JSON.parse(responseText);
            } catch (error) {
                payload = null;
            }
        }
        return {payload: payload, responseText: responseText};
    }

    function errorFromHttpResponse(response, parsed, defaults) {
        const payload = parsed.payload || {};
        const responseText = parsed.responseText || '';
        const message = payload.error || payload.message || readableHttpFailure(response.status, response.statusText, responseText);
        return createProcessingError(message, {
            referenceId: payload.reference_id || defaults.referenceId,
            httpStatus: response.status,
            statusText: response.statusText,
            stage: payload.stage || defaults.stage,
            responseText: responseText,
            serverPayload: payload
        });
    }

    function readableHttpFailure(status, statusText, responseText) {
        if (status === 401) return 'Your session expired before the recording could be processed. Sign in again in another tab, then retry here without refreshing this page.';
        if (status === 413) return 'One audio segment exceeded the safe upload limit. Keep this tab open, retry, or record a shorter mock-interview segment.';
        if (status === 429) return 'The service is temporarily rate limited. Wait briefly, then retry processing.';
        if (status === 502) return 'A server or transcription service failed while processing the mock interview.';
        if (status === 503) return 'The mock interview service is temporarily unavailable.';
        if (status === 504) return 'The server took too long to respond while processing the mock interview.';
        const plainText = stripHtml(responseText).trim();
        if (plainText) return truncate(plainText, 700);
        return `The mock interview could not be processed (${status || 'network error'}${statusText ? ` ${statusText}` : ''}).`;
    }

    function createProcessingError(message, details) {
        const error = new Error(message || 'The recording could not be saved.');
        Object.assign(error, details || {});
        return error;
    }

    function normalizeProcessingError(error, defaults) {
        const normalized = error instanceof Error ? error : new Error(String(error || 'The recording could not be saved.'));
        return Object.assign(normalized, defaults || {}, {
            message: normalized.message || 'The recording could not be saved.'
        });
    }

    function readStorage(key, fallback) {
        try {
            const value = window.localStorage.getItem(key);
            return value ? JSON.parse(value) : fallback;
        } catch (error) {
            return fallback;
        }
    }

    function writeStorage(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            // Linking remains optional when browser storage is unavailable.
        }
    }

    function activePreparedMeetings() {
        const meetings = preparedMeetingsCache.length
            ? preparedMeetingsCache
            : readStorage(UPCOMING_MEETINGS_KEY, []);
        return Array.isArray(meetings)
            ? meetings.filter(function (meeting) {
                return meeting && (meeting.id || meeting.meeting_id) && !['completed', 'cancelled'].includes(String(meeting.status || ''));
            }).map(function (meeting) {
                return Object.assign({}, meeting, {id: String(meeting.id || meeting.meeting_id)});
            })
            : [];
    }

    function preparedMeetingLabel(meeting) {
        const scheduled = meeting.scheduled_at ? new Date(meeting.scheduled_at) : null;
        const date = scheduled && !Number.isNaN(scheduled.getTime())
            ? scheduled.toLocaleString(window.AppI18n?.locale || undefined, {dateStyle: 'medium', timeStyle: 'short'})
            : 'Draft';
        return `${meeting.title || 'Untitled application'} · ${date}`;
    }

    async function initializePreparedMeetings() {
        if (!preparedMeetingSelect) return;
        const selectedValue = preparedMeetingSelect.value;
        let activeMeetingId = '';
        try {
            const response = await fetch(AppUI.appUrl('/api/career/application-workspaces'), {
                credentials: 'same-origin',
                headers: {'Accept': 'application/json'}
            });
            if (response.ok) {
                const result = await response.json();
                preparedMeetingsCache = Array.isArray(result.application_workspaces)
                    ? result.application_workspaces
                    : (Array.isArray(result.meetings) ? result.meetings : []);
                activeMeetingId = String(result.active_application_workspace_id || result.active_meeting_id || '');
                writeStorage(UPCOMING_MEETINGS_KEY, preparedMeetingsCache);
            }
        } catch (error) {
            // Keep the previous browser cache available when the server is temporarily unreachable.
        }
        const meetings = activePreparedMeetings();
        preparedMeetingSelect.replaceChildren(new Option('Do not link an application workspace', ''));
        meetings.forEach(function (meeting) {
            preparedMeetingSelect.add(new Option(preparedMeetingLabel(meeting), String(meeting.id)));
        });
        const preferredValue = selectedValue || activeMeetingId;
        if (preferredValue && meetings.some(function (meeting) { return String(meeting.id) === preferredValue; })) {
            preparedMeetingSelect.value = preferredValue;
        }
        preparedMeetingSelect.disabled = phase !== 'ready';
        if (preparedMeetingHelp) {
            preparedMeetingHelp.textContent = meetings.length
                ? 'Link this recording to materials and context prepared for an upcoming interview.'
                : 'No upcoming application workspaces are available. Create one in Application Materials, or record without linking.';
        }
    }

    function getSelectedPreparedMeeting() {
        const selectedId = preparedMeetingSelect?.value || '';
        if (!selectedId) return null;
        return activePreparedMeetings().find(function (meeting) {
            return String(meeting.id) === selectedId;
        }) || null;
    }

    // Real-time interview assistance is intentionally retired. These no-op
    // compatibility hooks keep the imported recorder lifecycle stable while
    // the candidate-facing product supports practice recording only.
    function startLiveQAStreaming() {}

    async function stopLiveQAStreaming() {
        liveQASession = null;
    }

    async function cancelLiveQASession() {
        liveQASession = null;
    }

    function finalizePreparedMeeting(preparedMeeting, payload) {
        if (!preparedMeeting?.id || !payload?.meeting_id) return;
        const completedMeetingId = String(payload.meeting_id);
        const upcomingMeetings = readStorage(UPCOMING_MEETINGS_KEY, []);
        if (Array.isArray(upcomingMeetings)) {
            const completedAt = new Date().toISOString();
            const updated = upcomingMeetings.map(function (meeting) {
                if (String(meeting?.id || '') !== String(preparedMeeting.id)) return meeting;
                return Object.assign({}, meeting, {
                    status: 'completed',
                    completed_at: completedAt,
                    completed_meeting_id: completedMeetingId,
                    updated_at: completedAt
                });
            });
            writeStorage(UPCOMING_MEETINGS_KEY, updated);
        }

        preparedMeetingsCache = activePreparedMeetings().filter(function (meeting) {
            return String(meeting.id) !== String(preparedMeeting.id);
        });
        fetch(AppUI.appUrl(`/api/career/application-workspaces/${encodeURIComponent(preparedMeeting.id)}`), {
            method: 'PUT',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                status: 'completed',
                completed_at: new Date().toISOString(),
                completed_meeting_id: completedMeetingId
            })
        }).catch(function () {});

        [MEETING_MATERIALS_KEY, MEETING_CONTEXTS_KEY].forEach(function (key) {
            const records = readStorage(key, {});
            if (!records || typeof records !== 'object' || !records[preparedMeeting.id]) return;
            records[completedMeetingId] = records[preparedMeeting.id];
            delete records[preparedMeeting.id];
            writeStorage(key, records);
        });
    }

    preparedMeetingSelect?.addEventListener('change', async function () {
        const selected = getSelectedPreparedMeeting();
        try {
            await fetch(AppUI.appUrl('/api/career/active-application'), {
                method: 'PUT',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({meeting_id: selected?.id || ''})
            });
        } catch (error) {
            // Selection is still included directly with browser-recorder uploads.
        }
        if (!preparedMeetingHelp) return;
        preparedMeetingHelp.textContent = selected
            ? `This recording will be linked to “${selected.title}” and its saved materials.`
            : 'Link this recording to materials and context prepared for an upcoming interview.';
    });

    function getAudioTracks(source) {
        const stream = source === 'microphone' ? microphoneStream : speakerAudioStream;
        return stream?.getAudioTracks?.() || [];
    }

    function applyMutePreference(source) {
        const muted = source === 'microphone' ? microphoneMuted : speakerMuted;
        getAudioTracks(source).forEach(function (track) {
            if (track.readyState === 'live') track.enabled = !muted;
        });
    }

    function toggleAudioSourceMute(source) {
        if (!['ready', 'connecting', 'recording'].includes(phase)) return;

        const nextMuted = source === 'microphone' ? !microphoneMuted : !speakerMuted;
        if (source === 'microphone') {
            microphoneMuted = nextMuted;
        } else {
            speakerMuted = nextMuted;
        }

        applyMutePreference(source);
        updateMuteControls();
    }

    function updateMuteControl(source, button, stateElement, card, muted, connected) {
        if (!button || !stateElement) return;

        const isPreRecording = phase === 'ready' || phase === 'connecting';
        const sourceSupported = source === 'microphone'
            ? !getRecorderSupportProblem()
            : sharedAudioSupported;
        const canToggle = sourceSupported && (isPreRecording || phase === 'recording');
        const displayMuted = muted && (connected || isPreRecording);
        button.disabled = !canToggle;
        button.setAttribute('aria-pressed', muted ? 'true' : 'false');
        button.setAttribute('aria-label', `${muted ? 'Unmute' : 'Mute'} ${source}`);
        const buttonLabel = button.querySelector('span');
        if (buttonLabel) buttonLabel.textContent = muted ? 'Unmute' : 'Mute';
        button.classList.toggle('is-muted', displayMuted);
        card?.classList.toggle('is-muted', displayMuted);

        stateElement.classList.toggle('is-muted', displayMuted);
        stateElement.classList.toggle('is-live', connected && !muted);
        if (connected) {
            stateElement.textContent = muted ? 'Muted' : 'Connected';
        } else if (isPreRecording && muted) {
            stateElement.textContent = 'Starts muted';
        } else if (isPreRecording && source === 'microphone') {
            stateElement.textContent = 'Not connected';
        } else if (isPreRecording) {
            stateElement.textContent = sharedAudioSupported
                ? (captureMeetingAudioInput.checked ? 'Not connected' : 'Disabled')
                : 'Unavailable';
        }
    }

    function updateMuteControls() {
        const microphoneConnected = getAudioTracks('microphone').some(function (track) {
            return track.readyState === 'live';
        });
        const speakerConnected = getAudioTracks('interviewer prompt audio').some(function (track) {
            return track.readyState === 'live';
        });

        updateMuteControl(
            'microphone',
            microphoneMuteButton,
            microphoneState,
            microphoneSourceCard,
            microphoneMuted,
            microphoneConnected
        );
        updateMuteControl(
            'interviewer prompt audio',
            speakerMuteButton,
            speakerState,
            speakerSourceCard,
            speakerMuted,
            speakerConnected
        );
    }

    function updateSharedAudioPresentation() {
        const enabled = sharedAudioSupported && captureMeetingAudioInput.checked;
        speakerSourceCard.classList.toggle('is-disabled', !enabled);
        speakerState.textContent = sharedAudioSupported
            ? (enabled ? 'Not connected' : 'Disabled')
            : 'Unavailable';
        speakerMeter.style.width = '0%';
        updateMuteControls();
    }

    function updateLiveQASourceOptions() {
        if (!enableLiveQAInput || !liveQASourceOptions) return;
        const enabled = enableLiveQAInput.checked;
        liveQASourceOptions.hidden = !enabled;
        if (liveQASpeakerInput) {
            liveQASpeakerInput.disabled = enableLiveQAInput.disabled || !captureMeetingAudioInput.checked;
            if (!captureMeetingAudioInput.checked) liveQASpeakerInput.checked = false;
        }
        if (liveQAMicrophoneInput) {
            liveQAMicrophoneInput.disabled = enableLiveQAInput.disabled;
            if (enabled && !captureMeetingAudioInput.checked && !liveQAMicrophoneInput.checked) {
                liveQAMicrophoneInput.checked = true;
            }
        }
    }

    function resetRecorder() {
        pollGeneration += 1;
        cleanupStreams();
        microphoneChunks = [];
        speakerChunks = [];
        microphoneMuted = false;
        speakerMuted = false;
        pendingRecording = null;
        recordingUploadSession = null;
        resetFinalSegmentState();
        lastErrorDiagnostics = '';
        startedAt = null;
        captureMeetingAudioInput.disabled = !sharedAudioSupported;
        if (enableLiveQAInput) enableLiveQAInput.disabled = false;
        updateSharedAudioPresentation();
        updateLiveQASourceOptions();
        startButton.hidden = false;
        startButton.disabled = false;
        stopButton.hidden = true;
        stopButton.disabled = true;
        discardButton.hidden = true;
        discardButton.disabled = true;
        resetButton.hidden = true;
        retryButton.disabled = false;
        discardFailedButton.disabled = false;
        hideOutcomePanels();
        timerElement.textContent = '00:00:00';
        microphoneState.textContent = 'Not connected';
        microphoneState.classList.remove('is-live', 'is-muted');
        speakerState.classList.remove('is-live', 'is-muted');
        microphoneSourceCard?.classList.remove('is-muted');
        speakerSourceCard.classList.remove('is-muted');
        microphoneMeter.style.width = '0%';
        speakerMeter.style.width = '0%';
        setPhase('ready');
        updateMuteControls();
        initializePreparedMeetings();
    }

    function buildRecorder(stream, chunks) {
        const preferredOptions = {audioBitsPerSecond: 20000};
        if (supportedMimeType) preferredOptions.mimeType = supportedMimeType;

        let recorder;
        try {
            recorder = new MediaRecorder(stream, preferredOptions);
        } catch (firstError) {
            try {
                recorder = new MediaRecorder(stream);
            } catch (fallbackError) {
                throw new Error(
                    `Edge could access the audio stream but could not create a recorder: ${fallbackError.message || firstError.message || 'unknown error'}`
                );
            }
        }

        recorder.addEventListener('dataavailable', function (event) {
            if (event.data?.size) chunks.push(event.data);
        });
        return recorder;
    }

    function stopMediaRecorder(recorder) {
        if (!recorder || recorder.state === 'inactive') return Promise.resolve();
        return new Promise(function (resolve, reject) {
            recorder.addEventListener('stop', resolve, {once: true});
            recorder.addEventListener('error', function (event) {
                reject(event.error || new Error('Audio recording failed.'));
            }, {once: true});
            recorder.stop();
        });
    }

    function chooseMimeType() {
        const candidates = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/mp4'
        ];
        return candidates.find(function (candidate) {
            return window.MediaRecorder?.isTypeSupported?.(candidate);
        }) || '';
    }

    function initializeBrowserSupport() {
        const problem = getRecorderSupportProblem();
        if (problem) {
            startButton.disabled = true;
            showCompatibilityProblem(problem, false);
            return;
        }

        if (!sharedAudioSupported) {
            captureMeetingAudioInput.checked = false;
            captureMeetingAudioInput.disabled = true;
            stageDescription.textContent = 'Microphone recording is available, but this browser cannot capture shared tab or system audio.';
        }

        updateSharedAudioPresentation();
        updateLiveQASourceOptions();
    }

    function getRecorderSupportProblem() {
        if (!window.isSecureContext) {
            return {
                title: 'HTTPS is required',
                message: 'Edge blocked microphone access because this page was opened over HTTP. Open the application with an https:// address. Local development may use http://localhost.'
            };
        }
        if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
            return {
                title: 'Microphone API unavailable',
                message: 'Edge cannot access the microphone API for this page. Check Edge Settings → Cookies and site permissions → Microphone, make sure this site is allowed, and confirm the page is not running in Internet Explorer mode or inside a restricted iframe.'
            };
        }
        if (typeof window.MediaRecorder !== 'function') {
            return {
                title: 'Audio recorder unavailable',
                message: 'This Edge installation does not expose MediaRecorder. Update Microsoft Edge and check whether an organization policy or browser extension is disabling media recording.'
            };
        }
        return null;
    }

    function showCompatibilityProblem(problem, showToast = true) {
        setPhase('error');
        stageTitle.textContent = problem.title;
        stageDescription.textContent = problem.message;
        if (showToast) {
            AppUI.showToast(problem.message, {type: 'error', duration: 10000});
        }
    }

    function mimeExtension(type) {
        const normalized = String(type || '').toLowerCase();
        if (normalized.includes('ogg')) return '.ogg';
        if (normalized.includes('mp4')) return '.m4a';
        return '.webm';
    }

    function startTimer() {
        stopTimer();
        updateTimer();
        timerInterval = window.setInterval(updateTimer, 1000);
    }

    function stopTimer() {
        if (timerInterval) window.clearInterval(timerInterval);
        timerInterval = null;
    }

    function updateTimer() {
        const elapsed = Math.max(0, Date.now() - (startedAt?.getTime() || Date.now()));
        timerElement.textContent = formatDuration(Math.floor(elapsed / 1000));
    }

    function startMeter(stream, meterElement, sourceName) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        const context = new AudioContextClass();
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        const source = context.createMediaStreamSource(stream);
        source.connect(analyser);
        const values = new Uint8Array(analyser.frequencyBinCount);
        audioContexts.push(context);

        const animation = {frameId: null};
        meterAnimations.push(animation);
        function draw() {
            const hasEnabledTrack = stream.getAudioTracks().some(function (track) {
                return track.readyState === 'live' && track.enabled;
            });
            if (!hasEnabledTrack) {
                meterElement.style.width = '0%';
                animation.frameId = window.requestAnimationFrame(draw);
                return;
            }
            analyser.getByteFrequencyData(values);
            const average = values.reduce(function (sum, value) { return sum + value; }, 0) / values.length;
            const activity = audioActivity[sourceName];
            if (activity) {
                activity.frames += 1;
                activity.maxLevel = Math.max(activity.maxLevel, average);
                if (average >= liveSpeechLevelThreshold) activity.speechFrames += 1;
            }
            meterElement.style.width = `${Math.min(100, Math.max(3, average * 1.7))}%`;
            animation.frameId = window.requestAnimationFrame(draw);
        }
        draw();
    }

    function cleanupStreams() {
        [microphoneStream, displayStream, speakerAudioStream].forEach(function (stream) {
            stream?.getTracks().forEach(function (track) { track.stop(); });
        });
        microphoneStream = null;
        displayStream = null;
        speakerAudioStream = null;
        microphoneRecorder = null;
        speakerRecorder = null;
        meterAnimations.forEach(function (animation) {
            if (animation.frameId !== null) window.cancelAnimationFrame(animation.frameId);
        });
        meterAnimations = [];
        audioContexts.forEach(function (context) { context.close().catch(function () {}); });
        audioContexts = [];
        microphoneMeter.style.width = '0%';
        speakerMeter.style.width = '0%';
        microphoneState.classList.remove('is-live', 'is-muted');
        speakerState.classList.remove('is-live', 'is-muted');
        microphoneSourceCard?.classList.remove('is-muted');
        speakerSourceCard.classList.remove('is-muted');
        updateMuteControls();
    }

    function syncRecordingActivity() {
        const activePhases = ['recording', 'stopping', 'processing'];
        if (!activePhases.includes(phase)) {
            clearRecordingActivity();
            return;
        }

        writeRecordingActivity();
        if (!recordingHeartbeatInterval) {
            recordingHeartbeatInterval = window.setInterval(writeRecordingActivity, 10000);
        }
    }

    function writeRecordingActivity() {
        try {
            window.localStorage.setItem(ACTIVE_RECORDING_KEY, JSON.stringify({
                instanceId: recorderInstanceId,
                phase: phase,
                startedAt: startedAt?.toISOString() || null,
                heartbeatAt: Date.now()
            }));
        } catch (error) {
            // Recording remains usable when browser storage is unavailable.
        }
    }

    function clearRecordingActivity() {
        if (recordingHeartbeatInterval) {
            window.clearInterval(recordingHeartbeatInterval);
            recordingHeartbeatInterval = null;
        }
        try {
            const storedStatus = JSON.parse(window.localStorage.getItem(ACTIVE_RECORDING_KEY) || 'null');
            if (!storedStatus || !storedStatus.instanceId || storedStatus.instanceId === recorderInstanceId) {
                window.localStorage.removeItem(ACTIVE_RECORDING_KEY);
            }
        } catch (error) {
            // No action is required when browser storage is unavailable.
        }
    }

    function setPhase(nextPhase) {
        phase = nextPhase;
        statusBadge.dataset.state = nextPhase;
        if (openLiveQALink) openLiveQALink.hidden = nextPhase !== 'recording';
        const copy = {
            ready: ['Ready', 'Ready to record', 'Your browser will ask for microphone access and, optionally, which interviewer-prompt tab or screen to share.'],
            connecting: ['Connecting', 'Connecting audio sources', 'Approve the browser permission prompts to continue.'],
            recording: ['Recording', 'Recording in progress', 'Audio is being saved in short secure segments while you record. Choose Stop & Process when the practice session ends.'],
            stopping: ['Stopping', 'Finalizing audio', 'Saving the last audio segments before processing begins.'],
            discarding: ['Discarding', 'Discarding recording', 'Stopping capture and deleting the current audio without saving or processing it.'],
            processing: ['Processing', 'Creating your interview review', 'The saved audio segments are being transcribed and analyzed.'],
            complete: ['Saved', 'Mock interview ready', 'Your mock interview is available in Interview Review.'],
            error: ['Action needed', 'Recording not saved', 'The detailed error is shown below. Retry processing or send the diagnostic information directly to support.']
        }[nextPhase] || ['Ready', 'Ready to record', ''];
        statusText.textContent = copy[0];
        stageTitle.textContent = copy[1];
        stageDescription.textContent = copy[2];
        syncRecordingActivity();
    }

    function readableMediaError(error) {
        if (error?.name === 'NotAllowedError') {
            return 'Recording permission was declined. Allow microphone and screen/audio sharing, then try again.';
        }
        if (error?.name === 'NotFoundError') {
            return 'No usable microphone or shared audio device was found.';
        }
        if (error?.name === 'NotReadableError') {
            return 'The selected audio device is already in use or could not be opened.';
        }
        return error?.message || 'The browser could not start recording.';
    }

    function createReferenceId() {
        return window.crypto?.randomUUID?.() || `rec-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }

    function describeRecording(recording) {
        const session = recording?.session;
        if (session?.capturedBytes) {
            const parts = [
                formatDuration(recording.durationSeconds),
                `mic ${formatBytes(session.capturedBytes.MICROPHONE || 0)}`
            ];
            if (session.capturedBytes.SPEAKER) {
                parts.push(`shared ${formatBytes(session.capturedBytes.SPEAKER)}`);
            }
            const segmentCount = session.uploadedSegments + (session.failedSegments?.size || 0);
            if (segmentCount) parts.push(`${segmentCount} segment${segmentCount === 1 ? '' : 's'}`);
            return parts.join(' · ');
        }
        const microphoneSize = recording?.microphoneBlob?.size || 0;
        const parts = [formatDuration(recording?.durationSeconds || 0), `mic ${formatBytes(microphoneSize)}`];
        if (recording?.speakerBlob?.size) parts.push(`shared ${formatBytes(recording.speakerBlob.size)}`);
        return parts.join(' · ');
    }

    function formatDuration(totalSeconds) {
        const safeSeconds = Math.max(0, Number(totalSeconds) || 0);
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const seconds = Math.floor(safeSeconds % 60);
        return [hours, minutes, seconds]
            .map(function (value) { return String(value).padStart(2, '0'); })
            .join(':');
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value < 1024) return `${value} B`;
        if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
        return `${(value / (1024 * 1024)).toFixed(2)} MB`;
    }

    function formatHttpStatus(status, statusText) {
        if (!status) return 'Unavailable';
        if (typeof status === 'string' && !/^\d+$/.test(status)) return status;
        return `${status}${statusText ? ` ${statusText}` : ''}`;
    }

    function humanizeStage(stage) {
        return String(stage || 'unknown')
            .replace(/_/g, ' ')
            .replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
    }

    function stripHtml(value) {
        const container = document.createElement('div');
        container.innerHTML = String(value || '');
        return container.textContent || container.innerText || '';
    }

    function truncate(value, maxLength) {
        const text = String(value || '');
        return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
    }

    function delay(milliseconds) {
        return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); });
    }
})();
