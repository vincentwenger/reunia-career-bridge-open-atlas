(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const builderErrorRetry = document.getElementById('application-builder-error-retry');
  builderErrorRetry?.addEventListener('click', () => window.location.reload());

  const modeSelect = document.querySelector('[data-processing-mode]');
  const customModels = document.querySelector('[data-custom-models]');
  const syncCustomModels = () => {
    if (!modeSelect || !customModels) return;
    customModels.hidden = modeSelect.value !== 'custom';
  };
  modeSelect?.addEventListener('change', syncCustomModels);
  syncCustomModels();

  const targetCountryField = document.querySelector('[data-target-country]');
  const usExperienceField = document.querySelector('[data-us-experience-field]');
  const syncUsExperienceVisibility = () => {
    if (!targetCountryField || !usExperienceField) return;
    const country = (targetCountryField.value || '')
      .trim()
      .toLowerCase()
      .replaceAll('.', '');
    const targetsUnitedStates = [
      'united states',
      'united states of america',
      'usa',
      'us',
    ].includes(country);
    usExperienceField.hidden = !targetsUnitedStates;
  };
  targetCountryField?.addEventListener('input', syncUsExperienceVisibility);
  targetCountryField?.addEventListener('change', syncUsExperienceVisibility);
  syncUsExperienceVisibility();

  document.querySelectorAll('[data-enable-when-filled]').forEach((button) => {
    const fieldIds = (button.dataset.enableWhenFilled || '')
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean);
    const fields = fieldIds
      .map((id) => document.getElementById(id))
      .filter(Boolean);
    const permanentlyDisabled = button.dataset.permanentlyDisabled === 'true';
    const hasValue = (field) => {
      if (field.type === 'file') return Boolean(field.files?.length);
      return Boolean((field.value || '').trim());
    };
    const syncEnabledState = () => {
      button.disabled = permanentlyDisabled || !fields.some(hasValue);
    };
    fields.forEach((field) => {
      field.addEventListener('input', syncEnabledState);
      field.addEventListener('change', syncEnabledState);
    });
    syncEnabledState();
  });

  document.querySelectorAll('[data-confirm-reopen]').forEach((button) => {
    button.addEventListener('click', async (event) => {
      if (button.dataset.confirmed === 'true') {
        delete button.dataset.confirmed;
        return;
      }
      event.preventDefault();
      const message = button.dataset.confirmReopen || 'Reopen this completed workflow step?';
      const confirmed = window.AppUI?.confirm
        ? await window.AppUI.confirm({
          title: 'Reopen workflow step?',
          message,
          confirmLabel: 'Reopen step',
        })
        : window.confirm(message);
      if (!confirmed) return;
      button.dataset.confirmed = 'true';
      button.click();
    });
  });

  document.querySelectorAll('form[data-confirm-submit]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      if (form.dataset.confirmed === 'true') {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      const message = form.dataset.confirmSubmit || 'Continue with this action?';
      const confirmed = window.AppUI?.confirm
        ? await window.AppUI.confirm({
          title: 'Confirm action',
          message,
          confirmLabel: 'Continue',
        })
        : window.confirm(message);
      if (!confirmed) return;
      form.dataset.confirmed = 'true';
      form.requestSubmit(event.submitter || undefined);
    });
  });

  const applicationMenus = [...document.querySelectorAll('[data-application-menu]')];
  const menuItems = (menu) => [...menu.querySelectorAll('[role="menuitem"]')]
    .filter((item) => !item.hasAttribute('disabled') && item.getAttribute('aria-disabled') !== 'true');
  const closeApplicationMenus = (except = null) => {
    applicationMenus.forEach((menu) => {
      if (menu !== except) menu.open = false;
    });
  };
  applicationMenus.forEach((menu) => {
    const summary = menu.querySelector('summary');
    menu.addEventListener('toggle', () => {
      summary?.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
      if (menu.open) closeApplicationMenus(menu);
    });
    summary?.setAttribute('aria-expanded', menu.open ? 'true' : 'false');
    summary?.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      menu.open = true;
      const items = menuItems(menu);
      (event.key === 'ArrowUp' ? items.at(-1) : items[0])?.focus();
    });
    menu.addEventListener('keydown', (event) => {
      const items = menuItems(menu);
      const index = items.indexOf(document.activeElement);
      if (event.key === 'Escape') {
        event.preventDefault();
        menu.open = false;
        summary?.focus();
        return;
      }
      if (!items.length || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'Home') nextIndex = 0;
      else if (event.key === 'End') nextIndex = items.length - 1;
      else if (event.key === 'ArrowDown') nextIndex = index < 0 ? 0 : (index + 1) % items.length;
      else nextIndex = index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length;
      items[nextIndex]?.focus();
    });
  });
  document.addEventListener('click', (event) => {
    const clickedMenu = event.target instanceof Element
      ? event.target.closest('[data-application-menu]')
      : null;
    closeApplicationMenus(clickedMenu);
  });

  document.querySelectorAll('form[data-auto-submit-radio]').forEach((form) => {
    const fieldName = form.dataset.autoSubmitRadio;
    if (!fieldName) return;

    let submitting = false;
    form.querySelectorAll(`input[type="radio"][name="${CSS.escape(fieldName)}"]`).forEach((radio) => {
      radio.addEventListener('change', () => {
        if (!radio.checked || submitting) return;
        submitting = true;
        form.setAttribute('aria-busy', 'true');
        form.querySelectorAll('.resume-style-card').forEach((card) => {
          const cardRadio = card.querySelector(`input[type="radio"][name="${CSS.escape(fieldName)}"]`);
          card.classList.toggle('selected', cardRadio === radio);
        });
        form.requestSubmit();
      });
    });
  });

  document.querySelectorAll('[data-tabs]').forEach((tabs) => {
    const buttons = [...tabs.querySelectorAll('[data-tab-target]')]
      .filter((item) => item.closest('[data-tabs]') === tabs);
    const panels = [...tabs.querySelectorAll('[data-tab-panel]')]
      .filter((item) => item.closest('[data-tabs]') === tabs);
    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        buttons.forEach((item) => item.classList.toggle('active', item === button));
        panels.forEach((panel) => panel.classList.toggle('active', panel.id === button.dataset.tabTarget));
      });
    });
  });

  // Open collapsed panels when a cross-workspace link targets content inside them.
  const revealHashTarget = () => {
    if (!window.location.hash) return;
    const targetId = decodeURIComponent(window.location.hash.slice(1));
    const target = targetId ? document.getElementById(targetId) : null;
    if (!target) return;
    let parentDetails = target.closest('details');
    while (parentDetails) {
      parentDetails.open = true;
      parentDetails = parentDetails.parentElement?.closest('details') || null;
    }
  };

  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', () => {
      const targetId = decodeURIComponent((link.getAttribute('href') || '').slice(1));
      const target = targetId ? document.getElementById(targetId) : null;
      let parentDetails = target?.closest('details') || null;
      while (parentDetails) {
        parentDetails.open = true;
        parentDetails = parentDetails.parentElement?.closest('details') || null;
      }
    });
  });
  window.addEventListener('hashchange', revealHashTarget);
  revealHashTarget();

  const overlay = document.getElementById('loading-overlay');
  const loadingCard = overlay?.querySelector('.loading-card');
  const loadingSpinner = document.getElementById('loading-spinner');
  const loadingTitle = document.getElementById('loading-title');
  const loadingMessage = document.getElementById('loading-message');
  const loadingProgress = document.getElementById('loading-progress');
  const loadingProgressSteps = document.getElementById('loading-progress-steps');
  const loadingElapsed = document.getElementById('loading-elapsed');
  const loadingReassurance = document.getElementById('loading-reassurance');
  let loadingTimers = [];
  let loadingElapsedTimer = null;

  const clearLoadingTimers = () => {
    loadingTimers.forEach((timer) => window.clearTimeout(timer));
    loadingTimers = [];
    if (loadingElapsedTimer !== null) {
      window.clearInterval(loadingElapsedTimer);
      loadingElapsedTimer = null;
    }
  };

  const splitLoadingData = (value) => (value || '')
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean);

  const formatElapsed = (elapsedSeconds) => {
    if (elapsedSeconds < 60) return `${elapsedSeconds}s elapsed`;
    const minutes = Math.floor(elapsedSeconds / 60);
    const seconds = elapsedSeconds % 60;
    return `${minutes}m ${String(seconds).padStart(2, '0')}s elapsed`;
  };

  const loadingData = (primarySource, fallbackSource, key) => (
    primarySource?.dataset?.[key] || fallbackSource?.dataset?.[key] || ''
  );

  const startLoadingProgress = (primarySource, fallbackSource) => {
    clearLoadingTimers();
    const steps = splitLoadingData(loadingData(primarySource, fallbackSource, 'loadingSteps'));
    const details = splitLoadingData(loadingData(primarySource, fallbackSource, 'loadingStepDetails'));
    const timings = loadingData(primarySource, fallbackSource, 'loadingStepTimings')
      .split(',')
      .map((value) => Number.parseInt(value.trim(), 10))
      .filter((value) => Number.isFinite(value) && value >= 0);
    const initialReassurance = loadingData(
      primarySource,
      fallbackSource,
      'loadingReassurance',
    ) || 'Some stages can take longer when several checks are required.';
    const delayedReassurance = loadingData(
      primarySource,
      fallbackSource,
      'loadingReassuranceDelayed',
    ) || 'Still working. The app is carefully checking the updated resume.';
    const extendedReassurance = loadingData(
      primarySource,
      fallbackSource,
      'loadingReassuranceExtended',
    ) || 'Still working. Longer resumes can require additional review before the next step is ready.';

    if (!steps.length || !loadingProgress || !loadingProgressSteps) {
      if (loadingProgress) loadingProgress.hidden = true;
      return;
    }

    loadingProgressSteps.replaceChildren();
    const stepElements = steps.map((label, index) => {
      const item = document.createElement('li');
      item.className = 'loading-progress-step';

      const marker = document.createElement('span');
      marker.className = 'loading-progress-marker';
      marker.setAttribute('aria-hidden', 'true');
      marker.textContent = String(index + 1);

      const copy = document.createElement('span');
      copy.className = 'loading-progress-copy';
      const title = document.createElement('strong');
      title.textContent = label;
      copy.append(title);
      if (details[index]) {
        const detail = document.createElement('small');
        detail.textContent = details[index];
        copy.append(detail);
      }

      item.append(marker, copy);
      loadingProgressSteps.append(item);
      return item;
    });

    const activateStep = (activeIndex) => {
      stepElements.forEach((item, index) => {
        item.classList.toggle('is-complete', index < activeIndex);
        item.classList.toggle('is-active', index === activeIndex);
        item.classList.toggle('is-upcoming', index > activeIndex);
        if (index === activeIndex) item.setAttribute('aria-current', 'step');
        else item.removeAttribute('aria-current');
        const marker = item.querySelector('.loading-progress-marker');
        if (marker) marker.textContent = index < activeIndex ? '✓' : String(index + 1);
      });
    };

    activateStep(0);
    loadingProgress.hidden = false;
    if (loadingReassurance) {
      loadingReassurance.textContent = initialReassurance;
    }

    steps.slice(1).forEach((_, offset) => {
      const defaultTiming = (offset + 1) * 7000;
      const timing = timings[offset] ?? defaultTiming;
      loadingTimers.push(window.setTimeout(() => activateStep(offset + 1), timing));
    });

    const startedAt = Date.now();
    const updateElapsed = () => {
      const elapsedSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
      if (loadingElapsed) loadingElapsed.textContent = formatElapsed(elapsedSeconds);
      if (!loadingReassurance) return;
      if (elapsedSeconds >= 45) {
        loadingReassurance.textContent = extendedReassurance;
      } else if (elapsedSeconds >= 20) {
        loadingReassurance.textContent = delayedReassurance;
      }
    };
    updateElapsed();
    loadingElapsedTimer = window.setInterval(updateElapsed, 1000);
  };

  const resetLoadingOverlay = () => {
    clearLoadingTimers();
    loadingCard?.classList.remove('status-success', 'status-warning', 'status-error');
    if (loadingSpinner) loadingSpinner.hidden = false;
    if (loadingProgress) loadingProgress.hidden = true;
    if (loadingProgressSteps) loadingProgressSteps.replaceChildren();
    if (loadingElapsed) loadingElapsed.textContent = '';
    if (loadingReassurance) loadingReassurance.textContent = '';
  };

  document.querySelectorAll('form.loading-form').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (event.defaultPrevented || !form.checkValidity()) return;
      const submitter = event.submitter;
      if (submitter?.disabled || submitter?.dataset.skipLoading === 'true') return;
      resetLoadingOverlay();
      if (loadingTitle) loadingTitle.textContent = submitter?.dataset.loadingTitle || form.dataset.loadingTitle || 'Processing…';
      if (loadingMessage) loadingMessage.textContent = submitter?.dataset.loadingMessage || form.dataset.loadingMessage || 'The requested operation is running.';
      startLoadingProgress(submitter, form);
      if (overlay) overlay.hidden = false;
      loadingCard?.focus({ preventScroll: true });
    });
  });
})();
