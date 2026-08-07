document.addEventListener('DOMContentLoaded', function () {
    const settingsForm = document.getElementById('settingsForm');
    const saveSettingsBtn = document.getElementById('saveSettingsBtn');
    const resetSettingsBtn = document.getElementById('resetSettingsBtn');
    const settingsToast = document.getElementById('settings-toast');
    const scopeButtons = Array.from(document.querySelectorAll('[data-settings-target]'));
    const categorySelect = document.getElementById('settings-category-select');
    const mobileDirtyMarker = document.querySelector('.settings-mobile-dirty');
    const workspaceBody = document.getElementById('workspace-body');
    const workspaceTitle = document.getElementById('workspace-title');
    const workspaceDescription = document.getElementById('workspace-description');
    const saveNote = document.getElementById('save-note');
    const categorySections = Array.from(document.querySelectorAll('.category-section'));
    const generateScorecard = document.getElementById('meetingGenerateScorecard');
    const scorecardSourceSelector = document.getElementById('scorecard-source-selector');

    const categoryContent = {
        'global-settings': {
            title: 'General & AI',
            description: 'Set your application language and the default AI performance level used across Réunia.'
        },
        'ai-coaching-settings': {
            title: 'AI Coaching Preferences',
            description: 'Choose the default answer style, response mode, and reusable audio or clipboard instructions.'
        },
        'meeting-review-settings': {
            title: 'Review & follow-up',
            description: 'Choose how mock interviews generate answer coaching, practice actions, and interview scorecards.'
        },
        'privacy-sharing-settings': {
            title: 'Data & privacy',
            description: 'Set retention periods for newly saved mock interviews and uploaded documents.'
        }
    };

    let baseline = new Map();
    let dirtyCategories = new Set();
    let isSaving = false;

    function translate(message) {
        return window.AppI18n?.t(message) || message;
    }

    function showToast(type, message) {
        if (!settingsToast) {
            return;
        }

        settingsToast.className = 'toast ' + type;
        settingsToast.textContent = message;
    }

    function clearToast() {
        if (!settingsToast) {
            return;
        }

        settingsToast.className = 'toast';
        settingsToast.textContent = '';
    }

    function getSectionCategory(section) {
        return section.dataset.settingsCategory || section.id;
    }

    function getElementCategory(element) {
        const section = element.closest('.category-section');
        return section ? getSectionCategory(section) : '';
    }

    function showSettingsScope(targetId) {
        if (!categoryContent[targetId]) {
            targetId = 'global-settings';
        }

        categorySections.forEach(function (section) {
            section.hidden = getSectionCategory(section) !== targetId;
        });

        scopeButtons.forEach(function (button) {
            const isActive = button.dataset.settingsTarget === targetId;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-pressed', String(isActive));
        });

        if (categorySelect) {
            categorySelect.value = targetId;
        }

        if (workspaceTitle) {
            workspaceTitle.textContent = translate(categoryContent[targetId].title);
        }

        if (workspaceDescription) {
            workspaceDescription.textContent = translate(categoryContent[targetId].description);
        }

        workspaceBody?.classList.remove('is-empty');

        if (window.history?.replaceState) {
            window.history.replaceState(null, '', '#' + targetId);
        }
    }

    function editableElements() {
        if (!settingsForm) {
            return [];
        }

        return Array.from(settingsForm.elements).filter(function (element) {
            return element.matches('input[name], select[name], textarea[name]') && element.type !== 'submit';
        });
    }

    function elementValue(element) {
        if (element.type === 'checkbox' || element.type === 'radio') {
            return element.checked;
        }
        return element.value;
    }

    function captureBaseline() {
        baseline = new Map();
        editableElements().forEach(function (element) {
            baseline.set(element, elementValue(element));
        });
        updateDirtyState();
    }

    function updateDirtyState() {
        dirtyCategories = new Set();

        editableElements().forEach(function (element) {
            if (baseline.has(element) && baseline.get(element) !== elementValue(element)) {
                const category = getElementCategory(element);
                if (category) {
                    dirtyCategories.add(category);
                }
            }
        });

        scopeButtons.forEach(function (button) {
            const isDirty = dirtyCategories.has(button.dataset.settingsTarget);
            button.classList.toggle('is-dirty', isDirty);
            const marker = button.querySelector('[data-settings-dirty]');
            if (marker) {
                marker.hidden = !isDirty;
            }
        });

        document.querySelector('.settings-mobile-nav')?.classList.toggle('has-dirty', dirtyCategories.size > 0);
        if (mobileDirtyMarker) {
            mobileDirtyMarker.hidden = dirtyCategories.size === 0;
        }

        const changedFields = editableElements().filter(function (element) {
            return baseline.has(element) && baseline.get(element) !== elementValue(element);
        }).length;

        if (saveNote) {
            if (changedFields === 0) {
                saveNote.textContent = translate('No unsaved changes');
            } else {
                const categoryLabel = dirtyCategories.size === 1 ? 'category' : 'categories';
                const changeLabel = changedFields === 1 ? 'change' : 'changes';
                saveNote.textContent = changedFields + ' unsaved ' + changeLabel + ' in ' + dirtyCategories.size + ' ' + categoryLabel;
            }
        }

        if (saveSettingsBtn) {
            saveSettingsBtn.disabled = isSaving || changedFields === 0;
        }
        if (resetSettingsBtn) {
            resetSettingsBtn.disabled = isSaving || changedFields === 0;
        }
    }

    function updateScorecardDependency() {
        if (!generateScorecard || !scorecardSourceSelector) {
            return;
        }

        const enabled = generateScorecard.checked;
        scorecardSourceSelector.classList.toggle('is-disabled', !enabled);
        scorecardSourceSelector.setAttribute('aria-disabled', String(!enabled));
        scorecardSourceSelector.querySelectorAll('input[type="radio"]').forEach(function (radio) {
            radio.disabled = !enabled;
        });
    }

    function updateRetentionWarnings() {
        document.querySelectorAll('[data-retention-control]').forEach(function (control) {
            const select = control.querySelector('select');
            const warning = control.querySelector('[data-retention-warning]');
            const automaticDeletion = select && select.value !== '0';
            control.classList.toggle('has-retention-warning', Boolean(automaticDeletion));
            if (warning) {
                warning.hidden = !automaticDeletion;
            }
        });
    }

    function updateDependencies() {
        updateScorecardDependency();
        updateRetentionWarnings();
    }

    scopeButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            showSettingsScope(button.dataset.settingsTarget);
        });
    });

    categorySelect?.addEventListener('change', function () {
        showSettingsScope(categorySelect.value);
    });

    if (!settingsForm || !saveSettingsBtn) {
        return;
    }

    settingsForm.addEventListener('input', function () {
        clearToast();
        updateDependencies();
        updateDirtyState();
    });

    settingsForm.addEventListener('change', function () {
        clearToast();
        updateDependencies();
        updateDirtyState();
    });

    resetSettingsBtn?.addEventListener('click', function () {
        baseline.forEach(function (value, element) {
            if (element.type === 'checkbox' || element.type === 'radio') {
                element.checked = value;
            } else {
                element.value = value;
            }
        });
        clearToast();
        updateDependencies();
        updateDirtyState();
    });

    settingsForm.addEventListener('submit', async function (event) {
        event.preventDefault();
        clearToast();

        if (dirtyCategories.size === 0) {
            return;
        }

        const selectedScorecardSource = document.querySelector('input[name="scorecard_source"]:checked')
            || document.querySelector('input[name="scorecard_source"]');
        const scorecardSourceValue = selectedScorecardSource ? selectedScorecardSource.value : 'microphone';
        const languageValue = document.getElementById('language')?.value || 'en';
        const meetingRetentionDays = parseInt(document.getElementById('meetingRetentionDays').value, 10);
        const documentRetentionDays = parseInt(document.getElementById('documentRetentionDays').value, 10);
        const meetingSummaryDetail = document.getElementById('meetingSummaryDetail').value;
        const coachingAnswerStyle = document.getElementById('aiCoachingAnswerStyle')?.value || 'balanced';
        const coachingResponseMode = document.getElementById('aiCoachingResponseMode')?.value || 'ready_to_say';

        if (!['balanced', 'concise', 'detailed', 'bullet_points', 'step_by_step', 'action_oriented', 'professional'].includes(coachingAnswerStyle)) {
            showToast('error', translate('Please select a valid answer style.'));
            showSettingsScope('ai-coaching-settings');
            document.getElementById('aiCoachingAnswerStyle')?.focus();
            return;
        }

        if (!['ready_to_say', 'concise_structured_action', 'coaching'].includes(coachingResponseMode)) {
            showToast('error', translate('Please select a valid response mode.'));
            showSettingsScope('ai-coaching-settings');
            document.getElementById('aiCoachingResponseMode')?.focus();
            return;
        }

        if (!['en', 'fr'].includes(languageValue)) {
            showToast('error', window.AppI18n?.t('Please select a valid application language.') || 'Please select a valid application language.');
            showSettingsScope('global-settings');
            document.getElementById('language')?.focus();
            return;
        }

        if (!['microphone', 'speaker', 'all'].includes(scorecardSourceValue)) {
            showToast('error', 'Please select a valid Scorecard Source.');
            showSettingsScope('meeting-review-settings');
            const firstScorecardSource = document.querySelector('input[name="scorecard_source"]');
            if (firstScorecardSource) {
                firstScorecardSource.focus();
            }
            return;
        }

        if (![0, 7, 30, 90, 365].includes(meetingRetentionDays)
            || ![0, 7, 30, 90, 365].includes(documentRetentionDays)) {
            showToast('error', 'Please select valid data-retention periods.');
            showSettingsScope('privacy-sharing-settings');
            return;
        }

        if (!['brief', 'standard', 'detailed'].includes(meetingSummaryDetail)) {
            showToast('error', 'Please select a valid interview-review summary detail level.');
            showSettingsScope('meeting-review-settings');
            return;
        }

        const settingsData = {
            language: languageValue,
            aiCoachingAnswerStyle: coachingAnswerStyle,
            aiCoachingResponseMode: coachingResponseMode,
            aiCoachingAudioInstructions: document.getElementById('aiCoachingAudioInstructions')?.value.trim() || '',
            aiCoachingClipboardInstructions: document.getElementById('aiCoachingClipboardInstructions')?.value.trim() || '',
            scorecard_source: scorecardSourceValue,
            meetingRetentionDays,
            documentRetentionDays,
            meetingSummaryDetail,
            meetingExtractActionItems: document.getElementById('meetingExtractActionItems').checked,
            meetingGenerateScorecard: document.getElementById('meetingGenerateScorecard').checked
        };
        const aiModelPresetInput = document.getElementById('aiModelPreset');
        if (aiModelPresetInput) {
            settingsData.aiModelPreset = aiModelPresetInput.value;
        }

        isSaving = true;
        saveSettingsBtn.textContent = translate('Saving...');
        updateDirtyState();

        try {
            const response = await fetch(window.AppUI?.appUrl('/update-settings') || '/update-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settingsData)
            });

            let result = {};
            try {
                result = await response.json();
            } catch (jsonError) {
                result = {};
            }

            if (response.ok) {
                window.localStorage.setItem('reunia-language', result.language || languageValue);
                captureBaseline();
                showToast('success', window.AppI18n?.t('Settings saved successfully.') || 'Settings saved successfully.');
                if ((window.AppI18n?.language || document.documentElement.lang) !== (result.language || languageValue)) {
                    window.setTimeout(function () { window.location.reload(); }, 350);
                }
            } else {
                showToast('error', 'Error updating settings: ' + (result.error || 'Unknown error occurred.'));
            }
        } catch (error) {
            console.error('Request failed:', error);
            showToast('error', 'An unexpected network error occurred while saving your settings.');
        } finally {
            isSaving = false;
            saveSettingsBtn.textContent = translate('Save changes');
            updateDirtyState();
        }
    });

    const requestedCategory = window.location.hash.replace('#', '');
    showSettingsScope(categoryContent[requestedCategory] ? requestedCategory : 'global-settings');
    updateDependencies();
    captureBaseline();
});
