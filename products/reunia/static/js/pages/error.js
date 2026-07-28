(function () {
    'use strict';

    function safePreviousUrl(fallbackUrl) {
        if (!document.referrer) return fallbackUrl;

        try {
            const previousUrl = new URL(document.referrer);
            const currentUrl = new URL(window.location.href);
            if (previousUrl.origin !== currentUrl.origin) return fallbackUrl;
            if (previousUrl.href === currentUrl.href) return fallbackUrl;
            return previousUrl.href;
        } catch (error) {
            return fallbackUrl;
        }
    }

    document.querySelectorAll('[data-safe-back]').forEach((button) => {
        button.addEventListener('click', () => {
            const fallbackUrl = button.dataset.fallbackUrl || window.AppUI?.appUrl('/') || '/';
            window.location.assign(safePreviousUrl(fallbackUrl));
        });
    });
})();
