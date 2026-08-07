(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
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
