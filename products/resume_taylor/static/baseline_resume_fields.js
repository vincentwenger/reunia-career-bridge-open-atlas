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
    element.textContent = String(message || '');
    element.classList.toggle('is-error', isError);
    element.classList.toggle('is-success', !isError);
  }

  async function refreshSections(selectors, {feedbackSelector = '', message = '', isError = false, focusSelector = ''} = {}) {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const activeId = document.activeElement?.id || '';
    const response = await fetch(window.location.href.split('#')[0], {
      method: 'GET',
      credentials: 'same-origin',
      headers: {'Accept': 'text/html', 'X-Requested-With': 'XMLHttpRequest'},
      cache: 'no-store'
    });
    if (!response.ok) throw new Error(`The updated Baseline Resume could not be refreshed (${response.status}).`);

    const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
    selectors.forEach(selector => {
      const current = document.querySelector(selector);
      const updated = parsed.querySelector(selector);
      if (current && updated) current.replaceWith(updated);
      else if (current && !updated) current.remove();
    });

    window.CareerBridgeBaselineFields?.initialize?.();
    window.CareerBridgeBaselineRoles?.initialize?.();

    const feedback = feedbackSelector ? document.querySelector(feedbackSelector) : null;
    if (feedback && message) showFeedback(feedback, message, isError);

    const focusTarget = (focusSelector && document.querySelector(focusSelector))
      || (activeId && document.getElementById(activeId));
    window.requestAnimationFrame(() => {
      window.scrollTo(scrollX, scrollY);
      focusTarget?.focus?.({preventScroll: true});
    });
  }

  window.CareerBridgeBaselineUI = {
    csrfToken,
    requestHeaders,
    showFeedback,
    refreshSections
  };

  function initializeSummary() {
    const panel = document.getElementById('professional-summary');
    const form = panel?.querySelector('[data-baseline-summary-form]');
    if (!panel || !form || form.dataset.baselineBound === 'true') return;
    form.dataset.baselineBound = 'true';
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
        await refreshSections(['#professional-summary', '#baseline-resume-preview-region'], {
          feedbackSelector: '[data-baseline-summary-feedback]',
          message: result.message || 'Professional summary saved.',
          focusSelector: '#baseline-professional-summary'
        });
      } catch (error) {
        showFeedback(feedback, error.message || 'The professional summary could not be saved.', true);
      } finally {
        const currentButton = document.querySelector('#professional-summary [data-baseline-summary-form] button[type="submit"]');
        if (currentButton) {
          currentButton.disabled = false;
          currentButton.textContent = 'Save summary';
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
    if (!panel || !form || form.dataset.baselineBound === 'true') return;
    form.dataset.baselineBound = 'true';
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
        await refreshSections(['#skills', '#baseline-resume-preview-region'], {
          feedbackSelector: '[data-baseline-skills-feedback]',
          message: result.message || 'Skills saved.',
          focusSelector: '#baseline-hard-skills'
        });
      } catch (error) {
        showFeedback(feedback, error.message || 'The skills could not be saved.', true);
      } finally {
        const currentButton = document.querySelector('#skills [data-baseline-skills-form] button[type="submit"]');
        if (currentButton) {
          currentButton.disabled = false;
          currentButton.textContent = 'Save skills';
        }
      }
    });
  }

  function educationPayload(form) {
    const data = new FormData(form);
    return {
      credential: String(data.get('credential') || '').trim(),
      institution: String(data.get('institution') || '').trim(),
      location: String(data.get('location') || '').trim(),
      date: String(data.get('date') || '').trim(),
      detail: String(data.get('detail') || '').trim()
    };
  }

  function initializeEducation() {
    const panel = document.getElementById('education-credentials');
    if (!panel) return;
    const endpoint = String(panel.dataset.baselineEducationEndpoint || '').replace(/\/$/, '');
    const feedback = panel.querySelector('[data-baseline-education-feedback]');
    if (!endpoint) return;

    const addForm = panel.querySelector('[data-add-baseline-education-form]');
    if (addForm && addForm.dataset.baselineBound !== 'true') {
      addForm.dataset.baselineBound = 'true';
      addForm.addEventListener('submit', async event => {
        event.preventDefault();
        const button = addForm.querySelector('button[type="submit"]');
        const payload = educationPayload(addForm);
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
          await refreshSections(['#education-credentials', '#baseline-resume-preview-region'], {
            feedbackSelector: '[data-baseline-education-feedback]',
            message: result.message || 'Education record added.',
            focusSelector: Number.isInteger(result.education_index)
              ? `#education-credential-${result.education_index}`
              : '#new-education-credential'
          });
        } catch (error) {
          showFeedback(feedback, error.message || 'The education record could not be added.', true);
          if (button) {
            button.disabled = false;
            button.textContent = 'Add education record';
          }
        }
      });
    }

    panel.querySelectorAll('[data-baseline-education-form]').forEach(form => {
      if (form.dataset.baselineBound === 'true') return;
      form.dataset.baselineBound = 'true';
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const index = String(form.dataset.educationIndex || '').trim();
        const row = form.closest('[data-baseline-education-row]');
        const button = row?.querySelector(`button[type="submit"][form="${CSS.escape(form.id)}"]`);
        const payload = educationPayload(form);
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
          await refreshSections(['#education-credentials', '#baseline-resume-preview-region'], {
            feedbackSelector: '[data-baseline-education-feedback]',
            message: result.message || 'Education record saved.',
            focusSelector: `#education-credential-${index}`
          });
        } catch (error) {
          showFeedback(feedback, error.message || 'The education record could not be saved.', true);
          if (button) {
            button.disabled = false;
            button.textContent = 'Save';
          }
        }
      });
    });

    panel.querySelectorAll('[data-delete-baseline-education]').forEach(button => {
      if (button.dataset.baselineBound === 'true') return;
      button.dataset.baselineBound = 'true';
      button.addEventListener('click', async () => {
        const row = button.closest('[data-baseline-education-row]');
        const index = String(row?.dataset.educationIndex || '').trim();
        const label = String(button.dataset.educationLabel || '').trim();
        if (!index) return;
        const confirmed = window.AppUI?.confirm
          ? await window.AppUI.confirm({
              title: 'Remove education record?',
              message: `Remove this education record from the Baseline Resume?${label ? ` “${label}”` : ''} The originally imported resume will remain preserved as source evidence.`,
              confirmLabel: 'Remove record',
              danger: true
            })
          : window.confirm(`Remove this education record from the Baseline Resume?${label ? `\n\n“${label}”` : ''}\n\nThe originally imported resume will remain preserved as source evidence.`);
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
          await refreshSections(['#education-credentials', '#baseline-resume-preview-region'], {
            feedbackSelector: '[data-baseline-education-feedback]',
            message: result.message || 'Education record removed.',
            focusSelector: '[data-baseline-education-add] > summary'
          });
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

  window.CareerBridgeBaselineFields = {initialize};

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, {once: true});
  } else {
    initialize();
  }
})();
