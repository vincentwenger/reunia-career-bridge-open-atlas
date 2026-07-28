(function () {
    'use strict';

    const page = document.getElementById('userGuidePage');
    if (!page) return;

    const searchInput = document.getElementById('guideSearch');
    const clearButton = document.getElementById('clearGuideSearch');
    const status = document.getElementById('guideSearchStatus');
    const sections = Array.from(page.querySelectorAll('[data-guide-section]'));
    const navLinks = Array.from(document.querySelectorAll('#guideSectionNav a'));
    let searchTimer = null;

    function normalize(value) {
        return String(value || '')
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .trim();
    }

    function applySearch() {
        if (!searchInput || !clearButton || !status) return;

        const query = normalize(searchInput.value);
        clearButton.hidden = !query;
        let matches = 0;

        sections.forEach(function (section) {
            const heading = section.querySelector('.guide-section-heading');
            const sectionMatches = Boolean(query) && normalize(heading?.textContent).includes(query);
            const sectionItems = Array.from(section.querySelectorAll('[data-guide-search-item]'));
            let visibleItems = 0;

            sectionItems.forEach(function (item) {
                const isMatch = !query || sectionMatches || normalize(item.textContent).includes(query);
                item.hidden = !isMatch;

                if (isMatch) {
                    visibleItems += 1;
                    matches += 1;
                    if (query && item.tagName === 'DETAILS') item.open = true;
                } else if (item.tagName === 'DETAILS') {
                    item.open = false;
                }
            });

            section.hidden = Boolean(query) && visibleItems === 0;
        });

        if (!query) {
            status.textContent = '';
        } else if (matches === 0) {
            status.textContent = 'No matching guide item was found. Try fewer words or open Help & Support.';
        } else {
            status.textContent = `${matches} guide item${matches === 1 ? '' : 's'} found.`;
        }
    }

    function scheduleSearch() {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(applySearch, 120);
    }

    if (searchInput && clearButton) {
        searchInput.addEventListener('input', scheduleSearch);
        searchInput.addEventListener('search', applySearch);

        clearButton.addEventListener('click', function () {
            searchInput.value = '';
            applySearch();
            searchInput.focus();
        });
    }

    navLinks.forEach(function (link) {
        link.addEventListener('click', function () {
            navLinks.forEach((item) => item.classList.remove('is-active'));
            link.classList.add('is-active');
        });
    });

    if ('IntersectionObserver' in window && sections.length && navLinks.length) {
        const observer = new IntersectionObserver(function (entries) {
            const visible = entries
                .filter((entry) => entry.isIntersecting && !entry.target.hidden)
                .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

            if (!visible) return;
            navLinks.forEach(function (link) {
                link.classList.toggle('is-active', link.getAttribute('href') === `#${visible.target.id}`);
            });
        }, {
            rootMargin: '-18% 0px -65% 0px',
            threshold: [0.05, 0.2, 0.5]
        });

        sections.forEach((section) => observer.observe(section));
    }
})();
