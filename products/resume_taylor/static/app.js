(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
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


  const builderErrorState = document.getElementById('application-builder-error-state');
  const builderErrorRetry = document.getElementById('application-builder-error-retry');
  const showBuilderError = (message) => {
    if (!builderErrorState) return;
    window.AppUI?.showWorkspaceState(builderErrorState, {
      state: 'error',
      title: 'This workspace could not finish the requested update',
      message: message || 'The current application data remains unchanged. Reload the workspace or retry the available action.'
    });
    builderErrorState.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };
  builderErrorRetry?.addEventListener('click', () => window.location.reload());

  const automaticReports = [...document.querySelectorAll('[data-auto-report]')];
  if (automaticReports.length) {
    (async () => {
      let shouldRefresh = false;
      let failedReportCount = 0;
      for (const item of automaticReports) {
        item.textContent = `${item.dataset.label || 'Resume Report'} · generating…`;
        const body = new FormData();
        try {
          const response = await fetch(item.dataset.url, {
            method: 'POST',
            body,
            credentials: 'same-origin',
            headers: {
              Accept: 'application/json',
              ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
            },
          });
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.message || 'Report generation failed.');
          item.textContent = `${result.label || item.dataset.label} · ${Number(result.score).toFixed(1)}%`;
          item.classList.add('is-ready');
          shouldRefresh ||= item.dataset.refresh === 'true';
        } catch (error) {
          failedReportCount += 1;
          item.textContent = `${item.dataset.label || 'Resume Report'} · retry available in Resume Reports`;
          item.classList.add('is-error');
        }
      }
      if (failedReportCount) {
        showBuilderError(`${failedReportCount} automatic resume report${failedReportCount === 1 ? '' : 's'} could not be generated. Open Resume Reports to retry without losing the current workflow.`);
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

// Load evidence-heavy Job Discovery analysis only when the user asks for it.
// Result cards arrive after the initial page render, so this initializer can be
// called for both server-rendered fallback content and injected JSON fragments.
const initializeDiscoveryAnalysis = (root = document) => {
  root.querySelectorAll('[data-discovery-analysis-url]').forEach((details) => {
    if (details.dataset.analysisBound === 'true') return;
    details.dataset.analysisBound = 'true';
    details.addEventListener('toggle', async () => {
      if (!details.open || details.dataset.analysisLoaded === 'true' || details.dataset.analysisLoading === 'true') return;
      const target = details.querySelector('[data-discovery-analysis-content]');
      const url = details.dataset.discoveryAnalysisUrl;
      if (!target || !url) return;

      details.dataset.analysisLoading = 'true';
      target.innerHTML = '<p class="discovery-analysis-loading">Loading evidence-grounded analysis…</p>';
      try {
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'same-origin',
          headers: { Accept: 'text/html' },
        });
        if (!response.ok) throw new Error(`Analysis request failed (${response.status}).`);
        target.innerHTML = await response.text();
        details.dataset.analysisLoaded = 'true';
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Analysis could not be loaded.';
        target.textContent = message;
        target.classList.add('discovery-analysis-error');
      } finally {
        details.dataset.analysisLoading = 'false';
      }
    });
  });
};
initializeDiscoveryAnalysis();

// Materialize the Job Discovery result read model outside the initial GET.
// Multiple page activities can invalidate it (catalog hydration, assessment,
// save/ignore, or profile preferences), so concurrent callers share one bounded
// prebuild request and reload only after the durable index is ready.
let discoveryResultIndexPrebuildPromise = null;
const prebuildDiscoveryResultIndex = () => {
  if (discoveryResultIndexPrebuildPromise) return discoveryResultIndexPrebuildPromise;
  const results = document.querySelector('[data-discovery-result-index-url]');
  const endpoint = results?.dataset.discoveryResultIndexUrl || '';
  if (!results || !endpoint) return Promise.resolve(false);

  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const payload = {
    min_fit: results.dataset.discoveryMinimumFit || '60',
    confidence: results.dataset.discoveryConfidence || 'high,medium',
    recommendation: results.dataset.discoveryRecommendation || 'all_viable',
    sort: results.dataset.discoverySort || 'recommended',
  };

  discoveryResultIndexPrebuildPromise = (async () => {
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        body: JSON.stringify(payload),
      });
      let result = {};
      try {
        result = await response.json();
      } catch (_error) {
        result = {};
      }
      return response.ok && result.ok !== false;
    } catch (_error) {
      return false;
    } finally {
      discoveryResultIndexPrebuildPromise = null;
    }
  })();
  return discoveryResultIndexPrebuildPromise;
};

