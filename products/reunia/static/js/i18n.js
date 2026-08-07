(function () {
    'use strict';

    const STORAGE_KEY = 'reunia-language';
    const SUPPORTED = new Set(['en', 'fr']);
    const EMPTY_CATALOG = Object.freeze({});

    let fr = EMPTY_CATALOG;
    let frCaseInsensitive = new Map();
    let frTemplates = [];

    function refreshCatalog() {
        fr = window.ReuniaTranslations?.fr || EMPTY_CATALOG;
        frCaseInsensitive = new Map(
            Object.entries(fr).map(([key, value]) => [
                normalizeTranslationText(key).toLocaleLowerCase('en-US'),
                value
            ])
        );
        frTemplates = Object.entries(fr)
            .filter(([key]) => key.includes('{value}'))
            .map(([key, translation]) => {
                const normalizedKey = normalizeTranslationText(key);
                const parts = normalizedKey.split('{value}');
                return {
                    pattern: new RegExp(`^${parts.map(escapePattern).join('(.*?)')}$`, 'iu'),
                    translation,
                    specificity: parts.join('').length
                };
            })
            .sort((a, b) => b.specificity - a.specificity);
    }

    function normalizeLanguage(value) {
        const text = String(value || '').trim().toLowerCase().replace('_', '-');
        if (text.startsWith('fr')) return 'fr';
        return 'en';
    }

    function queryLanguage() {
        const requested = new URLSearchParams(window.location.search).get('lang');
        if (!requested) return null;
        const normalized = normalizeLanguage(requested);
        const raw = String(requested).trim().toLowerCase().replace('_', '-');
        return raw.startsWith('en') || raw.startsWith('fr') ? normalized : null;
    }

    function resolveLanguage() {
        const body = document.body;
        const server = normalizeLanguage(body?.dataset.appLanguage || document.documentElement.lang);
        const authenticated = body?.dataset.authenticated === 'true';
        const requested = queryLanguage();
        if (!authenticated && requested) {
            window.localStorage.setItem(STORAGE_KEY, requested);
            return requested;
        }
        const storedValue = window.localStorage.getItem(STORAGE_KEY);
        const stored = normalizeLanguage(storedValue);
        if (authenticated) return server;
        return SUPPORTED.has(stored) && storedValue ? stored : server;
    }

    function normalizeTranslationText(value) {
        return String(value ?? '').replace(/\s+/g, ' ').trim();
    }

    const escapePattern = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    refreshCatalog();

    function lookupTranslation(value) {
        const normalized = normalizeTranslationText(value);
        if (!normalized) return undefined;
        if (Object.prototype.hasOwnProperty.call(fr, normalized)) return fr[normalized];
        return frCaseInsensitive.get(normalized.toLocaleLowerCase('en-US'));
    }

    function fillTemplate(template, values) {
        const translatedValues = values.map((value) => {
            // Template delimiters can leave surrounding whitespace in a capture.
            // Normalize it so applying the same French translation stays stable.
            const normalizedValue = normalizeTranslationText(value);
            return lookupTranslation(normalizedValue) ?? normalizedValue;
        });
        let index = 0;
        return template.replace(/\{value\}/g, () => translatedValues[index++] ?? '');
    }

    function activeCatalog() {
        return window.ReuniaTranslations?.fr || EMPTY_CATALOG;
    }

    function translate(value) {
        const original = String(value ?? '');
        if (language !== 'fr') return original;
        const normalized = normalizeTranslationText(original);
        if (!normalized) return original;

        const direct = lookupTranslation(normalized);
        if (direct !== undefined) return direct;

        for (const template of frTemplates) {
            const match = normalized.match(template.pattern);
            if (match) return fillTemplate(template.translation, match.slice(1));
        }
        return original;
    }

    function shouldSkip(element) {
        if (!element || !element.closest) return false;
        return Boolean(element.closest(
            'script, style, pre, code, textarea, [contenteditable="true"], [data-i18n-skip], ' +
            '.transcript-content, .transcript-text, .meeting-transcript, .knowledge-answer, ' +
            '.ai-answer, .source-excerpt, .document-preview, .user-content'
        ));
    }

    function translateTextNode(node) {
        const parent = node.parentElement;
        if (!parent || shouldSkip(parent)) return;
        const original = node.nodeValue || '';
        const trimmed = normalizeTranslationText(original);
        if (!trimmed) return;
        const translated = translate(trimmed);
        if (translated === trimmed) return;
        const leading = original.match(/^\s*/)?.[0] || '';
        const trailing = original.match(/\s*$/)?.[0] || '';
        const nextValue = leading + translated + trailing;
        // The observer watches character data, so an unchanged write would
        // schedule this node for translation forever.
        if (nextValue === original) return;
        node.nodeValue = nextValue;
    }

    function translateElement(element) {
        if (!(element instanceof Element) || shouldSkip(element)) return;
        for (const attribute of ['placeholder', 'title', 'aria-label', 'alt']) {
            const value = element.getAttribute(attribute);
            if (value) {
                const translated = translate(value);
                if (translated !== value) element.setAttribute(attribute, translated);
            }
        }
        if (element instanceof HTMLMetaElement && element.name.toLowerCase() === 'description') {
            const content = element.getAttribute('content');
            if (content) element.setAttribute('content', translate(content));
        }
        if (element instanceof HTMLInputElement && ['button', 'submit', 'reset'].includes(element.type)) {
            element.value = translate(element.value);
        }
    }

    function apply(root) {
        if (language !== 'fr' || !root) return;
        if (root.nodeType === Node.TEXT_NODE) {
            translateTextNode(root);
            return;
        }
        if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
        if (root.nodeType === Node.ELEMENT_NODE) translateElement(root);
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
            else translateElement(node);
        }
        document.documentElement.lang = 'fr';
    }

    const language = resolveLanguage();
    const locale = language === 'fr' ? 'fr-FR' : 'en-US';
    window.AppI18n = {
        language,
        locale,
        t: translate,
        apply,
        setLanguage(nextLanguage) {
            const normalized = normalizeLanguage(nextLanguage);
            window.localStorage.setItem(STORAGE_KEY, normalized);
            const url = new URL(window.location.href);
            url.searchParams.set('lang', normalized);
            window.location.assign(url.toString());
        }
    };

    function initializeGuestLanguageButtons() {
        const dropdowns = Array.from(document.querySelectorAll('[data-guest-language-dropdown]'));

        function closeDropdown(dropdown, returnFocus) {
            const trigger = dropdown.querySelector('[data-guest-language-trigger]');
            const menu = dropdown.querySelector('[data-guest-language-menu]');
            if (!trigger || !menu) return;

            trigger.setAttribute('aria-expanded', 'false');
            menu.hidden = true;
            if (returnFocus) trigger.focus();
        }

        dropdowns.forEach(function (dropdown) {
            const trigger = dropdown.querySelector('[data-guest-language-trigger]');
            const menu = dropdown.querySelector('[data-guest-language-menu]');
            const options = Array.from(dropdown.querySelectorAll('[data-guest-language-toggle]'));
            if (!trigger || !menu) return;

            trigger.addEventListener('click', function () {
                const shouldOpen = trigger.getAttribute('aria-expanded') !== 'true';
                dropdowns.forEach(function (otherDropdown) {
                    if (otherDropdown !== dropdown) closeDropdown(otherDropdown, false);
                });
                trigger.setAttribute('aria-expanded', String(shouldOpen));
                menu.hidden = !shouldOpen;
                if (shouldOpen) {
                    const activeOption = options.find(function (option) {
                        return option.getAttribute('aria-checked') === 'true';
                    });
                    (activeOption || options[0])?.focus();
                }
            });

            options.forEach(function (button) {
                const targetLanguage = normalizeLanguage(button.dataset.targetLanguage);
                const isActive = targetLanguage === language;

                button.classList.toggle('active', isActive);
                button.setAttribute('aria-checked', String(isActive));
                button.addEventListener('click', function () {
                    if (isActive) {
                        closeDropdown(dropdown, true);
                        return;
                    }
                    window.AppI18n.setLanguage(targetLanguage);
                });
            });

            dropdown.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    closeDropdown(dropdown, true);
                    return;
                }

                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
                event.preventDefault();
                const currentIndex = options.indexOf(document.activeElement);
                const direction = event.key === 'ArrowDown' ? 1 : -1;
                const nextIndex = currentIndex < 0
                    ? 0
                    : (currentIndex + direction + options.length) % options.length;
                options[nextIndex]?.focus();
            });
        });

        document.addEventListener('click', function (event) {
            dropdowns.forEach(function (dropdown) {
                if (!dropdown.contains(event.target)) closeDropdown(dropdown, false);
            });
        });
    }

    function loadFrenchCatalog() {
        if (window.ReuniaTranslations?.fr) return Promise.resolve();
        const source = document.body?.dataset.i18nFrSrc;
        if (!source) return Promise.resolve();
        return new Promise(function (resolve) {
            const script = document.createElement('script');
            script.src = source;
            script.defer = true;
            script.addEventListener('load', resolve, {once: true});
            script.addEventListener('error', resolve, {once: true});
            document.head.appendChild(script);
        });
    }

    function startFrenchTranslation() {
        apply(document.documentElement);
        const observer = new MutationObserver(function (mutations) {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) apply(node);
                if (mutation.type === 'characterData' || mutation.type === 'attributes') {
                    apply(mutation.target);
                }
            }
        });
        if (document.body) {
            observer.observe(document.body, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['placeholder', 'title', 'aria-label', 'alt']
            });
        }
    }

    document.documentElement.lang = language;
    const domReady = document.readyState === 'loading'
        ? new Promise(function (resolve) { document.addEventListener('DOMContentLoaded', resolve, {once: true}); })
        : Promise.resolve();
    window.AppI18n.ready = domReady.then(function () {
        initializeGuestLanguageButtons();
        if (language !== 'fr') return;
        return loadFrenchCatalog().then(function () {
            refreshCatalog();
            startFrenchTranslation();
        });
    });
})();
