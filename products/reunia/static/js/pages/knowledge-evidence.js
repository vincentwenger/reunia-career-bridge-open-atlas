'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('knowledgePage');
    const modal = document.getElementById('evidenceModal');
    const form = document.getElementById('manualEvidenceForm');
    if (!root || !modal || !form) return;

    const appUrl = window.AppUI?.appUrl || (path => path);
    const endpoint = root.dataset.evidenceAnswersEndpoint || appUrl('/api/career/evidence/answers');
    const roleSelect = document.getElementById('manualEvidenceRoleSelect');
    const statusSelect = document.getElementById('manualEvidenceStatus');
    const workflowStatusNote = document.getElementById('workflowEvidenceStatusNote');
    let trigger = null;
    let editingEntryMethod = 'manual';

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
        window.setTimeout(() => { toast.hidden = true; }, 3800);
    }

    function setStatusEditingMode(entryMethod) {
        editingEntryMethod = entryMethod === 'workflow' ? 'workflow' : 'manual';
        const isManual = editingEntryMethod === 'manual';
        if (statusSelect) statusSelect.disabled = !isManual;
        if (workflowStatusNote) workflowStatusNote.hidden = isManual;
    }

    function resetForm() {
        form.reset();
        setStatusEditingMode('manual');
        document.getElementById('manualEvidenceId').value = '';
        document.getElementById('evidenceModalTitle').textContent = 'Add confirmed evidence';
        document.getElementById('saveManualEvidenceButton').textContent = 'Add confirmed evidence';
    }

    function openModal(source) {
        trigger = source || document.activeElement;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        root.inert = true;
        window.requestAnimationFrame(() => document.getElementById('manualEvidenceTitle')?.focus());
    }

    function closeModal() {
        modal.hidden = true;
        document.body.style.overflow = '';
        root.inert = false;
        trigger?.focus?.();
        trigger = null;
    }

    function setValue(id, value) {
        const element = document.getElementById(id);
        if (element) element.value = String(value || '');
    }

    function openForEdit(row, source) {
        resetForm();
        setStatusEditingMode(row.dataset.evidenceEntryMethod || 'workflow');
        setValue('manualEvidenceId', row.dataset.evidenceId);
        setValue('manualEvidenceTitle', row.dataset.evidenceTitle);
        setValue('manualEvidenceStatement', row.querySelector('textarea[name="answer_text"]')?.value);
        setValue('manualEvidenceEmployer', row.dataset.evidenceEmployer);
        setValue('manualEvidenceRole', row.dataset.evidenceRole);
        setValue('manualEvidenceDates', row.dataset.evidenceDates);
        setValue('manualEvidenceSkills', row.dataset.evidenceSkills);
        setValue('manualEvidenceSource', row.dataset.evidenceSource);
        setValue('manualEvidenceLimitations', row.dataset.evidenceLimitations);
        if (editingEntryMethod === 'manual') {
            setValue('manualEvidenceStatus', row.dataset.evidenceConfirmationStatus || 'confirmed');
        }
        if (roleSelect) roleSelect.value = '';
        document.getElementById('evidenceModalTitle').textContent = 'Edit evidence details';
        document.getElementById('saveManualEvidenceButton').textContent = 'Save evidence';
        openModal(source);
    }

    document.addEventListener('click', event => {
        const editButton = event.target.closest('[data-edit-evidence]');
        if (editButton) {
            const row = editButton.closest('[data-evidence-answer-row]');
            if (row) openForEdit(row, editButton);
            return;
        }
        const addButton = event.target.closest('#openEvidenceModal, [data-open-evidence-modal]');
        if (addButton) {
            resetForm();
            openModal(addButton);
        }
    });

    roleSelect?.addEventListener('change', () => {
        const option = roleSelect.selectedOptions?.[0];
        if (!option?.value) return;
        setValue('manualEvidenceEmployer', option.dataset.employer);
        setValue('manualEvidenceRole', option.dataset.role);
        setValue('manualEvidenceDates', option.dataset.dates);
    });

    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeModal();
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const data = new FormData(form);
        const evidenceId = String(data.get('evidence_id') || '').trim();
        const title = String(data.get('evidence_title') || '').trim();
        const statement = String(data.get('confirmed_statement') || '').trim();
        if (!title || !statement) {
            showToast('Add an evidence title and a factual confirmed statement.', true);
            return;
        }
        const supportedSkills = String(data.get('supported_skills') || '').trim();
        const payload = {
            evidence_title: title,
            question: title,
            confirmed_statement: statement,
            answer_text: statement,
            experience_employer: String(data.get('experience_employer') || '').trim(),
            experience_title: String(data.get('experience_title') || '').trim(),
            experience_dates: String(data.get('experience_dates') || '').trim(),
            supported_skills: supportedSkills,
            requirement: supportedSkills,
            source_note: String(data.get('source_note') || '').trim(),
            evidence_limitations: String(data.get('evidence_limitations') || '').trim(),
        };
        if (!evidenceId || editingEntryMethod === 'manual') {
            payload.confirmation_status = String(data.get('confirmation_status') || 'confirmed').trim();
        }
        const saveButton = document.getElementById('saveManualEvidenceButton');
        saveButton.disabled = true;
        saveButton.textContent = evidenceId ? 'Saving…' : 'Adding…';
        try {
            const url = evidenceId ? `${endpoint}/${encodeURIComponent(evidenceId)}` : endpoint;
            const response = await fetch(url, {
                method: evidenceId ? 'PUT' : 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(result.error || result.message || 'The confirmed evidence could not be saved.');
            }
            closeModal();
            showToast(evidenceId ? 'Evidence details updated.' : 'Confirmed evidence added.');
            window.setTimeout(() => window.location.reload(), 250);
        } catch (error) {
            showToast(error.message || 'The confirmed evidence could not be saved.', true);
        } finally {
            saveButton.disabled = false;
            saveButton.textContent = evidenceId ? 'Save evidence' : 'Add confirmed evidence';
        }
    });
});
