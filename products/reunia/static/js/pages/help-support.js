(function () {
    'use strict';

    const page = document.getElementById('supportPage');
    const form = document.getElementById('supportForm');
    if (!page || !form) return;

    const endpoint = page.dataset.supportEndpoint || window.AppUI?.appUrl('/api/support') || '/api/support';
    const supportEmail = (page.dataset.supportEmail || '').trim();
    const submitButton = document.getElementById('submitSupportRequest');
    const copyButton = document.getElementById('copySupportRequest');
    const statusElement = document.getElementById('supportSubmitStatus');
    const messageInput = document.getElementById('supportMessage');
    const messageCount = document.getElementById('supportMessageCount');
    const attachmentInput = document.getElementById('supportAttachment');
    const fileName = document.getElementById('supportFileName');
    const detailHint = document.getElementById('supportDetailHint');
    const faqSearch = document.getElementById('supportFaqSearch');
    const faqItems = Array.from(document.querySelectorAll('[data-support-faq]'));
    const faqEmpty = document.getElementById('supportFaqEmpty');
    const maximumFileSize = 5 * 1024 * 1024;
    const allowedExtensions = ['png', 'jpg', 'jpeg', 'pdf', 'txt', 'log'];

    const fields = {
        name: document.getElementById('supportName'),
        email: document.getElementById('supportEmail'),
        topic: document.getElementById('supportTopic'),
        area: document.getElementById('supportArea'),
        subject: document.getElementById('supportSubject'),
        message: messageInput,
        attachment: attachmentInput
    };

    const areaGuidance = {
        'getting-started': 'Mention where you became unsure and which step you expected to complete next.',
        preparation: 'Mention whether the issue involves Application Workspace, Application Materials, Career Profile, or Career Evidence Library, plus the file type when relevant.',
        recorder: 'Mention your browser, microphone status, whether interviewer prompt audio was enabled, and the last status shown by the recorder.',
        'meeting-review': 'Mention the application workspace or approximate practice date and whether the issue affects Summary, Interview Scorecard, Transcript, or Ask about this interview.',
        sharing: 'Mention which sharing option was selected, whether a password or expiration was used, and what the recipient experienced.',
        'action-center': 'Mention the action, selected view or filters, and whether the problem affects editing, application linking, status, priority, or due date.',
        analytics: 'Mention the Impact & Progress chart or metric and the applications, mock interviews, or date range you expected it to include.',
        settings: 'Mention the settings section and the value you selected, including Interview Scorecard Source when relevant.',
        account: 'Mention whether the issue involves sign-in, profile details, navigation, or session behavior. Never include your password.',
        other: 'Describe where the issue happened and the last few steps before it occurred.'
    };

    function setStatus(message, type = 'info') {
        statusElement.textContent = message || '';
        statusElement.className = `support-submit-status ${message ? type : ''}`;
    }

    function setError(fieldName, message) {
        const field = fields[fieldName];
        const error = document.getElementById(`support${fieldName.charAt(0).toUpperCase()}${fieldName.slice(1)}Error`);
        field?.classList.toggle('is-invalid', Boolean(message));
        field?.setAttribute('aria-invalid', message ? 'true' : 'false');
        if (error) error.textContent = message || '';
    }

    function clearErrors() {
        Object.keys(fields).forEach((fieldName) => setError(fieldName, ''));
        setStatus('');
    }

    function validateAttachment() {
        const file = attachmentInput.files?.[0];
        if (!file) return '';
        const extension = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
        if (!allowedExtensions.includes(extension)) {
            return 'Choose a PNG, JPG, PDF, TXT, or LOG file.';
        }
        if (file.size > maximumFileSize) {
            return 'The attachment must be 5 MB or smaller.';
        }
        return '';
    }

    function validateForm() {
        clearErrors();
        let valid = true;

        if (!fields.name.value.trim()) {
            setError('name', 'Enter your name.');
            valid = false;
        }

        const email = fields.email.value.trim();
        if (!email) {
            setError('email', 'Enter your email address.');
            valid = false;
        } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            setError('email', 'Enter a valid email address.');
            valid = false;
        }

        if (!fields.topic.value) {
            setError('topic', 'Select a request type.');
            valid = false;
        }

        if (!fields.area.value) {
            setError('area', 'Select the feature or area involved.');
            valid = false;
        }

        if (!fields.subject.value.trim()) {
            setError('subject', 'Enter a short subject.');
            valid = false;
        }

        if (!fields.message.value.trim()) {
            setError('message', 'Describe how we can help.');
            valid = false;
        }

        const attachmentError = validateAttachment();
        if (attachmentError) {
            setError('attachment', attachmentError);
            valid = false;
        }

        if (!valid) {
            form.querySelector('.is-invalid')?.focus();
        }
        return valid;
    }

    function selectedLabel(field, fallback) {
        const option = field.options[field.selectedIndex];
        return option?.textContent?.trim() || fallback;
    }

    function buildPlainTextRequest() {
        const t = (value) => window.AppI18n?.t(value) || value;
        const lines = [
            t('Réunia support request'),
            '',
            `${t('Name:')} ${fields.name.value.trim()}`,
            `${t('Email:')} ${fields.email.value.trim()}`,
            `${t('Request type:')} ${selectedLabel(fields.topic, 'Other')}`,
            `${t('Feature or area:')} ${selectedLabel(fields.area, 'Other')}`,
            `${t('Subject:')} ${fields.subject.value.trim()}`,
            '',
            t('Message:'),
            fields.message.value.trim()
        ];

        const attachment = attachmentInput.files?.[0];
        if (attachment) {
            lines.push('', `${t('Attachment selected:')} ${attachment.name}`);
        }
        lines.push('', `Page: ${window.location.href}`, `Browser: ${navigator.userAgent}`);
        return lines.join('\n');
    }

    async function copyRequest() {
        if (!validateForm()) return;
        try {
            await navigator.clipboard.writeText(buildPlainTextRequest());
            setStatus('The support request was copied. You can paste it into an email or message.', 'success');
            window.AppUI?.showToast('Support request copied.', {type: 'success'});
        } catch (error) {
            setStatus('Your browser could not copy the request automatically.', 'error');
        }
    }

    function openEmailFallback() {
        if (!supportEmail) return false;
        const subject = `[Réunia Support] ${fields.subject.value.trim()}`;
        const body = buildPlainTextRequest();
        window.location.href = `mailto:${encodeURIComponent(supportEmail)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
        return true;
    }

    async function submitRequest(event) {
        event.preventDefault();
        if (!validateForm()) return;

        submitButton.disabled = true;
        submitButton.classList.add('is-loading');
        setStatus('Sending your request…', 'info');

        const formData = new FormData(form);
        formData.append('page_url', window.location.href);

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                body: formData,
                headers: {'Accept': 'application/json'}
            });

            let payload = {};
            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                payload = await response.json();
            }

            if (!response.ok) {
                const error = new Error(payload.message || payload.error || `Support request failed (${response.status}).`);
                error.status = response.status;
                throw error;
            }

            setStatus(payload.message || 'Your support request was sent successfully.', 'success');
            window.AppUI?.showToast('Support request sent.', {type: 'success'});
            form.reset();
            fileName.textContent = 'Choose a file';
            messageCount.textContent = '0 / 5000';
            updateAreaGuidance();
        } catch (error) {
            if ([404, 405, 501].includes(error.status) && openEmailFallback()) {
                setStatus('The support endpoint is unavailable, so your email app was opened instead.', 'info');
            } else {
                const message = error.message || 'We could not save your support request. Please try again.';
                setStatus(message, 'error');
                window.AppUI?.showToast('Support request was not saved.', {type: 'error'});
            }
        } finally {
            submitButton.disabled = false;
            submitButton.classList.remove('is-loading');
        }
    }

    function updateAreaGuidance() {
        const guidance = areaGuidance[fields.area.value];
        const paragraph = detailHint?.querySelector('p');
        if (!paragraph) return;
        paragraph.textContent = guidance || 'Select a feature area to see which details will help us investigate.';
        detailHint.classList.toggle('has-guidance', Boolean(guidance));
    }

    function filterFaqs() {
        const query = (faqSearch?.value || '').trim().toLowerCase();
        let visibleCount = 0;

        faqItems.forEach(function (item) {
            const matches = !query || item.textContent.toLowerCase().includes(query);
            item.hidden = !matches;
            if (matches) visibleCount += 1;
        });

        if (faqEmpty) faqEmpty.hidden = visibleCount !== 0;
    }

    function applyQueryPrefill() {
        const params = new URLSearchParams(window.location.search);
        const topic = params.get('type') || params.get('topic');
        const area = params.get('area');
        const subject = params.get('subject');

        if (topic && Array.from(fields.topic.options).some((option) => option.value === topic)) {
            fields.topic.value = topic;
        }
        if (area && Array.from(fields.area.options).some((option) => option.value === area)) {
            fields.area.value = area;
        }
        if (subject && !fields.subject.value) {
            fields.subject.value = subject.slice(0, 160);
        }
        updateAreaGuidance();
    }

    messageInput.addEventListener('input', function () {
        messageCount.textContent = `${messageInput.value.length} / 5000`;
        if (messageInput.value.trim()) setError('message', '');
    });

    attachmentInput.addEventListener('change', function () {
        const file = attachmentInput.files?.[0];
        fileName.textContent = file ? file.name : 'Choose a file';
        setError('attachment', validateAttachment());
    });

    ['name', 'email', 'topic', 'subject'].forEach(function (fieldName) {
        fields[fieldName].addEventListener('input', () => setError(fieldName, ''));
        fields[fieldName].addEventListener('change', () => setError(fieldName, ''));
    });

    fields.area.addEventListener('change', function () {
        setError('area', '');
        updateAreaGuidance();
    });

    faqSearch?.addEventListener('input', filterFaqs);
    copyButton.addEventListener('click', copyRequest);
    form.addEventListener('submit', submitRequest);

    applyQueryPrefill();
})();