// Load only the compact result fragment after the surrounding Job Discovery
// page has rendered. This keeps profile/index reads and card markup out of the
// initial HTML response while preserving normal links as a no-JavaScript fallback.
let discoveryResultsRequestController = null;
let discoveryStaleIndexRefreshPromise = null;

const showDiscoveryResultsSkeleton = (results) => {
  const target = results?.querySelector('[data-discovery-results-content]');
  const template = results?.querySelector('[data-discovery-results-skeleton-template]');
  if (!target) return;
  target.setAttribute('aria-busy', 'true');
  if (template instanceof HTMLTemplateElement) {
    target.replaceChildren(template.content.cloneNode(true));
  }
};

const updateDiscoveryAssessmentControls = (results, payload) => {
  const form = results?.querySelector('[data-discovery-assessment-run]');
  if (!form) return;
  const pendingCount = Math.max(0, Number(payload?.summary?.pending_count || 0));
  form.dataset.pendingCount = String(pendingCount);
  form.querySelectorAll('[data-discovery-assessment-submit]').forEach((button) => {
    button.disabled = pendingCount === 0;
    if (button.dataset.assessmentScope === 'all') {
      button.textContent = pendingCount > 0
        ? `Assess all remaining (${pendingCount})`
        : 'Assess all remaining';
    }
  });
  const meter = results.querySelector('[data-discovery-assessment-meter]');
  if (meter) meter.max = Math.max(1, pendingCount);
};

const discoveryPageUrlFromForm = (form) => {
  const url = new URL(form.action || window.location.href, window.location.href);
  url.search = new URLSearchParams(new FormData(form)).toString();
  url.searchParams.delete('render_results');
  url.hash = 'job-discovery-results';
  return url;
};

