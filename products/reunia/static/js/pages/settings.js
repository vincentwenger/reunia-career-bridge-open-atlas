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
        'live-qa-settings': {
            title: 'Live interview assistance',
            description: 'Choose approved input sources, feed update frequency, and temporary history retention.'
        },
        'meeting-review-settings': {
            title: 'Review & follow-up',
            description: 'Choose how mock interviews generate answer coaching, practice actions, and interview scorecards.'
        },
        'privacy-sharing-settings': {
            title: 'Privacy & sharing',
            description: 'Set retention periods for new content and secure defaults for newly created public links.'
        }
    };
    if (!document.getElementById('live-qa-settings')) {
        delete categoryContent['live-qa-settings'];
    }

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

        const retentionHoursInput = document.getElementById('retentionHours');
        const updateFrequencyInput = document.getElementById('liveQaAnswerUpdateFrequency');
        const retentionHoursValue = retentionHoursInput ? parseInt(retentionHoursInput.value, 10) : null;
        const answerUpdateFrequency = updateFrequencyInput?.value || null;
        const selectedScorecardSource = document.querySelector('input[name="scorecard_source"]:checked')
            || document.querySelector('input[name="scorecard_source"]');
        const scorecardSourceValue = selectedScorecardSource ? selectedScorecardSource.value : 'microphone';
        const languageValue = document.getElementById('language')?.value || 'en';
        const meetingRetentionDays = parseInt(document.getElementById('meetingRetentionDays').value, 10);
        const documentRetentionDays = parseInt(document.getElementById('documentRetentionDays').value, 10);
        const shareDefaultExpirationDays = parseInt(document.getElementById('shareDefaultExpirationDays').value, 10);
        const meetingSummaryDetail = document.getElementById('meetingSummaryDetail').value;

        if (retentionHoursInput && (isNaN(retentionHoursValue) || retentionHoursValue < 1 || retentionHoursValue > 24)) {
            showToast('error', 'Message expiration must be between 1 and 24 hours.');
            showSettingsScope('live-qa-settings');
            retentionHoursInput.focus();
            return;
        }

        if (updateFrequencyInput && !['fast', 'balanced', 'efficient'].includes(answerUpdateFrequency)) {
            showToast('error', 'Please select a valid answer update frequency.');
            showSettingsScope('live-qa-settings');
            updateFrequencyInput.focus();
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

        if (![0, 7, 30, 90].includes(shareDefaultExpirationDays)) {
            showToast('error', 'Please select a valid default share-link expiration.');
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
            aiModelPreset: document.getElementById('aiModelPreset').value,
            scorecard_source: scorecardSourceValue,
            meetingRetentionDays,
            documentRetentionDays,
            shareDefaultExpirationDays,
            shareRequirePassword: document.getElementById('shareRequirePassword').checked,
            shareAllowDownload: document.getElementById('shareAllowDownload').checked,
            shareIncludeScorecard: document.getElementById('shareIncludeScorecard').checked,
            meetingSummaryDetail,
            meetingExtractActionItems: document.getElementById('meetingExtractActionItems').checked,
            meetingGenerateScorecard: document.getElementById('meetingGenerateScorecard').checked
        };
        if (retentionHoursInput) {
            settingsData.retentionHours = retentionHoursValue;
            settingsData.liveQaAnswerUpdateFrequency = answerUpdateFrequency;
            settingsData.aiClipboard = Boolean(document.getElementById('aiClipboard')?.checked);
            settingsData.aiSpeaker = Boolean(document.getElementById('aiSpeaker')?.checked);
            settingsData.aiMicrophone = Boolean(document.getElementById('aiMicrophone')?.checked);
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
