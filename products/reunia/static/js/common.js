(function () {
    'use strict';

    const body = document.body;
    const appRoot = (body?.dataset.appRoot || '').replace(/\/$/, '');
    const tokenElement = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = tokenElement ? tokenElement.getAttribute('content') : '';

    function appUrl(path) {
        if (!path) return appRoot || '/';
        if (/^(?:[a-z]+:)?\/\//i.test(path)) return path;
        const normalizedPath = path.startsWith('/') ? path : `/${path}`;
        return `${appRoot}${normalizedPath}` || normalizedPath;
    }

    function debounce(callback, delay = 250) {
        let timeoutId;
        return function debounced(...args) {
            window.clearTimeout(timeoutId);
            timeoutId = window.setTimeout(() => callback.apply(this, args), delay);
        };
    }

    function ensureToastRegion() {
        let region = document.getElementById('app-toast-region');
        if (region) return region;

        region = document.createElement('div');
        region.id = 'app-toast-region';
        region.className = 'app-toast-region';
        region.setAttribute('aria-live', 'polite');
        region.setAttribute('aria-atomic', 'true');
        document.body.appendChild(region);
        return region;
    }

    function translated(value) {
        const text = String(value ?? '');
        return window.AppI18n?.t(text) || text;
    }

    function showToast(message, options = {}) {
        const {type = 'info', duration = 4000} = options;
        const region = ensureToastRegion();
        const toast = document.createElement('div');
        toast.className = `app-toast app-toast-${type}`;
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
        toast.textContent = translated(message);
        region.appendChild(toast);

        window.requestAnimationFrame(() => toast.classList.add('is-visible'));
        window.setTimeout(() => {
            toast.classList.remove('is-visible');
            window.setTimeout(() => toast.remove(), 180);
        }, duration);
    }

    function confirmAction(options = {}) {
        const {
            title = 'Confirm action',
            message = 'Are you sure you want to continue?',
            confirmLabel = 'Continue',
            cancelLabel = 'Cancel',
            danger = false
        } = options;

        return new Promise((resolve) => {
            const previousFocus = document.activeElement;
            const backdrop = document.createElement('div');
            backdrop.className = 'app-confirm-backdrop';
            backdrop.innerHTML = `
                <section class="app-confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="app-confirm-title" aria-describedby="app-confirm-message">
                    <h2 id="app-confirm-title"></h2>
                    <p id="app-confirm-message"></p>
                    <div class="app-confirm-actions">
                        <button type="button" class="app-confirm-cancel"></button>
                        <button type="button" class="app-confirm-submit ${danger ? 'danger' : ''}"></button>
                    </div>
                </section>
            `;

            const titleElement = backdrop.querySelector('#app-confirm-title');
            const messageElement = backdrop.querySelector('#app-confirm-message');
            const cancelButton = backdrop.querySelector('.app-confirm-cancel');
            const confirmButton = backdrop.querySelector('.app-confirm-submit');
            titleElement.textContent = translated(title);
            messageElement.textContent = translated(message);
            cancelButton.textContent = translated(cancelLabel);
            confirmButton.textContent = translated(confirmLabel);

            function close(result) {
                document.removeEventListener('keydown', handleKeydown, true);
                backdrop.remove();
                previousFocus?.focus?.();
                resolve(result);
            }

            function handleKeydown(event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    close(false);
                    return;
                }

                if (event.key !== 'Tab') return;
                const focusable = [cancelButton, confirmButton];
                const currentIndex = focusable.indexOf(document.activeElement);
                const nextIndex = event.shiftKey
                    ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
                    : (currentIndex >= focusable.length - 1 ? 0 : currentIndex + 1);
                event.preventDefault();
                focusable[nextIndex].focus();
            }

            cancelButton.addEventListener('click', () => close(false));
            confirmButton.addEventListener('click', () => close(true));
            backdrop.addEventListener('click', (event) => {
                if (event.target === backdrop) close(false);
            });
            document.addEventListener('keydown', handleKeydown, true);
            document.body.appendChild(backdrop);
            cancelButton.focus();
        });
    }

    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
        if (!csrfToken || form.querySelector('input[name="csrf_token"]')) return;

        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = 'csrf_token';
        hiddenInput.value = csrfToken;
        form.appendChild(hiddenInput);
    });

    const originalFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
        const request = input instanceof Request ? input : null;
        const options = Object.assign({}, init || {});
        const method = String(options.method || request?.method || 'GET').toUpperCase();
        const unsafeMethod = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method);

        if (unsafeMethod && csrfToken) {
            const requestUrl = input instanceof URL
                ? input.href
                : typeof input === 'string'
                    ? input
                    : input.url;
            const resolvedUrl = new URL(requestUrl, window.location.href);

            if (resolvedUrl.origin === window.location.origin) {
                const headers = new Headers(options.headers || request?.headers || {});
                if (!headers.has('X-CSRFToken')) {
                    headers.set('X-CSRFToken', csrfToken);
                }
                options.headers = headers;
            }
        }

        return originalFetch(input, options);
    };

    window.AppUI = Object.freeze({
        appUrl,
        debounce,
        showToast,
        confirm: confirmAction
    });
})();
