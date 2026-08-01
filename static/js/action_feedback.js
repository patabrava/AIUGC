(() => {
    const pendingButtons = new WeakMap();

    function spinner() {
        const icon = document.createElement('span');
        icon.className = 'action-feedback-spinner';
        icon.setAttribute('aria-hidden', 'true');
        return icon;
    }

    function pendingLabel(button, fallback = 'Working…') {
        return String(button?.dataset?.pendingLabel || fallback).trim() || fallback;
    }

    window.beginActionFeedback = function (button, label = 'Working…') {
        if (!(button instanceof HTMLButtonElement)) return null;
        const existing = pendingButtons.get(button);
        if (existing) return existing;

        const state = {
            disabled: button.disabled,
            html: button.innerHTML,
        };
        pendingButtons.set(button, state);
        button.dataset.actionPending = 'true';
        button.setAttribute('aria-busy', 'true');
        button.disabled = true;
        button.replaceChildren(spinner(), document.createTextNode(pendingLabel(button, label)));
        return state;
    };

    window.endActionFeedback = function (button, state = null) {
        if (!(button instanceof HTMLButtonElement)) return;
        const original = state || pendingButtons.get(button);
        if (!original) return;
        button.innerHTML = original.html;
        button.disabled = original.disabled;
        button.removeAttribute('data-action-pending');
        button.removeAttribute('aria-busy');
        pendingButtons.delete(button);
    };

    function initiatingButton(element) {
        if (element instanceof HTMLButtonElement) return element;
        if (element instanceof Element) {
            const closest = element.closest('button');
            if (closest) return closest;
            if (element instanceof HTMLFormElement) {
                return element.querySelector('button[type="submit"]');
            }
        }
        return null;
    }

    document.body.addEventListener('htmx:beforeRequest', (event) => {
        const button = initiatingButton(event.detail?.elt);
        if (!button) return;
        window.beginActionFeedback(
            button,
            pendingLabel(button, 'Saving…'),
        );
    });

    document.body.addEventListener('htmx:afterRequest', (event) => {
        const button = initiatingButton(event.detail?.elt);
        if (!button?.isConnected) return;
        window.endActionFeedback(button);
    });

    document.addEventListener('submit', (event) => {
        const button = event.submitter;
        if (!(button instanceof HTMLButtonElement) || button.hasAttribute('hx-post')) return;
        window.beginActionFeedback(button, pendingLabel(button, 'Submitting…'));
    }, true);

    window.addEventListener('pageshow', () => {
        document.querySelectorAll('button[data-action-pending="true"]').forEach((button) => {
            window.endActionFeedback(button);
        });
    });
})();