const loadDiscoveryResults = async (
  pageUrl = window.location.href,
  { updateHistory = false, showSkeleton = true, allowPrebuild = true } = {},
) => {
  const results = document.querySelector('[data-discovery-results-url]');
  const target = results?.querySelector('[data-discovery-results-content]');
  const endpoint = results?.dataset.discoveryResultsUrl || '';
  if (!results || !target || !endpoint) return false;

  const requestedPageUrl = new URL(pageUrl, window.location.href);
  requestedPageUrl.searchParams.delete('render_results');
  const requestUrl = new URL(endpoint, window.location.href);
  requestUrl.search = requestedPageUrl.search;

  results.dataset.discoveryMinimumFit = requestedPageUrl.searchParams.get('min_fit') || '60';
  results.dataset.discoveryConfidence = requestedPageUrl.searchParams.get('confidence') || 'high,medium';
  results.dataset.discoveryRecommendation = requestedPageUrl.searchParams.get('recommendation') || 'all_viable';
  results.dataset.discoverySort = requestedPageUrl.searchParams.get('sort') || 'recommended';

  discoveryResultsRequestController?.abort();
  discoveryResultsRequestController = new AbortController();
  if (showSkeleton) showDiscoveryResultsSkeleton(results);

  try {
    const response = await fetch(requestUrl, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: discoveryResultsRequestController.signal,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {};
    }
    if (!response.ok || payload.ok === false || typeof payload.html !== 'string') {
      throw new Error(`Job results request failed (${response.status}).`);
    }

    target.innerHTML = payload.html;
    target.setAttribute('aria-busy', 'false');
    results.dataset.discoveryResultsLoaded = 'true';
    results.dataset.discoveryResultIndexStale = payload.index_stale === true ? 'true' : 'false';
    updateDiscoveryAssessmentControls(results, payload);
    initializeDiscoveryAnalysis(target);

    if (updateHistory && typeof payload.page_url === 'string' && payload.page_url) {
      window.history.pushState({ discoveryResults: true }, '', payload.page_url);
    }

    if (payload.index_stale === true && allowPrebuild && !discoveryStaleIndexRefreshPromise) {
      const refresh = async () => {
        if (await prebuildDiscoveryResultIndex()) {
          await loadDiscoveryResults(window.location.href, {
            updateHistory: false,
            showSkeleton: false,
            allowPrebuild: false,
          });
        }
      };
      discoveryStaleIndexRefreshPromise = new Promise((resolve) => {
        const run = () => refresh().finally(resolve);
        if ('requestIdleCallback' in window) {
          window.requestIdleCallback(run, { timeout: 500 });
        } else {
          window.setTimeout(run, 0);
        }
      }).finally(() => {
        discoveryStaleIndexRefreshPromise = null;
      });
    }
    return true;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return false;
    target.setAttribute('aria-busy', 'false');
    target.innerHTML = '';
    const errorState = document.createElement('div');
    errorState.className = 'empty-state discovery-results-load-error';
    const heading = document.createElement('h3');
    heading.textContent = 'Job results could not be loaded';
    const message = document.createElement('p');
    message.textContent = error instanceof Error
      ? error.message
      : 'The result request did not complete.';
    const retry = document.createElement('button');
    retry.className = 'button primary';
    retry.type = 'button';
    retry.textContent = 'Try again';
    retry.addEventListener('click', () => loadDiscoveryResults(requestedPageUrl.href));
    const fallback = document.createElement('a');
    fallback.className = 'button secondary';
    const fallbackUrl = new URL(requestedPageUrl.href);
    fallbackUrl.searchParams.set('render_results', '1');
    fallback.href = fallbackUrl.href;
    fallback.textContent = 'Load directly';
    errorState.append(heading, message, retry, fallback);
    target.append(errorState);
    return false;
  }
};

(() => {
  const results = document.querySelector('[data-discovery-results-url]');
  const target = results?.querySelector('[data-discovery-results-content]');
  if (!results || !target) return;

  results.addEventListener('click', (event) => {
    const link = event.target instanceof Element
      ? event.target.closest('[data-discovery-results-navigation]')
      : null;
    if (!(link instanceof HTMLAnchorElement)) return;
    event.preventDefault();
    loadDiscoveryResults(link.href, { updateHistory: true });
  });

  results.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('[data-discovery-filter-form], [data-discovery-page-size-form]')) return;
    event.preventDefault();
    loadDiscoveryResults(discoveryPageUrlFromForm(form).href, { updateHistory: true });
  });

  window.addEventListener('popstate', () => {
    if (window.location.pathname.endsWith('/applications/job-discovery')) {
      loadDiscoveryResults(window.location.href, { updateHistory: false });
    }
  });

  if (results.dataset.discoveryResultsLoaded !== 'true') {
    loadDiscoveryResults(window.location.href, { showSkeleton: false });
  }
})();

// Keep shared-catalog materialization out of the initial Job Discovery page
// request. The page renders from its durable read model first, then checks for
// newer centrally collected postings in a separate bounded request. A catalog
// version marker prevents repeated checks while navigating result tabs/pages.
(() => {
  const results = document.querySelector('[data-discovery-catalog-hydration-url]');
  if (!results) return;

  const endpoint = results.dataset.discoveryCatalogHydrationUrl || '';
  const catalogVersion = results.dataset.discoveryCatalogVersion || 'unversioned';
  const ownerScope = results.dataset.discoveryOwnerScope || 'anonymous';
  const storageKey = `careerBridgeDiscoveryCatalogHydrated:${ownerScope}:${catalogVersion}`;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  if (!endpoint) return;

  try {
    if (window.sessionStorage.getItem(storageKey) === '1') return;
  } catch (_error) {
    // Storage is optional. The server-side freshness checks still make repeat
    // hydration requests safe and idempotent.
  }

  const markChecked = () => {
    try {
      window.sessionStorage.setItem(storageKey, '1');
    } catch (_error) {
      // Private browser modes may disable storage; no user action is required.
    }
  };

  const hydrate = async () => {
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = {};
      }
      if (!response.ok || payload.ok === false) return;

      markChecked();
      if (payload.changed === true) {
        // The server prebuilds the default index, and this call also covers any
        // non-default filters currently selected on the page.
        await prebuildDiscoveryResultIndex();
        // Refresh only the compact result fragment; the surrounding page and
        // controls are already current and do not need a full navigation.
        await loadDiscoveryResults(window.location.href, {
          updateHistory: false,
          showSkeleton: false,
          allowPrebuild: false,
        });
      }
    } catch (_error) {
      // Existing results remain usable. A later navigation can retry the sync.
    }
  };

  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(() => hydrate(), { timeout: 750 });
  } else {
    window.setTimeout(() => hydrate(), 0);
  }
})();

