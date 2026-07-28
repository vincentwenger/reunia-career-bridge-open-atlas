(function () {
    'use strict';

    const navbar = document.getElementById('mainNavbar');
    const mobileMenuButton = document.getElementById('mobileMenuBtn');
    const navContent = document.getElementById('primaryNavContent');
    const accountDropdown = document.getElementById('accountDropdown');
    const accountDropdownButton = document.getElementById('accountDropdownBtn');
    const accountDropdownMenu = document.getElementById('accountDropdownMenu');
    const desktopBreakpoint = 1340;

    if (!navbar) {
        return;
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

    function setMobileMenu(open) {
        navbar.classList.toggle('mobile-open', open);

        if (mobileMenuButton) {
            mobileMenuButton.setAttribute('aria-expanded', String(open));
            mobileMenuButton.setAttribute(
                'aria-label',
                open ? 'Close navigation menu' : 'Open navigation menu'
            );
        }

        if (!open) {
            setAccountDropdown(false);
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

    if (accountDropdown && accountDropdownButton) {
        accountDropdownButton.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
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
        if (event.key === 'Escape') {
            setAccountDropdown(false);
            setMobileMenu(false);
        }
    });

    window.addEventListener('resize', function () {
        setAccountDropdown(false);
        if (window.innerWidth > desktopBreakpoint) {
            setMobileMenu(false);
        }
    });

    window.addEventListener('scroll', updateScrollState, { passive: true });
    updateScrollState();
})();
