(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  const builderErrorState = document.getElementById('application-builder-error-state');
  const showBuilderError = (message) => {
    if (!builderErrorState) return;
    window.AppUI?.showWorkspaceState(builderErrorState, {
      state: 'error',
      title: 'This workspace could not finish the requested update',
      message: message || 'The current application data remains unchanged. Reload the workspace or retry the available action.'
    });
    builderErrorState.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  const fetchResumeJob = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        Accept: 'application/json',
        ...(csrfToken ? { 'X-CSRFToken': csrfToken } : {}),
        ...(options.headers || {}),
      },
    });
    let result = {};
    try {
      result = await response.json();
    } catch (_error) {
      result = {};
    }
    if (!response.ok) {
      throw new Error(result.message || 'We could not update your resume progress.');
    }
    return result;
  };

  const pollResumeJob = async (initialJob, onUpdate) => {
    let job = initialJob;
    onUpdate?.(job);
    while (!job.terminal) {
      await sleep(document.hidden ? 5000 : 1800);
      job = await fetchResumeJob(job.status_url);
      onUpdate?.(job);
    }
    return job;
  };

  const applyJobToCard = (card, job) => {
    const message = card.querySelector('[data-resume-job-message]');
    const status = card.querySelector('[data-resume-job-status]');
    const progress = card.querySelector('[data-resume-job-progress]');
    const percent = card.querySelector('[data-resume-job-percent]');
    const cancelButton = card.querySelector('[data-resume-job-cancel]');
    const retryButton = card.querySelector('[data-resume-job-retry]');
    const resultLink = card.querySelector('[data-resume-job-result]');
    const progressValue = Number(job.progress_percent || 0);

    card.dataset.statusUrl = job.status_url || card.dataset.statusUrl || '';
    card.dataset.cancelUrl = job.cancel_url || card.dataset.cancelUrl || '';
    card.dataset.retryUrl = job.retry_url || card.dataset.retryUrl || '';
    card.dataset.resultUrl = job.result_url || card.dataset.resultUrl || '';
    card.dataset.terminal = job.terminal ? 'true' : 'false';
    if (message) message.textContent = job.message || 'Your resume is being prepared.';
    if (status) {
      const statusLabels = {
        queued: 'Starting',
        running: 'In progress',
        completed: 'Ready',
        failed: 'Needs attention',
        canceled: 'Canceled',
      };
      const rawStatus = String(job.status || 'running');
      status.textContent = statusLabels[rawStatus] || rawStatus.replaceAll('_', ' ');
      status.classList.toggle('is-ready', Boolean(job.terminal && job.ok));
      status.classList.toggle('is-pending', !job.terminal || !job.ok);
    }
    if (progress) progress.value = progressValue;
    if (percent) percent.textContent = `${progressValue}%`;
    if (cancelButton) cancelButton.hidden = Boolean(job.terminal);
    if (retryButton) retryButton.hidden = !job.terminal || Boolean(job.ok);
    if (resultLink) {
      resultLink.hidden = !job.terminal || !job.ok;
      if (job.result_url) resultLink.href = job.result_url;
    }
  };

  const monitorResumeJobCard = async (card, initialJob = null) => {
    if (card.dataset.polling === 'true') return;
    card.dataset.polling = 'true';
    try {
      const first = initialJob || await fetchResumeJob(card.dataset.statusUrl);
      const completed = await pollResumeJob(first, (job) => applyJobToCard(card, job));
      if (completed.ok && completed.result_url) {
        await sleep(650);
        window.location.assign(completed.result_url);
      }
    } catch (error) {
      const message = card.querySelector('[data-resume-job-message]');
      const retryButton = card.querySelector('[data-resume-job-retry]');
      if (message) message.textContent = error.message || 'We could not load your resume progress.';
      if (retryButton) retryButton.hidden = false;
      showBuilderError(error.message);
    } finally {
      card.dataset.polling = 'false';
    }
  };

  document.querySelectorAll('[data-resume-async-job]').forEach((card) => {
    card.querySelector('[data-resume-job-cancel]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const job = await fetchResumeJob(card.dataset.cancelUrl, { method: 'POST' });
        applyJobToCard(card, job);
      } catch (error) {
        showBuilderError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    card.querySelector('[data-resume-job-retry]')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const job = await fetchResumeJob(card.dataset.retryUrl, { method: 'POST' });
        applyJobToCard(card, job);
        monitorResumeJobCard(card, job);
      } catch (error) {
        showBuilderError(error.message);
      } finally {
        button.disabled = false;
      }
    });

    if (card.dataset.terminal !== 'true' && card.dataset.statusUrl) {
      monitorResumeJobCard(card);
    }
  });

  const automaticReports = [...document.querySelectorAll('[data-auto-report]')];
  if (automaticReports.length) {
    (async () => {
      let shouldRefresh = false;
      let failedReportCount = 0;
      for (const item of automaticReports) {
        item.textContent = `${item.dataset.label || 'Resume Report'} · queued…`;
        try {
          const job = await fetchResumeJob(item.dataset.url, {
            method: 'POST',
            body: new FormData(),
          });
          const completed = await pollResumeJob(job, (current) => {
            item.textContent = `${item.dataset.label || 'Resume Report'} · ${Number(current.progress_percent || 0)}%`;
          });
          if (!completed.ok) throw new Error(completed.message || 'Report generation failed.');
          item.textContent = `${item.dataset.label || 'Resume Report'} · ready`;
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
      if (shouldRefresh && !failedReportCount) window.location.reload();
    })();
  }
})();
