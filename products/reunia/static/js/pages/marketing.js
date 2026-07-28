(function () {
    'use strict';

    const tabs = Array.from(document.querySelectorAll('[data-tour-tab]'));
    const panels = Array.from(document.querySelectorAll('[data-tour-panel]'));

    if (!tabs.length || !panels.length) return;

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            activateTab(tab.dataset.tourTab);
        });

        tab.addEventListener('keydown', function (event) {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();

            const currentIndex = tabs.indexOf(tab);
            let nextIndex = currentIndex;
            if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
            if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length;
            if (event.key === 'Home') nextIndex = 0;
            if (event.key === 'End') nextIndex = tabs.length - 1;

            tabs[nextIndex].focus();
            activateTab(tabs[nextIndex].dataset.tourTab);
        });
    });

    function activateTab(name) {
        tabs.forEach(function (tab) {
            const isActive = tab.dataset.tourTab === name;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', String(isActive));
            tab.tabIndex = isActive ? 0 : -1;
        });

        panels.forEach(function (panel) {
            const isActive = panel.dataset.tourPanel === name;
            panel.classList.toggle('is-active', isActive);
            panel.hidden = !isActive;
        });
    }
})();
