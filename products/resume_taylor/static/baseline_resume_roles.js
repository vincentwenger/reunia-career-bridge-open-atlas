(() => {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  function initializeBaselineRoles() {
    const panel = document.getElementById('employment-roles');
    if (!panel) return;

    const endpoint = String(panel.dataset.careerRolesEndpoint || '').replace(/\/$/, '');
    if (!endpoint) return;

    const feedback = panel.querySelector('[data-baseline-role-feedback]');
    const showFeedback = (message, isError = false) => {
      if (window.CareerBridgeBaselineUI?.showFeedback) {
        window.CareerBridgeBaselineUI.showFeedback(feedback, message, isError);
        return;
      }
      if (!feedback) return;
      feedback.hidden = false;
      feedback.textContent = String(message || '');
      feedback.classList.toggle('is-error', isError);
      feedback.classList.toggle('is-success', !isError);
    };

    const refreshRoles = async (message, focusSelector = '') => {
      if (!window.CareerBridgeBaselineUI?.refreshSections) return;
      await window.CareerBridgeBaselineUI.refreshSections(
        ['#employment-roles', '#baseline-resume-preview-region'],
        {
          feedbackSelector: '[data-baseline-role-feedback]',
          message,
          focusSelector
        }
      );
    };

    const addForm = panel.querySelector('[data-add-career-role-form]');
    if (addForm && addForm.dataset.baselineBound !== 'true') {
      addForm.dataset.baselineBound = 'true';
      addForm.addEventListener('submit', async event => {
        event.preventDefault();
        const button = addForm.querySelector('button[type="submit"]');
        const data = new FormData(addForm);
        const payload = {
          official_title: String(data.get('official_title') || '').trim(),
          employer: String(data.get('employer') || '').trim(),
          dates: String(data.get('dates') || '').trim(),
          location: String(data.get('location') || '').trim(),
          responsibilities: String(data.get('responsibilities') || '').trim()
        };
        if (!payload.official_title || !payload.employer) {
          showFeedback('Add the official job title and employer before saving.', true);
          return;
        }
        if (button) {
          button.disabled = true;
          button.textContent = 'Adding…';
        }
        try {
          const response = await fetch(endpoint, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(csrfToken ? {'X-CSRFToken': csrfToken} : {})
            },
            body: JSON.stringify(payload)
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || result.message || 'The employment role could not be added.');
          await refreshRoles(result.message || 'Employment role added.', '[data-career-role-row] input[name="official_title"]');
        } catch (error) {
          showFeedback(error.message || 'The employment role could not be added.', true);
          if (button) {
            button.disabled = false;
            button.textContent = 'Add employment role';
          }
        }
      });
    }

    panel.querySelectorAll('[data-career-role-form]').forEach(form => {
      if (form.dataset.baselineBound === 'true') return;
      form.dataset.baselineBound = 'true';
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const roleId = String(form.dataset.roleId || '').trim();
        if (!roleId) return;

        const row = form.closest('[data-career-role-row]');
        const submitButton = row?.querySelector(`button[type="submit"][form="${CSS.escape(form.id)}"]`);
        const data = new FormData(form);
        const payload = {
          official_title: String(data.get('official_title') || '').trim(),
          employer: String(data.get('employer') || '').trim(),
          dates: String(data.get('dates') || '').trim(),
          location: String(data.get('location') || '').trim(),
          target_market_title: String(data.get('target_market_title') || '').trim(),
          responsibilities: String(data.get('responsibilities') || '').trim(),
          recruiter_explanation: String(data.get('recruiter_explanation') || '').trim(),
          status: String(data.get('status') || 'needs_review').trim()
        };

        if (!payload.official_title || !payload.employer || !payload.target_market_title) {
          showFeedback('Add the official title, employer, and target-market title before saving.', true);
          return;
        }

        if (submitButton) {
          submitButton.disabled = true;
          submitButton.textContent = 'Saving…';
        }

        try {
          const response = await fetch(`${endpoint}/${encodeURIComponent(roleId)}`, {
            method: 'PUT',
            headers: {
              'Content-Type': 'application/json',
              ...(csrfToken ? {'X-CSRFToken': csrfToken} : {})
            },
            body: JSON.stringify(payload)
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || result.message || 'The employment role could not be saved.');
          await refreshRoles(
            result.message || (payload.status === 'confirmed'
              ? 'Employment role confirmed and ready for reuse in future applications.'
              : 'Baseline Resume role details saved.'),
            `#role-title-${CSS.escape(roleId)}`
          );
        } catch (error) {
          showFeedback(error.message || 'The employment role could not be saved.', true);
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = 'Save';
          }
        }
      });
    });

    panel.querySelectorAll('[data-confirm-career-role]').forEach(button => {
      if (button.dataset.baselineBound === 'true') return;
      button.dataset.baselineBound = 'true';
      button.addEventListener('click', () => {
        const row = button.closest('[data-career-role-row]');
        const form = row?.querySelector('[data-career-role-form]');
        const status = row?.querySelector('select[name="status"]');
        if (!form || !status) return;
        status.value = 'confirmed';
        form.requestSubmit();
      });
    });

    panel.querySelectorAll('[data-delete-career-role]').forEach(button => {
      if (button.dataset.baselineBound === 'true') return;
      button.dataset.baselineBound = 'true';
      button.addEventListener('click', async () => {
        const row = button.closest('[data-career-role-row]');
        const roleId = String(row?.dataset.roleId || '').trim();
        if (!roleId) return;
        const roleLabel = String(button.dataset.roleLabel || '').trim();
        const manualRole = button.dataset.manualRole === 'true';
        const message = manualRole
          ? `Remove this employment role from the Baseline Resume?${roleLabel ? ` “${roleLabel}”` : ''} Future applications will no longer use it.`
          : `Remove this imported title-review record?${roleLabel ? ` “${roleLabel}”` : ''} The Baseline Resume itself will not be changed. Regenerating it may create the review record again.`;
        const confirmed = window.AppUI?.confirm
          ? await window.AppUI.confirm({
              title: manualRole ? 'Remove employment role?' : 'Remove title-review record?',
              message,
              confirmLabel: manualRole ? 'Remove role' : 'Remove review record',
              danger: true
            })
          : window.confirm(message);
        if (!confirmed) return;

        button.disabled = true;
        button.textContent = 'Removing…';
        try {
          const response = await fetch(`${endpoint}/${encodeURIComponent(roleId)}`, {
            method: 'DELETE',
            headers: csrfToken ? {'X-CSRFToken': csrfToken} : {}
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || result.message || 'The employment role could not be removed.');
          await refreshRoles(
            result.baseline_updated
              ? 'Employment role removed from the Baseline Resume.'
              : 'Imported title-review record removed. The Baseline Resume was not changed.',
            '[data-baseline-role-add] > summary'
          );
        } catch (error) {
          button.disabled = false;
          button.textContent = manualRole ? 'Remove from baseline' : 'Remove review record';
          showFeedback(error.message || 'The employment role could not be removed.', true);
        }
      });
    });
  }

  window.CareerBridgeBaselineRoles = {initialize: initializeBaselineRoles};

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeBaselineRoles, {once: true});
  } else {
    initializeBaselineRoles();
  }
})();
