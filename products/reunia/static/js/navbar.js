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

    const navGroups = Array.from(navbar.querySelectorAll('[data-nav-group]'));

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

    function setNavGroup(group, open, returnFocus) {
        if (!group) return;
        const trigger = group.querySelector('.nav-group-trigger');
        const menu = group.querySelector('.nav-group-menu');
        if (!trigger || !menu) return;

        group.classList.toggle('open', open);
        trigger.setAttribute('aria-expanded', String(open));
        menu.hidden = !open;

        if (returnFocus) {
            trigger.focus();
        }
    }

    function closeNavGroups(exceptGroup) {
        navGroups.forEach(function (group) {
            if (group !== exceptGroup) {
                setNavGroup(group, false);
            }
        });
    }

    function setAccountDropdown(open, returnFocus) {
        if (!accountDropdown || !accountDropdownButton) {
            return;
        }

        if (open) {
            closeNavGroups();
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
            closeNavGroups();
            setAccountDropdown(false);
        }
    }

    function updateScrollState() {
        navbar.classList.toggle('is-scrolled', window.scrollY > 8);
    }

    navGroups.forEach(function (group) {
        const trigger = group.querySelector('.nav-group-trigger');
        const menu = group.querySelector('.nav-group-menu');
        if (!trigger || !menu) return;

        trigger.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            const shouldOpen = !group.classList.contains('open');
            closeNavGroups(group);
            setAccountDropdown(false);
            setNavGroup(group, shouldOpen);
        });

        trigger.addEventListener('keydown', function (event) {
            if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
                event.preventDefault();
                closeNavGroups(group);
                setAccountDropdown(false);
                setNavGroup(group, true);
                const items = menu.querySelectorAll('[role="menuitem"]');
                const target = event.key === 'ArrowUp' ? items[items.length - 1] : items[0];
                target?.focus();
            }
        });

        group.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.stopPropagation();
                setNavGroup(group, false, true);
                return;
            }

            if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                moveMenuFocus(menu, event);
            }
        });
    });

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
        if (!navbar.contains(event.target)) {
            closeNavGroups();
            setAccountDropdown(false);
            if (window.innerWidth <= desktopBreakpoint) {
                setMobileMenu(false);
            }
            return;
        }

        navGroups.forEach(function (group) {
            if (!group.contains(event.target)) {
                setNavGroup(group, false);
            }
        });

        if (accountDropdown && !accountDropdown.contains(event.target)) {
            setAccountDropdown(false);
        }
    });

    navbar.querySelectorAll('a').forEach(function (link) {
        link.addEventListener('click', function () {
            closeNavGroups();
            if (window.innerWidth <= desktopBreakpoint) {
                setMobileMenu(false);
            }
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            closeNavGroups();
            setAccountDropdown(false);
            setMobileMenu(false);
        }
    });

    window.addEventListener('resize', function () {
        closeNavGroups();
        setAccountDropdown(false);
        if (window.innerWidth > desktopBreakpoint) {
            setMobileMenu(false);
        }
    });

    window.addEventListener('scroll', updateScrollState, { passive: true });
    updateScrollState();
})();
