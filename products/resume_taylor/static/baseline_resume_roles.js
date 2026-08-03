(() => {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  function initializeBaselineRoles() {
    const panel = document.getElementById('employment-roles');
    if (!panel) return;

    const endpoint = String(panel.dataset.careerRolesEndpoint || '').replace(/\/$/, '');
    if (!endpoint) return;

    const feedback = panel.querySelector('[data-baseline-role-feedback]');

    function showFeedback(message, isError = false) {
      if (!feedback) return;
      feedback.hidden = false;
      feedback.textContent = message;
      feedback.classList.toggle('is-error', isError);
      feedback.classList.toggle('is-success', !isError);
    }

    function updateEmptyState() {
      const remaining = panel.querySelectorAll('[data-career-role-row]').length;
      const count = panel.querySelector('.employment-role-count');
      if (count) count.textContent = `${remaining} role${remaining === 1 ? '' : 's'}`;
      if (remaining !== 0) return;

      const tableWrapper = panel.querySelector('.employment-role-table-wrapper');
      if (!tableWrapper) return;
      const emptyState = document.createElement('div');
      emptyState.className = 'reusable-evidence-empty employment-role-empty';
      const strong = document.createElement('strong');
      strong.textContent = 'No employment roles have been extracted yet.';
      const paragraph = document.createElement('p');
      paragraph.textContent = 'Import or regenerate the Baseline Resume. Its documented job titles, employers, dates, locations, and responsibilities will appear here for review.';
      emptyState.append(strong, paragraph);
      tableWrapper.replaceWith(emptyState);
    }

    const addForm = panel.querySelector('[data-add-career-role-form]');
    addForm?.addEventListener('submit', async event => {
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
        showFeedback(result.message || 'Employment role added.');
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        showFeedback(error.message || 'The employment role could not be added.', true);
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = 'Add employment role';
        }
      }
    });

    panel.querySelectorAll('[data-career-role-form]').forEach(form => {
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

          const updatedAt = row?.querySelector('[data-career-role-updated]');
          if (updatedAt) updatedAt.textContent = `Updated ${result.career_role?.updated_at || 'just now'}`;
          let confirmedAt = row?.querySelector('[data-career-role-confirmed]');
          if (payload.status === 'confirmed') {
            if (!confirmedAt) {
              confirmedAt = document.createElement('small');
              confirmedAt.dataset.careerRoleConfirmed = '';
              updatedAt?.after(confirmedAt);
            }
            confirmedAt.textContent = `Confirmed ${result.career_role?.confirmed_at || 'just now'}`;
          } else {
            confirmedAt?.remove();
          }
          showFeedback(result.message || (payload.status === 'confirmed'
            ? 'Employment role confirmed and ready for reuse in future applications.'
            : 'Baseline Resume role details saved.'));
          if (result.baseline_updated) {
            window.setTimeout(() => window.location.reload(), 250);
          }
        } catch (error) {
          showFeedback(error.message || 'The employment role could not be saved.', true);
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = 'Save';
          }
        }
      });
    });

    panel.querySelectorAll('[data-confirm-career-role]').forEach(button => {
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
      button.addEventListener('click', async () => {
        const row = button.closest('[data-career-role-row]');
        const roleId = String(row?.dataset.roleId || '').trim();
        if (!roleId) return;
        const roleLabel = String(button.dataset.roleLabel || '').trim();
        const manualRole = button.dataset.manualRole === 'true';
        const confirmed = window.confirm(
          manualRole
            ? `Remove this employment role from the Baseline Resume?${roleLabel ? `\n\n“${roleLabel}”` : ''}\n\nFuture applications will no longer use it.`
            : `Remove this imported title-review record?${roleLabel ? `\n\n“${roleLabel}”` : ''}\n\nThe Baseline Resume itself will not be changed. Regenerating it may create the review record again.`
        );
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
          if (result.baseline_updated) {
            showFeedback('Employment role removed from the Baseline Resume.');
            window.setTimeout(() => window.location.reload(), 250);
          } else {
            row?.remove();
            updateEmptyState();
            showFeedback('Imported title-review record removed. The Baseline Resume was not changed.');
          }
        } catch (error) {
          button.disabled = false;
          button.textContent = manualRole ? 'Remove from baseline' : 'Remove review record';
          showFeedback(error.message || 'The employment role could not be removed.', true);
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeBaselineRoles, {once: true});
  } else {
    initializeBaselineRoles();
  }
})();
