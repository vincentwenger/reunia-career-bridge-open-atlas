(function () {
    'use strict';

    const profileForm = document.getElementById('profileForm');
    if (!profileForm) return;

    const saveProfileBtn = document.getElementById('saveProfileBtn');
    const profileToast = document.getElementById('profile-toast');
    const profileSaveState = document.getElementById('profileSaveState');
    const fullNameInput = document.getElementById('fullName');
    const jobTitleInput = document.getElementById('jobTitle');
    if (!saveProfileBtn || !profileToast || !profileSaveState || !fullNameInput || !jobTitleInput) return;

    const saveButtonLabel = saveProfileBtn.textContent.trim();

    let savedProfile = readProfile();

    function translated(value) {
        return window.AppI18n?.t(value) || value;
    }

    function readProfile() {
        return {
            fullName: fullNameInput.value.trim(),
            jobTitle: jobTitleInput.value.trim()
        };
    }

    function profilesMatch(left, right) {
        return left.fullName === right.fullName && left.jobTitle === right.jobTitle;
    }

    function showToast(type, message) {
        const isError = type === 'error';
        profileToast.className = `toast profile-save-feedback ${type}`;
        profileToast.setAttribute('role', isError ? 'alert' : 'status');
        profileToast.setAttribute('aria-live', isError ? 'assertive' : 'polite');
        profileToast.textContent = message;
    }

    function clearToast() {
        profileToast.className = 'toast profile-save-feedback';
        profileToast.setAttribute('role', 'status');
        profileToast.setAttribute('aria-live', 'polite');
        profileToast.textContent = '';
    }

    function updateDirtyState() {
        const isDirty = !profilesMatch(readProfile(), savedProfile);
        profileForm.classList.toggle('is-dirty', isDirty);
        profileSaveState.classList.toggle('is-dirty', isDirty);
        profileSaveState.textContent = isDirty
            ? translated('Unsaved changes')
            : translated('Saved');
    }

    function updateProfileSummary(profile) {
        document.getElementById('summaryName').textContent = profile.fullName || translated('Your Profile');
        document.getElementById('summaryJobTitle').textContent = profile.jobTitle || translated('Not provided yet');
        document.getElementById('profileAvatar').textContent = profile.fullName
            ? profile.fullName.charAt(0).toUpperCase()
            : 'U';
    }

    function updateNavbar(profile) {
        if (!profile.fullName) return;

        const initial = profile.fullName.charAt(0).toUpperCase();
        const textUpdates = {
            navbarProfileAvatar: initial,
            navbarProfileName: profile.fullName,
            navbarDropdownAvatar: initial,
            navbarDropdownName: profile.fullName
        };

        Object.entries(textUpdates).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) element.textContent = value;
        });
    }

    function setSaving(isSaving) {
        saveProfileBtn.disabled = isSaving;
        saveProfileBtn.setAttribute('aria-busy', String(isSaving));
        profileForm.setAttribute('aria-busy', String(isSaving));
        saveProfileBtn.textContent = isSaving ? translated('Saving...') : translated(saveButtonLabel);
    }

    [fullNameInput, jobTitleInput].forEach((input) => {
        input.addEventListener('input', function () {
            input.removeAttribute('aria-invalid');
            if (input === fullNameInput) input.setCustomValidity('');
            clearToast();
            updateDirtyState();
        });
    });

    profileForm.addEventListener('submit', async function (event) {
        event.preventDefault();
        clearToast();

        const profileData = readProfile();

        if (!profileData.fullName) {
            const message = translated('Full name is required.');
            fullNameInput.setCustomValidity(message);
            fullNameInput.setAttribute('aria-invalid', 'true');
            showToast('error', message);
            fullNameInput.reportValidity();
            fullNameInput.focus();
            return;
        }

        fullNameInput.setCustomValidity('');
        setSaving(true);

        try {
            const response = await fetch(window.AppUI?.appUrl('/update-profile') || '/update-profile', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    'Full Name': profileData.fullName,
                    'Job Title': profileData.jobTitle
                })
            });

            let result = {};
            try {
                result = await response.json();
            } catch (jsonError) {
                result = {};
            }

            if (response.ok) {
                fullNameInput.value = profileData.fullName;
                jobTitleInput.value = profileData.jobTitle;
                savedProfile = profileData;
                updateProfileSummary(savedProfile);
                updateNavbar(savedProfile);
                updateDirtyState();
                showToast('success', translated('Profile saved successfully.'));
            } else {
                const detail = result.error || translated('Unknown error occurred.');
                showToast('error', `${translated('Error updating profile:')} ${detail}`);
            }
        } catch (error) {
            console.error(translated('Request failed:'), error);
            showToast('error', translated('An unexpected network error occurred while saving your profile.'));
        } finally {
            setSaving(false);
        }
    });

    updateDirtyState();
})();
