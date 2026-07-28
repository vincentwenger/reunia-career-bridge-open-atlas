(function () {
    'use strict';

    const navbar = document.getElementById('mainNavbar');
    const mobileMenuButton = document.getElementById('mobileMenuBtn');
    const navContent = document.getElementById('primaryNavContent');
    const categoryDropdowns = Array.from(document.querySelectorAll('[data-nav-category]'));
    const accountDropdown = document.getElementById('accountDropdown');
    const accountDropdownButton = document.getElementById('accountDropdownBtn');
    const accountDropdownMenu = document.getElementById('accountDropdownMenu');
    const desktopBreakpoint = 1080;

    if (!navbar) {
        return;
    }

    function categoryParts(dropdown) {
        return {
            button: dropdown?.querySelector('.nav-category-btn'),
            menu: dropdown?.querySelector('.nav-category-menu')
        };
    }

    function setCategoryDropdown(dropdown, open, options) {
        if (!dropdown) return;
        const { button, menu } = categoryParts(dropdown);
        if (!button || !menu) return;

        const settings = options || {};
        dropdown.classList.toggle('open', open);
        button.setAttribute('aria-expanded', String(open));
        menu.hidden = !open;

        if (open && settings.focusItem) {
            const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
            const target = settings.focusItem === 'last' ? items[items.length - 1] : items[0];
            target?.focus();
        }

        if (!open && settings.returnFocus) {
            button.focus();
        }
    }

    function closeCategoryDropdowns(exceptDropdown) {
        categoryDropdowns.forEach(function (dropdown) {
            if (dropdown !== exceptDropdown) {
                setCategoryDropdown(dropdown, false);
            }
        });
    }

    function setMobileMenu(open) {
        navbar.classList.toggle('mobile-open', open);

        if (mobileMenuButton) {
            mobileMenuButton.setAttribute('aria-expanded', String(open));
            mobileMenuButton.setAttribute('aria-label', open ? 'Close navigation menu' : 'Open navigation menu');
        }

        if (!open) {
            closeCategoryDropdowns();
            setAccountDropdown(false);
        }
    }

    function setAccountDropdown(open, returnFocus) {
        if (!accountDropdown || !accountDropdownButton) {
            return;
        }

        accountDropdown.classList.toggle('open', open);
        accountDropdownButton.setAttribute('aria-expanded', String(open));
        if (accountDropdownMenu) {
            accountDropdownMenu.hidden = !open;
        }

        if (open && accountDropdownMenu && window.innerWidth > desktopBreakpoint) {
            accountDropdownMenu.querySelector('[role="menuitem"]')?.focus();
        }

        if (returnFocus) {
            accountDropdownButton.focus();
        }
    }

    function moveMenuFocus(menu, event) {
        const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
        if (!items.length) return;

        const currentIndex = items.indexOf(document.activeElement);
        let nextIndex = currentIndex;

        if (event.key === 'ArrowDown') {
            nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % items.length;
        } else if (event.key === 'ArrowUp') {
            nextIndex = currentIndex < 0 ? items.length - 1 : (currentIndex - 1 + items.length) % items.length;
        } else if (event.key === 'Home') {
            nextIndex = 0;
        } else if (event.key === 'End') {
            nextIndex = items.length - 1;
        } else {
            return;
        }

        event.preventDefault();
        items[nextIndex].focus();
    }

    function updateScrollState() {
        navbar.classList.toggle('is-scrolled', window.scrollY > 8);
    }

    if (mobileMenuButton) {
        mobileMenuButton.addEventListener('click', function (event) {
            event.stopPropagation();
            setMobileMenu(!navbar.classList.contains('mobile-open'));
        });
    }

    categoryDropdowns.forEach(function (dropdown) {
        const { button, menu } = categoryParts(dropdown);
        if (!button || !menu) return;

        button.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            const shouldOpen = !dropdown.classList.contains('open');
            closeCategoryDropdowns(dropdown);
            setAccountDropdown(false);
            setCategoryDropdown(dropdown, shouldOpen);
        });

        button.addEventListener('keydown', function (event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                closeCategoryDropdowns(dropdown);
                setAccountDropdown(false);
                setCategoryDropdown(dropdown, true, {
                    focusItem: event.key === 'ArrowUp' ? 'last' : 'first'
                });
            } else if (event.key === 'Escape') {
                event.preventDefault();
                setCategoryDropdown(dropdown, false, { returnFocus: true });
            }
        });

        menu.addEventListener('keydown', function (event) {
            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                moveMenuFocus(menu, event);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                setCategoryDropdown(dropdown, false, { returnFocus: true });
            }
        });
    });

    if (accountDropdown && accountDropdownButton) {
        accountDropdownButton.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            closeCategoryDropdowns();
            setAccountDropdown(!accountDropdown.classList.contains('open'));
        });

        accountDropdown.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.stopPropagation();
                setAccountDropdown(false, true);
                return;
            }

            if (accountDropdownMenu && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                moveMenuFocus(accountDropdownMenu, event);
            }
        });
    }

    document.addEventListener('click', function (event) {
        categoryDropdowns.forEach(function (dropdown) {
            if (!dropdown.contains(event.target)) {
                setCategoryDropdown(dropdown, false);
            }
        });

        if (accountDropdown && !accountDropdown.contains(event.target)) {
            setAccountDropdown(false);
        }

        if (
            window.innerWidth <= desktopBreakpoint &&
            navbar.classList.contains('mobile-open') &&
            navContent &&
            !navbar.contains(event.target)
        ) {
            setMobileMenu(false);
        }
    });

    navbar.querySelectorAll('a').forEach(function (link) {
        link.addEventListener('click', function () {
            if (window.innerWidth <= desktopBreakpoint) {
                setMobileMenu(false);
            }
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') {
            return;
        }

        closeCategoryDropdowns();
        setAccountDropdown(false);
        setMobileMenu(false);
    });

    window.addEventListener('resize', function () {
        closeCategoryDropdowns();
        setAccountDropdown(false);
        if (window.innerWidth > desktopBreakpoint) {
            setMobileMenu(false);
        }
    });

    window.addEventListener('scroll', updateScrollState, { passive: true });
    updateScrollState();
})();
