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

// Keep destructive Job Discovery actions compatible with the strict CSP.
// Confirmation text lives in data attributes instead of inline onsubmit code.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches('[data-discovery-confirm]')) return;
  const message = form.dataset.discoveryConfirm || '';
  if (message && !window.confirm(message)) event.preventDefault();
});

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

  results.addEventListener('change', (event) => {
    const control = event.target;
    if (!(control instanceof HTMLSelectElement)) return;
    if (!control.matches('[data-discovery-filter-auto-submit]')) return;
    const form = control.form;
    if (!(form instanceof HTMLFormElement) || !form.matches('[data-discovery-filter-form]')) return;

    if (typeof form.requestSubmit === 'function') {
      form.requestSubmit();
      return;
    }
    loadDiscoveryResults(discoveryPageUrlFromForm(form).href, { updateHistory: true });
  });

  results.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('[data-discovery-filter-form]')) return;
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

// Queue AI-backed Job Discovery assessment in durable storage. Flask only
// accepts the job and returns 202; a separate worker owns all model calls.
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
  const assessmentRunLimit = Math.max(1, Number(form.dataset.assessmentRunLimit || 25));
  let activeJobId = form.dataset.activeJobId || '';
  let statusUrl = form.dataset.activeJobStatusUrl || '';
  let cancelUrl = '';
  let retryUrl = '';
  let polling = false;
  let terminalReloadScheduled = false;

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
    let result = {};
    try {
      result = await response.json();
    } catch (_error) {
      result = { message: `The server returned HTTP ${response.status}.` };
    }
    if (!response.ok) {
      throw new Error(String(result.message || result.error || `Request failed (${response.status}).`));
    }
    return result;
  };

  const setButtonsDisabled = (disabled) => {
    for (const button of submitButtons) button.disabled = disabled;
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
    const completed = Math.max(0, Number(job.completed_count || 0));
    const total = Math.max(0, Number(job.total_count || 0));
    const failed = Math.max(0, Number(job.failed_count || 0));

    if (progressPanel) progressPanel.hidden = false;
    if (progressMeter) {
      progressMeter.max = Math.max(1, total);
      progressMeter.value = Math.min(progressMeter.max, attempted);
    }
    if (progressTitle) {
      const titles = {
        queued: 'Assessment queued',
        running: `Assessing jobs · ${attempted} of ${total}`,
        completed: 'Pending-job assessment completed',
        completed_with_errors: 'Assessment completed with issues',
        failed: 'Pending-job assessment failed',
        canceled: 'Pending-job assessment stopped',
      };
      progressTitle.textContent = titles[status] || 'Background assessment';
    }
    if (progressMessage) {
      progressMessage.textContent = String(job.message || 'Background processing is in progress. You can leave this page.');
    }
    if (progressSummary) {
      progressSummary.textContent = `${completed} assessed · ${Math.max(0, total - attempted)} remaining · ${failed} with issues`;
    }
    if (issuePanel) {
      const failures = Array.isArray(job.failed_items) ? job.failed_items : [];
      const messages = failures
        .slice(-5)
        .map((item) => `${String(item.label || item.job_id || 'Job')}: ${String(item.message || 'Assessment failed')}`);
      issuePanel.hidden = messages.length === 0;
      issuePanel.textContent = messages.join('\n');
      if (terminal && failures.length && retryUrl) {
        const retryButton = document.createElement('button');
        retryButton.type = 'button';
        retryButton.className = 'button text';
        retryButton.textContent = `Retry ${failures.length} failed assessment${failures.length === 1 ? '' : 's'}`;
        retryButton.addEventListener('click', async () => {
          retryButton.disabled = true;
          try {
            const retried = await requestJson(retryUrl, { method: 'POST' });
            terminalReloadScheduled = false;
            renderJob(retried);
            pollUntilTerminal();
          } catch (error) {
            retryButton.disabled = false;
            progressMessage.textContent = error instanceof Error ? error.message : 'Retry could not be queued.';
          }
        });
        issuePanel.append(document.createElement('br'), retryButton);
      }
    }

    setButtonsDisabled(!terminal);
    if (stopButton) {
      stopButton.hidden = terminal || !cancelUrl;
      stopButton.disabled = Boolean(job.cancel_requested);
      stopButton.textContent = job.cancel_requested ? 'Stopping after current job…' : 'Stop after current job';
    }

    if (status === 'completed' && !terminalReloadScheduled) {
      terminalReloadScheduled = true;
      window.setTimeout(async () => {
        await prebuildDiscoveryResultIndex();
        window.location.reload();
      }, 1200);
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
        progressMessage.textContent = `${error instanceof Error ? error.message : 'Could not load progress.'} The background worker continues independently; reload this page to reconnect.`;
      }
    } finally {
      polling = false;
    }
  };

  stopButton?.addEventListener('click', async () => {
    if (!cancelUrl) return;
    stopButton.disabled = true;
    stopButton.textContent = 'Stopping after current job…';
    try {
      renderJob(await requestJson(cancelUrl, { method: 'POST' }));
    } catch (error) {
      stopButton.disabled = false;
      if (progressMessage) progressMessage.textContent = error instanceof Error ? error.message : 'Cancellation could not be requested.';
    }
  });

  form.addEventListener('submit', async (event) => {
    if (!endpoint) return;
    event.preventDefault();
    if (!form.checkValidity()) return;
    const submittedButton = event.submitter instanceof HTMLElement ? event.submitter : null;
    const assessAllRemaining = submittedButton?.dataset.assessmentScope === 'all';
    if (assessAllRemaining) {
      const confirmed = window.confirm(
        'Queue all remaining jobs for background assessment? You may leave this page after the job is accepted; configured AI usage and cost controls still apply.'
      );
      if (!confirmed) return;
    }

    setButtonsDisabled(true);
    if (progressPanel) progressPanel.hidden = false;
    if (progressTitle) progressTitle.textContent = 'Queueing background assessment…';
    if (progressMessage) progressMessage.textContent = 'The page will return as soon as the durable job is created.';
    if (issuePanel) {
      issuePanel.hidden = true;
      issuePanel.textContent = '';
    }
    try {
      const job = await requestJson(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          assess_all_remaining: assessAllRemaining,
          run_limit: assessmentRunLimit,
        }),
      });
      terminalReloadScheduled = false;
      renderJob(job);
      pollUntilTerminal();
    } catch (error) {
      setButtonsDisabled(false);
      if (progressTitle) progressTitle.textContent = 'Assessment could not be queued';
      if (progressMessage) progressMessage.textContent = error instanceof Error ? error.message : 'The request failed.';
    }
  });

  if (activeJobId && statusUrl) {
    setButtonsDisabled(true);
    if (progressPanel) progressPanel.hidden = false;
    if (progressTitle) progressTitle.textContent = 'Reconnecting to background assessment…';
    pollUntilTerminal();
  }
})();