// Refresh shared Job Discovery sources one company per request. A single bulk
// Flask request can exceed a gateway's timeout when dozens of sources are
// configured, even though every individual connector is bounded.
(() => {
  const form = document.querySelector('[data-discovery-batch-refresh]');
  if (!form) return;

  const sourceData = document.querySelector('[data-discovery-refresh-sources]');
  const progressPanel = document.querySelector('[data-discovery-refresh-progress]');
  const progressTitle = progressPanel?.querySelector('[data-discovery-refresh-title]');
  const progressMessage = progressPanel?.querySelector('[data-discovery-refresh-message]');
  const progressMeter = progressPanel?.querySelector('[data-discovery-refresh-meter]');
  const progressSummary = progressPanel?.querySelector('[data-discovery-refresh-summary]');
  const issuePanel = progressPanel?.querySelector('[data-discovery-refresh-issues]');
  const stopButton = progressPanel?.querySelector('[data-discovery-refresh-stop]');
  const submitButton = form.querySelector('[data-discovery-refresh-submit]');
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const endpoint = form.dataset.sourceRefreshUrl || '';
  const storedSummaryKey = 'careerBridgeDiscoveryRefreshSummary';

  let sources = [];
  try {
    const parsed = JSON.parse(sourceData?.textContent || '[]');
    if (Array.isArray(parsed)) sources = parsed;
  } catch (_error) {
    sources = [];
  }

  const showStoredSummary = () => {
    try {
      const message = window.sessionStorage.getItem(storedSummaryKey);
      if (!message || !progressPanel || !progressTitle || !progressMessage) return;
      window.sessionStorage.removeItem(storedSummaryKey);
      progressPanel.hidden = false;
      progressTitle.textContent = 'Shared company refresh completed';
      progressMessage.textContent = message;
      if (progressMeter) {
        progressMeter.max = Math.max(1, sources.length);
        progressMeter.value = sources.length;
      }
    } catch (_error) {
      // Storage can be unavailable in private browser modes; the refresh still works.
    }
  };
  showStoredSummary();

  let running = false;
  let stopRequested = false;
  stopButton?.addEventListener('click', () => {
    stopRequested = true;
    stopButton.disabled = true;
    stopButton.textContent = 'Stopping after current company…';
  });

  form.addEventListener('submit', async (event) => {
    if (!endpoint || !sources.length || running) return;
    event.preventDefault();
    if (!form.checkValidity()) return;

    running = true;
    stopRequested = false;
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = 'Refreshing companies…';
    }
    if (stopButton) {
      stopButton.hidden = false;
      stopButton.disabled = false;
      stopButton.textContent = 'Stop after current company';
    }
    if (progressPanel) progressPanel.hidden = false;
    if (progressMeter) {
      progressMeter.max = Math.max(1, sources.length);
      progressMeter.value = 0;
    }
    if (progressTitle) progressTitle.textContent = `Refreshing 0 of ${sources.length} companies`;
    if (progressMessage) {
      progressMessage.textContent = 'Starting the shared catalog refresh. Recently scanned companies will be reused without another external request.';
    }
    if (progressSummary) progressSummary.textContent = '';
    if (issuePanel) {
      issuePanel.hidden = true;
      issuePanel.textContent = '';
    }

    const totals = {
      refreshed: 0,
      reused: 0,
      inProgress: 0,
      completed: 0,
      issues: 0,
      jobs: 0,
    };
    const issueMessages = [];
    let processed = 0;
    let fatalError = '';

    for (const source of sources) {
      if (stopRequested) break;
      const companyName = String(source.company_name || 'Company');
      if (progressTitle) {
        progressTitle.textContent = `Refreshing ${processed + 1} of ${sources.length}: ${companyName}`;
      }
      if (progressMessage) {
        progressMessage.textContent = 'This company runs in its own bounded request, so the overall scan does not depend on one long gateway request.';
      }

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRFToken': csrf } : {}),
          },
          body: JSON.stringify({ source_id: source.id }),
        });
        let result = {};
        try {
          result = await response.json();
        } catch (_error) {
          result = { message: `The server returned HTTP ${response.status}.` };
        }
        if ([400, 401, 403].includes(response.status)) {
          fatalError = String(result.message || result.error || 'Your session or security token expired. Reload the page and try again.');
          break;
        }
        if (!response.ok || result.ok === false) {
          totals.issues += 1;
          const details = Array.isArray(result.issues) && result.issues.length
            ? result.issues.join('; ')
            : String(result.message || `HTTP ${response.status}`);
          issueMessages.push(`${companyName}: ${details}`);
        }
        if (result.outcome === 'refreshed') totals.refreshed += 1;
        else if (result.outcome === 'reused') totals.reused += 1;
        else if (result.outcome === 'in_progress') totals.inProgress += 1;
        else totals.completed += 1;
        totals.jobs += Number(result.jobs_available || 0);
      } catch (error) {
        totals.issues += 1;
        const message = error instanceof Error ? error.message : 'Network request failed.';
        issueMessages.push(`${companyName}: ${message}`);
      }

      processed += 1;
      if (progressMeter) progressMeter.value = processed;
      if (progressSummary) {
        progressSummary.textContent = `${processed}/${sources.length} checked · ${totals.refreshed} refreshed · ${totals.reused} reused · ${totals.issues} with issues`;
      }
      if (issuePanel && issueMessages.length) {
        issuePanel.hidden = false;
        issuePanel.textContent = issueMessages.slice(-5).join('\n');
      }
    }

    running = false;
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = 'Refresh jobs for everyone';
    }
    if (stopButton) stopButton.hidden = true;

    if (fatalError) {
      if (progressTitle) progressTitle.textContent = 'Company refresh stopped';
      if (progressMessage) progressMessage.textContent = fatalError;
      if (issuePanel) {
        issuePanel.hidden = false;
        issuePanel.textContent = fatalError;
      }
      return;
    }

    if (stopRequested) {
      if (progressTitle) progressTitle.textContent = `Refresh stopped after ${processed} of ${sources.length} companies`;
      if (progressMessage) {
        progressMessage.textContent = 'You can restart safely. Recently refreshed companies will be reused from the shared catalog rather than scanned again.';
      }
      return;
    }

    const summary = `${sources.length} companies checked: ${totals.refreshed} refreshed, ${totals.reused} reused from the shared catalog, ${totals.inProgress} already refreshing, and ${totals.issues} with issues.`;
    if (progressTitle) progressTitle.textContent = 'Shared company refresh completed';
    if (progressMessage) progressMessage.textContent = `${summary} Reloading the updated results…`;
    try {
      window.sessionStorage.setItem(storedSummaryKey, summary);
    } catch (_error) {
      // Nonessential; the results page can still reload normally.
    }
    await prebuildDiscoveryResultIndex();
    window.setTimeout(() => window.location.reload(), 250);
  });
})();

