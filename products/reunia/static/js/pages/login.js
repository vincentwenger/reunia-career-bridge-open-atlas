'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('login-form');
    const signupForm = document.getElementById('signup-form');
    const mainTitle = document.getElementById('auth-title');
    const subTitle = document.getElementById('auth-subtitle');
    const tabLogin = document.getElementById('tab-login');
    const tabSignup = document.getElementById('tab-signup');
    const toSignupBtn = document.getElementById('link-to-signup');
    const toLoginBtn = document.getElementById('link-to-login');
    const passwordField = document.getElementById('signup-password');
    const confirmPasswordField = document.getElementById('signup-confirm-password');
    const errorDiv = document.getElementById('password-error');
    const authCard = document.querySelector('.auth-card');
    const serverError = document.getElementById('auth-error');

    function setAuthMode(mode, {focus = true} = {}) {
        const signupMode = mode === 'signup';
        loginForm.classList.toggle('hidden', signupMode);
        signupForm.classList.toggle('hidden', !signupMode);
        tabLogin.classList.toggle('active', !signupMode);
        tabSignup.classList.toggle('active', signupMode);
        tabLogin.setAttribute('aria-selected', String(!signupMode));
        tabSignup.setAttribute('aria-selected', String(signupMode));
        mainTitle.textContent = signupMode ? 'Create Account' : 'Welcome Back';
        subTitle.textContent = signupMode
            ? 'Create your workspace to start capturing and analyzing meetings.'
            : 'Log in to continue reviewing your meeting intelligence dashboard.';
        if (focus) {
            (signupMode ? document.getElementById('signup-name') : document.getElementById('login-email'))?.focus();
        }
    }

    function validatePasswordMatch() {
        if (!confirmPasswordField.value) {
            errorDiv.style.display = 'none';
            confirmPasswordField.classList.remove('error', 'success');
            confirmPasswordField.removeAttribute('aria-invalid');
            return false;
        }
        const matches = passwordField.value === confirmPasswordField.value;
        errorDiv.style.display = matches ? 'none' : 'block';
        confirmPasswordField.classList.toggle('error', !matches);
        confirmPasswordField.classList.toggle('success', matches);
        confirmPasswordField.setAttribute('aria-invalid', String(!matches));
        return matches;
    }

    document.querySelectorAll('[data-password-target]').forEach(button => {
        button.addEventListener('click', () => {
            const input = document.getElementById(button.dataset.passwordTarget);
            if (!input) return;
            const reveal = input.type === 'password';
            input.type = reveal ? 'text' : 'password';
            button.textContent = reveal ? 'Hide' : 'Show';
            button.setAttribute('aria-pressed', String(reveal));
        });
    });

    tabSignup.addEventListener('click', () => setAuthMode('signup'));
    tabLogin.addEventListener('click', () => setAuthMode('login'));
    toSignupBtn.addEventListener('click', event => {
        event.preventDefault();
        setAuthMode('signup');
    });
    toLoginBtn.addEventListener('click', event => {
        event.preventDefault();
        setAuthMode('login');
    });
    signupForm.addEventListener('submit', event => {
        if (!validatePasswordMatch()) {
            event.preventDefault();
            confirmPasswordField.focus();
        }
    });
    passwordField.addEventListener('input', validatePasswordMatch);
    confirmPasswordField.addEventListener('input', validatePasswordMatch);

    setAuthMode(authCard?.dataset.authMode === 'signup' ? 'signup' : 'login', {focus: false});
    serverError?.focus();
});
