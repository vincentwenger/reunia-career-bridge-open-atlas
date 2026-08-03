(() => {
  'use strict';

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

  function requestHeaders() {
    return {
      'Content-Type': 'application/json',
      ...(csrfToken ? {'X-CSRFToken': csrfToken} : {})
    };
  }

  function showFeedback(element, message, isError = false) {
    if (!element) return;
    element.hidden = false;
    element.textContent = message;
    element.classList.toggle('is-error', isError);
    element.classList.toggle('is-success', !isError);
  }

  function initializeSummary() {
    const panel = document.getElementById('professional-summary');
    const form = panel?.querySelector('[data-baseline-summary-form]');
    if (!panel || !form) return;
    const endpoint = String(panel.dataset.baselineSummaryEndpoint || '').trim();
    const feedback = panel.querySelector('[data-baseline-summary-feedback]');
    const button = form.querySelector('button[type="submit"]');

    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!endpoint) return;
      const summary = String(new FormData(form).get('current_summary') || '').trim();
      if (button) {
        button.disabled = true;
        button.textContent = 'Saving…';
      }
      try {
        const response = await fetch(endpoint, {
          method: 'PUT',
          headers: requestHeaders(),
          body: JSON.stringify({current_summary: summary})
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || result.message || 'The professional summary could not be saved.');
        showFeedback(feedback, result.message || 'Professional summary saved.');
        if (result.baseline_updated) window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        showFeedback(feedback, error.message || 'The professional summary could not be saved.', true);
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = 'Save summary';
        }
      }
    });
  }

  function skillLines(value) {
    const seen = new Set();
    return String(value || '')
      .split(/\r?\n/)
      .map(item => item.trim().replace(/\s+/g, ' '))
      .filter(item => {
        const key = item.toLocaleLowerCase();
        if (!item || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  }

  function initializeSkills() {
    const panel = document.getElementById('skills');
    const form = panel?.querySelector('[data-baseline-skills-form]');
    if (!panel || !form) return;
    const endpoint = String(panel.dataset.baselineSkillsEndpoint || '').trim();
    const feedback = panel.querySelector('[data-baseline-skills-feedback]');
    const button = form.querySelector('button[type="submit"]');

    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!endpoint) return;
      const data = new FormData(form);
      const payload = {
        hard_skills: skillLines(data.get('hard_skills')),
        soft_skills: skillLines(data.get('soft_skills')),
        tools_software: skillLines(data.get('tools_software')),
        industry_knowledge: skillLines(data.get('industry_knowledge')),
        languages: skillLines(data.get('languages'))
      };
      if (button) {
        button.disabled = true;
        button.textContent = 'Saving…';
      }
      try {
        const response = await fetch(endpoint, {
          method: 'PUT',
          headers: requestHeaders(),
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || result.message || 'The skills could not be saved.');
        showFeedback(feedback, result.message || 'Skills saved.');
        if (result.baseline_updated) window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        showFeedback(feedback, error.message || 'The skills could not be saved.', true);
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = 'Save skills';
        }
      }
    });
  }

  function initializeEducation() {
    const panel = document.getElementById('education-credentials');
    if (!panel) return;
    const endpoint = String(panel.dataset.baselineEducationEndpoint || '').replace(/\/$/, '');
    const feedback = panel.querySelector('[data-baseline-education-feedback]');
    if (!endpoint) return;

    const addForm = panel.querySelector('[data-add-baseline-education-form]');
    addForm?.addEventListener('submit', async event => {
      event.preventDefault();
      const button = addForm.querySelector('button[type="submit"]');
      const data = new FormData(addForm);
      const payload = {
        credential: String(data.get('credential') || '').trim(),
        institution: String(data.get('institution') || '').trim(),
        location: String(data.get('location') || '').trim(),
        date: String(data.get('date') || '').trim(),
        detail: String(data.get('detail') || '').trim()
      };
      if (!payload.credential || !payload.institution) {
        showFeedback(feedback, 'Add the credential and institution before saving.', true);
        return;
      }
      if (button) {
        button.disabled = true;
        button.textContent = 'Adding…';
      }
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: requestHeaders(),
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || result.message || 'The education record could not be added.');
        showFeedback(feedback, result.message || 'Education record added.');
        window.setTimeout(() => window.location.reload(), 250);
      } catch (error) {
        showFeedback(feedback, error.message || 'The education record could not be added.', true);
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = 'Add education record';
        }
      }
    });

    panel.querySelectorAll('[data-baseline-education-form]').forEach(form => {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const index = String(form.dataset.educationIndex || '').trim();
        const row = form.closest('[data-baseline-education-row]');
        const button = row?.querySelector(`button[type="submit"][form="${form.id}"]`);
        const data = new FormData(form);
        const payload = {
          credential: String(data.get('credential') || '').trim(),
          institution: String(data.get('institution') || '').trim(),
          location: String(data.get('location') || '').trim(),
          date: String(data.get('date') || '').trim(),
          detail: String(data.get('detail') || '').trim()
        };
        if (!payload.credential || !payload.institution) {
          showFeedback(feedback, 'Add the credential and institution before saving.', true);
          return;
        }
        if (button) {
          button.disabled = true;
          button.textContent = 'Saving…';
        }
        try {
          const response = await fetch(`${endpoint}/${encodeURIComponent(index)}`, {
            method: 'PUT',
            headers: requestHeaders(),
            body: JSON.stringify(payload)
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || result.message || 'The education record could not be saved.');
          showFeedback(feedback, result.message || 'Education record saved.');
          if (result.baseline_updated) window.setTimeout(() => window.location.reload(), 250);
        } catch (error) {
          showFeedback(feedback, error.message || 'The education record could not be saved.', true);
        } finally {
          if (button) {
            button.disabled = false;
            button.textContent = 'Save';
          }
        }
      });
    });

    panel.querySelectorAll('[data-delete-baseline-education]').forEach(button => {
      button.addEventListener('click', async () => {
        const row = button.closest('[data-baseline-education-row]');
        const index = String(row?.dataset.educationIndex || '').trim();
        const label = String(button.dataset.educationLabel || '').trim();
        if (!index) return;
        const confirmed = window.confirm(
          `Remove this education record from the Baseline Resume?${label ? `\n\n“${label}”` : ''}\n\nThe originally imported resume will remain preserved as source evidence.`
        );
        if (!confirmed) return;
        button.disabled = true;
        button.textContent = 'Removing…';
        try {
          const response = await fetch(`${endpoint}/${encodeURIComponent(index)}`, {
            method: 'DELETE',
            headers: csrfToken ? {'X-CSRFToken': csrfToken} : {}
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || result.message || 'The education record could not be removed.');
          showFeedback(feedback, result.message || 'Education record removed.');
          window.setTimeout(() => window.location.reload(), 250);
        } catch (error) {
          button.disabled = false;
          button.textContent = 'Remove from baseline';
          showFeedback(feedback, error.message || 'The education record could not be removed.', true);
        }
      });
    });
  }

  function initialize() {
    initializeSummary();
    initializeSkills();
    initializeEducation();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, {once: true});
  } else {
    initialize();
  }
})();