// Scan one company source directly from the Company Sources manager. This uses
// the same bounded JSON endpoint as the shared bulk refresh, so connector,
// caching, catalog hydration, and error behavior stay consistent.
(() => {
  const forms = Array.from(document.querySelectorAll('[data-discovery-source-scan]'));
  if (!forms.length) return;

  const storedSummaryKey = 'careerBridgeDiscoverySourceScanSummary';

  const showFeedback = (form, message, isError = false) => {
    const feedback = form.querySelector('[data-discovery-source-scan-feedback]');
    if (!feedback) return;
    feedback.hidden = false;
    feedback.textContent = message;
    feedback.classList.toggle('is-error', isError);
    feedback.classList.toggle('is-success', !isError);
  };

  try {
    const stored = window.sessionStorage.getItem(storedSummaryKey);
    if (stored) {
      window.sessionStorage.removeItem(storedSummaryKey);
      const summary = JSON.parse(stored);
      const matchingForm = forms.find((item) => item.dataset.sourceId === String(summary.sourceId || ''));
      if (matchingForm) {
        const manager = matchingForm.closest('[data-discovery-source-id]');
        if (manager instanceof HTMLDetailsElement) manager.open = true;
        showFeedback(
          matchingForm,
          String(summary.message || 'Source scan completed.'),
          Boolean(summary.hasIssues),
        );
      }
    }
  } catch (_error) {
    // Session storage is optional; the persisted last-scan panel still updates.
  }

  forms.forEach((form) => {
    let running = false;
    form.addEventListener('submit', async (event) => {
      const endpoint = form.dataset.sourceRefreshUrl || '';
      const sourceId = form.dataset.sourceId || '';
      const companyName = form.dataset.companyName || 'Company';
      const button = form.querySelector('[data-discovery-source-scan-submit]');
      const csrf = form.querySelector('input[name="csrf_token"]')?.value
        || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
        || '';

      if (!endpoint || !sourceId || running || button?.disabled) return;
      event.preventDefault();
      if (!form.checkValidity()) return;

      running = true;
      if (button) {
        button.disabled = true;
        button.textContent = 'Scanning…';
      }
      showFeedback(form, `Scanning ${companyName}. This source runs in its own bounded request.`);

      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRFToken': csrf } : {}),
          },
          body: JSON.stringify({ source_id: sourceId }),
        });
        let result = {};
        try {
          result = await response.json();
        } catch (_error) {
          result = { message: `The server returned HTTP ${response.status}.` };
        }

        const issues = Array.isArray(result.issues) ? result.issues.filter(Boolean) : [];
        const baseMessage = String(result.message || `${companyName} scan completed.`);
        const detailMessage = issues.length ? `${baseMessage} ${issues.join('; ')}` : baseMessage;

        if (!response.ok) {
          showFeedback(form, detailMessage, true);
          return;
        }

        try {
          window.sessionStorage.setItem(
            storedSummaryKey,
            JSON.stringify({
              sourceId,
              message: detailMessage,
              hasIssues: result.ok === false || issues.length > 0,
            }),
          );
        } catch (_error) {
          // The page can still reload and show the persisted last-scan result.
        }
        showFeedback(form, `${detailMessage} Reloading the updated scan result…`, result.ok === false || issues.length > 0);
        window.setTimeout(() => window.location.reload(), 650);
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Network request failed.';
        showFeedback(form, `${companyName} could not be scanned: ${message}`, true);
      } finally {
        running = false;
        if (button) {
          button.disabled = false;
          button.textContent = 'Scan this source';
        }
      }
    });
  });
})();

