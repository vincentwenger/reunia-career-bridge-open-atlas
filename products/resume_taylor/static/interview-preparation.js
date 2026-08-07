// Queue interview-preparation generation in durable storage. The Flask request
// only creates a job and returns 202; a separate worker owns the model call.
(() => {
  const form = document.querySelector('[data-interview-preparation-job-form]');
  if (!(form instanceof HTMLFormElement)) return;

  const submitButton = form.querySelector('[data-interview-preparation-submit]');
  const progressPanel = document.querySelector('[data-interview-preparation-job-progress]');
  const progressTitle = progressPanel?.querySelector('[data-interview-preparation-job-title]');
  const progressMessage = progressPanel?.querySelector('[data-interview-preparation-job-message]');
  const progressMeter = progressPanel?.querySelector('[data-interview-preparation-job-meter]');
  const progressSummary = progressPanel?.querySelector('[data-interview-preparation-job-summary]');
  const issuePanel = progressPanel?.querySelector('[data-interview-preparation-job-issues]');
  const cancelButton = progressPanel?.querySelector('[data-interview-preparation-job-cancel]');
  const csrf = form.querySelector('input[name="csrf_token"]')?.value
    || document.querySelector('meta[name="csrf-token"]')?.getAttribute('content')
    || '';

  let activeJobId = form.dataset.activeJobId || '';
  let statusUrl = form.dataset.activeJobStatusUrl || '';
  let cancelUrl = '';
  let retryUrl = '';
  let polling = false;
  let reloadScheduled = false;

  const setSubmitDisabled = (disabled) => {
    if (submitButton instanceof HTMLButtonElement) submitButton.disabled = disabled;
  };

  const requestJson = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        ...(options.headers || {}),
      },
      ...options,
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      payload = { message: `The server returned HTTP ${response.status}.` };
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(String(payload.message || payload.error || `Request failed (${response.status}).`));
    }
    return payload;
  };

  const clearIssues = () => {
    if (!issuePanel) return;
    issuePanel.hidden = true;
    issuePanel.replaceChildren();
  };

  const addRetryButton = (job) => {
    if (!issuePanel || !job.retry_url) return;
    const retryButton = document.createElement('button');
    retryButton.type = 'button';
    retryButton.className = 'button text';
    retryButton.textContent = 'Retry generation';
    retryButton.addEventListener('click', async () => {
      retryButton.disabled = true;
      try {
        const retried = await requestJson(job.retry_url, { method: 'POST' });
        reloadScheduled = false;
        renderJob(retried);
        pollUntilTerminal();
      } catch (error) {
        retryButton.disabled = false;
        if (progressMessage) {
          progressMessage.textContent = error instanceof Error
            ? error.message
            : 'Interview preparation could not be queued again.';
        }
      }
    });
    issuePanel.hidden = false;
    issuePanel.append(retryButton);
  };

  const renderJob = (job) => {
    if (!job) return;
    activeJobId = String(job.id || activeJobId || '');
    statusUrl = String(job.status_url || statusUrl || '');
    cancelUrl = String(job.cancel_url || cancelUrl || '');
    retryUrl = String(job.retry_url || retryUrl || '');

    const status = String(job.status || 'queued');
    const terminal = Boolean(job.terminal);
    const attempted = Math.max(0, Number(job.attempted_count || 0));
    const total = Math.max(1, Number(job.total_count || 1));
    const completed = Math.max(0, Number(job.completed_count || 0));

    if (progressPanel) progressPanel.hidden = false;
    if (progressMeter) {
      progressMeter.max = total;
      progressMeter.value = Math.min(total, attempted);
    }
    if (progressTitle) {
      const titles = {
        queued: 'Interview preparation queued',
        running: 'Generating interview preparation',
        completed: 'Interview preparation ready',
        completed_with_errors: 'Interview preparation completed with issues',
        failed: 'Interview preparation failed',
        canceled: 'Interview preparation stopped',
      };
      progressTitle.textContent = titles[status] || 'Background interview preparation';
    }
    if (progressMessage) {
      progressMessage.textContent = String(
        job.message || 'The background worker is processing this request. You can leave this page.'
      );
    }
    if (progressSummary) {
      progressSummary.textContent = terminal
        ? `${completed} preparation saved`
        : 'The durable job will continue if this tab is closed or the page is reloaded.';
    }

    clearIssues();
    const failures = Array.isArray(job.failed_items) ? job.failed_items : [];
    if (issuePanel && failures.length) {
      const messages = document.createElement('div');
      messages.textContent = failures
        .map((item) => String(item.message || 'Interview preparation failed.'))
        .join('\n');
      issuePanel.hidden = false;
      issuePanel.append(messages);
    }
    if (terminal && status !== 'completed' && retryUrl) addRetryButton({ ...job, retry_url: retryUrl });

    setSubmitDisabled(!terminal);
    if (cancelButton instanceof HTMLButtonElement) {
      cancelButton.hidden = terminal || !cancelUrl;
      cancelButton.disabled = Boolean(job.cancel_requested);
      cancelButton.textContent = job.cancel_requested
        ? 'Stopping after current operation…'
        : 'Stop after current operation';
    }

    if (status === 'completed' && !reloadScheduled) {
      reloadScheduled = true;
      if (progressMessage) {
        progressMessage.textContent = `${job.message || 'Interview preparation is ready.'} Reloading the saved workspace…`;
      }
      window.setTimeout(() => window.location.reload(), 1000);
    }
  };

  const pollUntilTerminal = async () => {
    if (!statusUrl || polling) return;
    polling = true;
    try {
      while (statusUrl) {
        const job = await requestJson(statusUrl);
        renderJob(job);
        if (job.terminal) break;
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    } catch (error) {
      if (progressPanel) progressPanel.hidden = false;
      if (progressTitle) progressTitle.textContent = 'Progress temporarily unavailable';
      if (progressMessage) {
        const detail = error instanceof Error ? error.message : 'Could not load progress.';
        progressMessage.textContent = `${detail} The worker continues independently; reload this page to reconnect.`;
      }
    } finally {
      polling = false;
    }
  };

  cancelButton?.addEventListener('click', async () => {
    if (!cancelUrl || !(cancelButton instanceof HTMLButtonElement)) return;
    cancelButton.disabled = true;
    cancelButton.textContent = 'Stopping after current operation…';
    try {
      renderJob(await requestJson(cancelUrl, { method: 'POST' }));
    } catch (error) {
      cancelButton.disabled = false;
      if (progressMessage) {
        progressMessage.textContent = error instanceof Error
          ? error.message
          : 'Cancellation could not be requested.';
      }
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!form.checkValidity() || submitButton?.disabled) return;

    setSubmitDisabled(true);
    if (progressPanel) progressPanel.hidden = false;
    if (progressTitle) progressTitle.textContent = 'Queueing interview preparation…';
    if (progressMessage) {
      progressMessage.textContent = 'The page will return as soon as the durable job is created.';
    }
    if (progressMeter) {
      progressMeter.max = 1;
      progressMeter.value = 0;
    }
    if (progressSummary) progressSummary.textContent = '';
    clearIssues();

    try {
      const job = await requestJson(form.action, {
        method: 'POST',
        body: new FormData(form),
      });
      reloadScheduled = false;
      renderJob(job);
      pollUntilTerminal();
    } catch (error) {
      setSubmitDisabled(false);
      if (progressTitle) progressTitle.textContent = 'Preparation could not be queued';
      if (progressMessage) {
        progressMessage.textContent = error instanceof Error ? error.message : 'The request failed.';
      }
    }
  });

  if (activeJobId && statusUrl) {
    setSubmitDisabled(true);
    if (progressPanel) progressPanel.hidden = false;
    if (progressTitle) progressTitle.textContent = 'Reconnecting to interview preparation…';
    pollUntilTerminal();
  }
})();
