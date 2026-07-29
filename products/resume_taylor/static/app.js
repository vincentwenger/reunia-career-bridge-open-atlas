(() => {
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
    button.addEventListener('click', (event) => {
      const message = button.dataset.confirmReopen || 'Reopen this completed workflow step?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  document.querySelectorAll('form[data-confirm-submit]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const message = form.dataset.confirmSubmit || 'Continue with this action?';
      if (!window.confirm(message)) event.preventDefault();
    });
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

  document.querySelectorAll('.question-card').forEach((card) => {
    const type = card.dataset.questionType;
    if (!['yes_no', 'yes_no_with_details'].includes(type)) return;
    const detail = card.querySelector('.conditional-details');
    const placement = card.querySelector('[data-confirmation-placement]');
    const experience = card.querySelector('[data-confirmation-experience]');
    const placementChoice = card.querySelector('[data-confirmation-placement-choice]');
    const radios = [...card.querySelectorAll('input[type="radio"]')];
    const sync = () => {
      const selected = radios.find((item) => item.checked);
      const show = selected?.value === 'yes';
      if (detail) detail.hidden = !show;
      if (placement) placement.hidden = !show;
      const field = detail?.querySelector('textarea, input');
      if (field) {
        field.required = show;
        field.disabled = !show;
      }
      if (experience) {
        experience.required = show;
        experience.disabled = !show;
      }
      if (placementChoice) placementChoice.disabled = !show;
    };
    radios.forEach((radio) => radio.addEventListener('change', sync));
    sync();
  });

  document.querySelectorAll('[data-confirmation-form]').forEach((form) => {
    const status = form.querySelector('[data-confirmation-bulk-status]');

    const setTextQuestionNoExperience = (card, markedNo) => {
      const choiceField = card.querySelector('[data-text-question-choice]');
      const answerArea = card.querySelector('[data-text-answer-for]');
      const noExperienceArea = card.querySelector('[data-no-experience-answer]');
      const answerField = answerArea?.querySelector('textarea, input:not([type="hidden"])');
      const placement = card.querySelector('[data-confirmation-placement]');
      const experience = card.querySelector('[data-confirmation-experience]');
      const placementChoice = card.querySelector('[data-confirmation-placement-choice]');
      if (!choiceField || !answerArea || !noExperienceArea || !answerField) return false;

      choiceField.value = markedNo ? 'no' : '';
      answerArea.hidden = markedNo;
      noExperienceArea.hidden = !markedNo;
      answerField.disabled = markedNo;
      answerField.required = !markedNo && answerField.dataset.requiredAnswer === 'true';
      if (placement) placement.hidden = markedNo;
      if (experience) {
        experience.required = !markedNo;
        experience.disabled = markedNo;
      }
      if (placementChoice) placementChoice.disabled = markedNo;
      card.classList.toggle('question-card-no-experience', markedNo);
      if (!markedNo) answerField.focus();
      return true;
    };

    form.querySelectorAll('.question-card').forEach((card) => {
      const choiceField = card.querySelector('[data-text-question-choice]');
      if (choiceField) setTextQuestionNoExperience(card, choiceField.value === 'no');

      card.querySelector('[data-mark-question-no-experience]')?.addEventListener('click', () => {
        setTextQuestionNoExperience(card, true);
        if (status) status.textContent = 'Marked this question as having no relevant experience.';
      });
      card.querySelector('[data-answer-question-instead]')?.addEventListener('click', () => {
        setTextQuestionNoExperience(card, false);
        if (status) status.textContent = 'Reopened this question so you can provide an answer.';
      });
    });

    form.querySelector('[data-confirmation-bulk="no"]')?.addEventListener('click', () => {
      let updated = 0;
      form.querySelectorAll('.question-card').forEach((card) => {
        const noRadio = card.querySelector('input[type="radio"][value="no"]');
        if (noRadio) {
          noRadio.checked = true;
          noRadio.dispatchEvent(new Event('change', { bubbles: true }));
          updated += 1;
          return;
        }
        if (setTextQuestionNoExperience(card, true)) updated += 1;
      });
      if (!status) return;
      status.textContent = updated
        ? `Marked all ${updated} question${updated === 1 ? '' : 's'} as having no relevant experience. You can reopen any individual question.`
        : 'There are no unanswered experience questions in this list.';
    });
  });

  const escapeHtml = (value) => value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const tokenize = (value) => value.match(/\s+|[\wÀ-ž]+(?:['’/-][\wÀ-ž]+)*|[^\s\wÀ-ž]/gu) || [];

  function wordDiff(original, proposed) {
    const left = tokenize(original);
    const right = tokenize(proposed);
    const rows = left.length + 1;
    const cols = right.length + 1;
    const matrix = Array.from({ length: rows }, () => new Uint16Array(cols));
    for (let i = left.length - 1; i >= 0; i -= 1) {
      for (let j = right.length - 1; j >= 0; j -= 1) {
        matrix[i][j] = left[i] === right[j]
          ? matrix[i + 1][j + 1] + 1
          : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
      }
    }
    const leftParts = [];
    const rightParts = [];
    let i = 0;
    let j = 0;
    while (i < left.length || j < right.length) {
      if (i < left.length && j < right.length && left[i] === right[j]) {
        const safe = escapeHtml(left[i]);
        leftParts.push(safe);
        rightParts.push(safe);
        i += 1;
        j += 1;
      } else if (j < right.length && (i === left.length || matrix[i][j + 1] >= matrix[i + 1][j])) {
        rightParts.push(`<span class="diff-added">${escapeHtml(right[j])}</span>`);
        j += 1;
      } else if (i < left.length) {
        leftParts.push(`<span class="diff-removed">${escapeHtml(left[i])}</span>`);
        i += 1;
      }
    }
    return [leftParts.join(''), rightParts.join('')];
  }

  document.querySelectorAll('[data-diff-container]').forEach((container) => {
    const textarea = container.parentElement.querySelector('[data-diff-editor]');
    const referenceOutput = container.querySelector('[data-reference-diff]');
    const proposedOutput = container.querySelector('[data-proposed-diff]');
    let timer;
    const update = () => {
      const [left, right] = wordDiff(container.dataset.reference || '', textarea.value || '');
      referenceOutput.innerHTML = left;
      proposedOutput.innerHTML = right;
    };
    textarea?.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(update, 120);
    });
  });

  document.querySelectorAll('.resume-editor-form').forEach((form) => {
    const section = form.closest('.card') || form.parentElement;
    const saveButton = form.querySelector('[data-resume-save-button]');
    const saveStatus = form.querySelector('[data-resume-save-status]');
    const downloadLink = section?.querySelector('[data-resume-download]');
    const downloadStatus = section?.querySelector('[data-download-status]');
    const savedDownloadStatus = downloadStatus?.dataset.savedStatus || downloadStatus?.textContent || '';

    const snapshot = () => JSON.stringify(
      [...form.elements]
        .filter((field) => field.name && field.type !== 'hidden' && field.type !== 'submit' && field.type !== 'button')
        .map((field) => [
          field.name,
          field.type === 'checkbox' || field.type === 'radio' ? Boolean(field.checked) : field.value,
        ]),
    );

    const savedSnapshot = snapshot();
    const syncDirtyState = () => {
      const dirty = snapshot() !== savedSnapshot;
      form.dataset.dirty = dirty ? 'true' : 'false';
      if (saveButton) saveButton.disabled = !dirty;
      if (saveStatus) {
        saveStatus.textContent = dirty
          ? 'Unsaved changes detected. Save them before downloading this resume version.'
          : 'This resume version is already saved. Edit a field to enable saving.';
        saveStatus.classList.toggle('unsaved-status', dirty);
      }
      if (downloadLink) {
        downloadLink.classList.toggle('disabled-link', dirty);
        downloadLink.setAttribute('aria-disabled', dirty ? 'true' : 'false');
        downloadLink.tabIndex = dirty ? -1 : 0;
      }
      if (downloadStatus) {
        downloadStatus.textContent = dirty
          ? 'Unsaved edits are not included yet. Save before downloading.'
          : savedDownloadStatus;
        downloadStatus.classList.toggle('unsaved-status', dirty);
      }
    };

    form.addEventListener('input', syncDirtyState);
    form.addEventListener('change', syncDirtyState);
    downloadLink?.addEventListener('click', (event) => {
      if (form.dataset.dirty === 'true') {
        event.preventDefault();
        saveButton?.focus();
      }
    });
    syncDirtyState();
  });

  document.querySelectorAll('[data-show-mutually-excluded]').forEach((toggle) => {
    const section = toggle.closest('.resume-version-card') || document;
    const bullets = [...section.querySelectorAll('[data-mutually-excluded-bullet]')];
    const syncVisibility = () => {
      bullets.forEach((bullet) => {
        bullet.hidden = !toggle.checked;
      });
    };
    toggle.addEventListener('change', syncVisibility);
    syncVisibility();
  });

  document.querySelectorAll('[data-restore-bullet]').forEach((button) => {
    button.addEventListener('click', () => {
      const targetId = button.dataset.target;
      const checkbox = targetId ? document.getElementById(targetId) : null;
      const editor = button.closest('[data-bullet-editor]');
      const textarea = editor?.querySelector('[data-diff-editor]');
      const status = editor?.querySelector('[data-restore-status]');
      if (!checkbox) return;

      checkbox.checked = true;
      checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      textarea?.dispatchEvent(new Event('input', { bubbles: true }));
      button.disabled = true;
      button.textContent = 'Restored — save changes';
      if (status) {
        status.textContent = 'This bullet will return after you save the Job-Aligned Resume.';
        status.classList.add('unsaved-status');
      }
    });
  });


  const automaticReports = [...document.querySelectorAll('[data-auto-report]')];
  if (automaticReports.length) {
    (async () => {
      let shouldRefresh = false;
      for (const item of automaticReports) {
        item.textContent = `${item.dataset.label || 'Resume Report'} · generating…`;
        const body = new FormData();
        body.append('csrf_token', item.dataset.csrf || '');
        try {
          const response = await fetch(item.dataset.url, {
            method: 'POST',
            body,
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.message || 'Report generation failed.');
          item.textContent = `${result.label || item.dataset.label} · ${Number(result.score).toFixed(1)}%`;
          item.classList.add('is-ready');
          shouldRefresh ||= item.dataset.refresh === 'true';
        } catch (error) {
          item.textContent = `${item.dataset.label || 'Resume Report'} · retry available in Resume Reports`;
          item.classList.add('is-error');
        }
      }
      if (shouldRefresh) window.location.reload();
    })();
  }

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
      if (submitter?.disabled) return;
      resetLoadingOverlay();
      if (loadingTitle) loadingTitle.textContent = submitter?.dataset.loadingTitle || form.dataset.loadingTitle || 'Processing…';
      if (loadingMessage) loadingMessage.textContent = submitter?.dataset.loadingMessage || form.dataset.loadingMessage || 'The requested operation is running.';
      startLoadingProgress(submitter, form);
      if (overlay) overlay.hidden = false;
    });
  });
})();

// Keep change explanations discreet: opening one closes its nearby peers.
document.querySelectorAll('[data-change-explanation]').forEach((details) => {
  details.addEventListener('toggle', () => {
    if (!details.open) return;
    const scope = details.closest('.resume-paper-section, .resume-experience, .skill-comparison-grid') || document;
    scope.querySelectorAll('[data-change-explanation][open]').forEach((other) => {
      if (other !== details) other.open = false;
    });
  });
});

// Reveal compact verification details when an in-page action targets content inside them.
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