// Assess already-collected Job Discovery postings for the signed-in user. The
// server processes a small batch per request so a large pending queue does not
// depend on one long-running Flask/Gunicorn request.
(() => {
  const form = document.querySelector('[data-discovery-assessment-run]');
  if (!form) return;

  const progressPanel = document.querySelector('[data-discovery-assessment-progress]');
  const progressTitle = progressPanel?.querySelector('[data-discovery-assessment-title]');
  const progressMessage = progressPanel?.querySelector('[data-discovery-assessment-message]');
  const progressMeter = progressPanel?.querySelector('[data-discovery-assessment-meter]');
  const progressSummary = progressPanel?.querySelector('[data-discovery-assessment-summary]');
  const issuePanel = progressPanel?.querySelector('[data-discovery-assessment-issues]');
  const stopButton = progressPanel?.querySelector('[data-discovery-assessment-stop]');
  const submitButtons = Array.from(form.querySelectorAll('[data-discovery-assessment-submit]'));
  const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const endpoint = form.dataset.assessmentUrl || '';
  const pendingCount = () => Math.max(0, Number(form.dataset.pendingCount || 0));
  const assessmentRunLimit = Math.max(1, Number(form.dataset.assessmentRunLimit || 25));
  const storedSummaryKey = 'careerBridgeDiscoveryAssessmentSummary';

  const showStoredSummary = () => {
    try {
      const message = window.sessionStorage.getItem(storedSummaryKey);
      if (!message || !progressPanel || !progressTitle || !progressMessage) return;
      window.sessionStorage.removeItem(storedSummaryKey);
      progressPanel.hidden = false;
      progressTitle.textContent = 'Pending-job assessment completed';
      progressMessage.textContent = message;
    } catch (_error) {
      // Storage can be unavailable in private browser modes; assessment still works.
    }
  };
  showStoredSummary();

  let running = false;
  let stopRequested = false;
  stopButton?.addEventListener('click', () => {
    stopRequested = true;
    stopButton.disabled = true;
    stopButton.textContent = 'Stopping after current batch…';
  });

  form.addEventListener('submit', async (event) => {
    if (!endpoint || running) return;
    event.preventDefault();
    if (!form.checkValidity()) return;

    const initialPendingCount = pendingCount();
    const submittedButton = event.submitter instanceof HTMLElement ? event.submitter : null;
    const assessAllRemaining = submittedButton?.dataset.assessmentScope === 'all';
    const runLimit = assessAllRemaining
      ? Math.max(initialPendingCount, assessmentRunLimit)
      : assessmentRunLimit;
    if (assessAllRemaining && initialPendingCount > assessmentRunLimit) {
      const confirmed = window.confirm(
        `Assess all ${initialPendingCount} remaining jobs? This may take a while and will use your configured AI budget. Keep this page open until it finishes.`
      );
      if (!confirmed) return;
    }

    running = true;
    stopRequested = false;
    for (const button of submitButtons) {
      button.disabled = true;
    }
    if (submittedButton) {
      submittedButton.dataset.originalText = submittedButton.textContent || '';
      submittedButton.textContent = assessAllRemaining
        ? 'Assessing all remaining jobs…'
        : 'Assessing next jobs…';
    }
    if (stopButton) {
      stopButton.hidden = false;
      stopButton.disabled = false;
      stopButton.textContent = 'Stop after current batch';
    }
    if (progressPanel) progressPanel.hidden = false;
    if (progressMeter) {
      progressMeter.max = Math.max(1, Math.min(initialPendingCount, runLimit));
      progressMeter.value = 0;
    }
    if (progressTitle) progressTitle.textContent = 'Assessing pending jobs';
    if (progressMessage) {
      progressMessage.textContent = assessAllRemaining
        ? `All ${initialPendingCount} remaining jobs will be scored against your Career Profile in small batches. Keep this page open; configured AI usage and cost controls still apply.`
        : `Up to ${assessmentRunLimit} jobs will be scored against your Career Profile in small batches. Configured AI usage and cost controls still apply.`;
    }
    if (progressSummary) progressSummary.textContent = '';
    if (issuePanel) {
      issuePanel.hidden = true;
      issuePanel.textContent = '';
    }

    const skippedJobKeys = [];
    const skippedSet = new Set();
    const issueMessages = [];
    let assessedTotal = 0;
    let attemptedTotal = 0;
    let remainingCount = initialPendingCount;
    let unresolvedCount = 0;
    let totalCount = initialPendingCount;
    let fatalError = '';
    let completed = false;

    while (!stopRequested && !completed && attemptedTotal < runLimit) {
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRFToken': csrf } : {}),
          },
          body: JSON.stringify({
            skip_job_keys: skippedJobKeys,
            // One AI-backed posting per HTTP request keeps each response below
            // the proxy timeout while the outer loop still handles up to 25 jobs.
            batch_size: 1,
          }),
        });
        let result = {};
        try {
          result = await response.json();
        } catch (_error) {
          result = { message: `The server returned HTTP ${response.status}.` };
        }
        if (!response.ok) {
          fatalError = String(result.message || result.error || `Assessment request failed (${response.status}).`);
          break;
        }

        const attempted = Math.max(0, Number(result.attempted_count || 0));
        const assessed = Math.max(0, Number(result.assessed_count || 0));
        attemptedTotal += attempted;
        assessedTotal += assessed;
        remainingCount = Math.max(0, Number(result.remaining_count || 0));
        unresolvedCount = Math.max(0, Number(result.unresolved_count || 0));
        totalCount = Math.max(totalCount, Number(result.pending_before || 0), remainingCount + assessedTotal);

        const failedKeys = Array.isArray(result.failed_job_keys) ? result.failed_job_keys : [];
        for (const key of failedKeys) {
          const normalized = String(key || '').trim();
          if (normalized && !skippedSet.has(normalized)) {
            skippedSet.add(normalized);
            skippedJobKeys.push(normalized);
          }
        }
        const issues = Array.isArray(result.issues) ? result.issues : [];
        issueMessages.push(...issues.map((item) => String(item || '')).filter(Boolean));
        completed = Boolean(result.complete);

        if (progressMeter) {
          progressMeter.max = Math.max(1, Math.min(totalCount, runLimit));
          progressMeter.value = Math.min(progressMeter.max, attemptedTotal);
        }
        if (progressTitle) {
          progressTitle.textContent = completed
            ? 'Pending-job assessment finishing'
            : assessAllRemaining
              ? `Assessing all pending jobs · ${attemptedTotal} of ${totalCount}`
              : `Assessing pending jobs · ${attemptedTotal} of ${Math.min(totalCount, runLimit)} this run`;
        }
        if (progressSummary) {
          progressSummary.textContent = `${assessedTotal} assessed · ${remainingCount} awaiting assessment · ${unresolvedCount} with issues`;
        }
        if (issuePanel && issueMessages.length) {
          issuePanel.hidden = false;
          issuePanel.textContent = issueMessages.slice(-5).join('\n');
        }

        if (!completed && attempted === 0 && failedKeys.length === 0) {
          fatalError = 'The assessment queue made no progress. Reload the page and try again.';
          break;
        }
      } catch (error) {
        fatalError = error instanceof Error ? error.message : 'The assessment request failed.';
        break;
      }
    }

    running = false;
    for (const button of submitButtons) {
      button.disabled = initialPendingCount === 0;
      if (button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
        delete button.dataset.originalText;
      }
    }
    if (stopButton) stopButton.hidden = true;

    if (fatalError) {
      if (progressTitle) progressTitle.textContent = 'Pending-job assessment stopped';
      if (progressMessage) progressMessage.textContent = fatalError;
      if (issuePanel) {
        issuePanel.hidden = false;
        issuePanel.textContent = fatalError;
      }
      return;
    }

    const runLimitReached = !completed && !stopRequested && attemptedTotal >= runLimit;
    const summary = stopRequested
      ? `Assessment stopped after ${assessedTotal} jobs. ${remainingCount} remain awaiting assessment.`
      : runLimitReached
        ? assessAllRemaining
          ? `${assessedTotal} jobs were assessed. ${remainingCount} remain because the queue changed while this run was active; choose Assess all remaining again to continue.`
          : `${assessedTotal} jobs were assessed in this run. ${remainingCount} remain; run the assessment again to continue.`
        : unresolvedCount
          ? `${assessedTotal} jobs were assessed. ${unresolvedCount} could not be assessed and can be retried later.`
          : `${assessedTotal} jobs were assessed. Recommended, Possible matches, and Low matches are now available.`;
    if (progressTitle) {
      progressTitle.textContent = stopRequested
        ? 'Pending-job assessment stopped'
        : 'Pending-job assessment completed';
    }
    if (progressMessage) progressMessage.textContent = `${summary} Reloading the updated results…`;
    try {
      window.sessionStorage.setItem(storedSummaryKey, summary);
    } catch (_error) {
      // Nonessential; the results page can still reload normally.
    }
    await prebuildDiscoveryResultIndex();
    window.setTimeout(() => window.location.reload(), 250);
  });
})();

