(() => {
    const activePolls = new WeakMap();

    const field = (root, name) => root.querySelector(`[data-field="${name}"]`);
    const action = (root, name) => root.querySelector(`[data-action="${name}"]`);

    function setStatus(root, message, isError = false) {
        const target = field(root, 'status');
        if (!target) return;
        target.textContent = message;
        target.classList.toggle('text-red-700', isError);
    }

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', ...(options.headers || {})},
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const message = payload?.error?.message || payload?.detail || 'Semantic video request failed.';
            throw new Error(typeof message === 'string' ? message : 'Semantic video request failed.');
        }
        return payload.data || payload;
    }

    function exactCostConfirmation(button, kind) {
        const cost = button.getAttribute('data-cost-usd');
        return window.confirm(`Approve ${kind} at the exact incremental cost of $${cost}? This may submit paid Veo work.`);
    }

    function updateProgress(root, progress) {
        const stage = field(root, 'stage');
        if (stage) stage.textContent = String(progress.stage || '').replaceAll('_', ' ');
        const generated = field(root, 'generated-takes');
        const verified = field(root, 'verified-takes');
        const total = field(root, 'total-takes');
        const verifiedTotal = field(root, 'verified-total');
        if (generated) generated.textContent = progress.generated_takes;
        if (verified) verified.textContent = progress.verified_takes;
        if (total) total.textContent = progress.total_takes;
        if (verifiedTotal) verifiedTotal.textContent = progress.total_takes;
        root.dataset.revision = progress.revision;
        root.dataset.stage = progress.stage;
        if (progress.plan_hash) root.dataset.planHash = progress.plan_hash;
    }

    async function pollProgress(root) {
        if (!root.isConnected || root.dataset.stage === 'completed' || root.dataset.stage === 'failed') return;
        try {
            const postId = root.dataset.postId;
            const progress = await requestJson(`/semantic-videos/posts/${encodeURIComponent(postId)}/progress`, {method: 'GET'});
            updateProgress(root, progress);
            if (progress.stage === 'retry_approval_required' || progress.stage === 'completed') {
                window.location.reload();
            }
        } catch (error) {
            if (root.dataset.stage !== 'not_started') setStatus(root, error.message, true);
        }
    }

    function startPolling(root) {
        if (activePolls.has(root) || ['not_started', 'awaiting_reference_approval', 'awaiting_paid_approval', 'retry_approval_required', 'completed', 'failed'].includes(root.dataset.stage)) return;
        const timer = window.setInterval(() => pollProgress(root), 8000);
        activePolls.set(root, timer);
        pollProgress(root);
    }

    async function runAction(root, button, path, body, pendingMessage) {
        button.disabled = true;
        setStatus(root, pendingMessage);
        try {
            await requestJson(`/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/${path}`, {
                method: 'POST',
                body: JSON.stringify(body),
            });
            window.location.reload();
        } catch (error) {
            button.disabled = false;
            setStatus(root, error.message, true);
        }
    }

    function bind(root) {
        if (root.dataset.semanticBound === 'true') return;
        root.dataset.semanticBound = 'true';
        const revision = () => Number(root.dataset.revision || 0);

        action(root, 'generate-candidates')?.addEventListener('click', (event) => {
            const expected = root.dataset.revision === '' ? null : revision();
            runAction(root, event.currentTarget, 'candidates', {candidate_count: 3, expected_revision: expected}, 'Locking the approved actor reference as the canonical master…');
        });
        action(root, 'approve-master')?.addEventListener('click', (event) => {
            const selected = root.querySelector('input[type="radio"]:checked');
            if (!selected) return setStatus(root, 'Select one master candidate first.', true);
            runAction(root, event.currentTarget, 'master-approve', {candidate_index: Number(selected.value), expected_revision: revision(), reason: null}, 'Approving master frame…');
        });
        action(root, 'create-plan')?.addEventListener('click', (event) => {
            runAction(root, event.currentTarget, 'plan', {expected_revision: revision(), base_seed: 240713, resolution: '1080p'}, 'Building the free deterministic plan…');
        });
        action(root, 'approve-plan')?.addEventListener('click', (event) => {
            const button = event.currentTarget;
            if (!exactCostConfirmation(button, 'the initial plan')) return;
            runAction(root, button, 'approve', {plan_hash: root.dataset.planHash, expected_revision: revision(), reason: null}, 'Persisting paid-plan approval…');
        });
        action(root, 'approve-retry')?.addEventListener('click', (event) => {
            const button = event.currentTarget;
            if (!exactCostConfirmation(button, 'only the failed takes')) return;
            const failed = (button.dataset.failedIndexes || '').split(',').filter(Boolean).map(Number);
            runAction(root, button, 'retry-approve', {plan_hash: root.dataset.planHash, expected_revision: revision(), failed_take_indexes: failed, reason: null}, 'Persisting failed-take retry approval…');
        });
        startPolling(root);
    }

    function init(scope = document) {
        scope.querySelectorAll('[data-semantic-video-controller]').forEach(bind);
    }

    document.addEventListener('DOMContentLoaded', () => init());
    document.addEventListener('htmx:afterSwap', (event) => init(event.target));
})();
