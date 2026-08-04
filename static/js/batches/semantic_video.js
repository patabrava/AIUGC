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

    function reloadAtWorkflow(root) {
        const target = `#semantic-video-post-${encodeURIComponent(root.dataset.postId)}`;
        window.history.replaceState(
            null,
            '',
            `${window.location.pathname}${window.location.search}${target}`,
        );
        window.location.reload();
    }

    function settleCandidateAction(root) {
        root.dataset.waitingForCandidates = 'false';
        root.dataset.candidateGenerationStatus = 'ready';
        stopPolling(root);
        const siblingBusy = Array.from(
            document.querySelectorAll('[data-semantic-video-controller]'),
        ).some((candidateRoot) => (
            candidateRoot !== root
            && (
                candidateRoot.dataset.waitingForCandidates === 'true'
                || candidateRoot.dataset.candidateGenerationStatus === 'generating'
            )
        ));
        if (!siblingBusy) reloadAtWorkflow(root);
    }

    window.handleSemanticDeliveryDecision = function (event, postId) {
        if (!event.detail.successful) return;
        let payload = {};
        try {
            payload = JSON.parse(event.detail.xhr.responseText || '{}');
        } catch (_error) {
            payload = {};
        }
        const target = payload?.data?.batch_advanced
            ? '#publish-workflow'
            : `#semantic-video-post-${encodeURIComponent(postId)}`;
        window.history.replaceState(
            null,
            '',
            `${window.location.pathname}${window.location.search}${target}`,
        );
        window.location.reload();
    };

    document.addEventListener('htmx:afterRequest', (event) => {
        const trigger = event.detail?.elt || event.target;
        const decision = trigger?.closest?.('[data-semantic-delivery-post-id]');
        if (!decision) return;
        window.handleSemanticDeliveryDecision(
            event,
            decision.dataset.semanticDeliveryPostId,
        );
    });

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', ...(options.headers || {})},
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const message = payload?.error?.message || payload?.message || payload?.detail || 'Semantic video request failed.';
            const error = new Error(typeof message === 'string' ? message : 'Semantic video request failed.');
            error.status = response.status;
            throw error;
        }
        return payload.data || payload;
    }

    function exactCostConfirmation(button, kind) {
        const cost = button.getAttribute('data-cost-usd');
        return window.confirm(`Approve ${kind} at the exact incremental cost of $${cost}? This may submit paid Veo work.`);
    }

    function setBatchPlanStatus(workflow, message, isError = false) {
        const target = workflow.querySelector('[data-field="batch-plan-status"]');
        if (!target) return;
        target.textContent = message;
        target.classList.remove('hidden');
        target.classList.toggle('text-red-700', isError);
        target.classList.toggle('text-[#1C2740]', !isError);
    }

    async function approveReadyBatchPlans(workflow, button) {
        const expectedCount = Number(button.dataset.readyCount || 0);
        const cost = button.dataset.costUsd || '0.00';
        if (!window.confirm(
            `Approve all ${expectedCount} ready videos at the exact combined cost of $${cost}? This may submit paid Veo work.`,
        )) return;

        const roots = Array.from(workflow.querySelectorAll('[data-semantic-video-controller]'))
            .filter((root) => {
                const approveButton = action(root, 'approve-plan');
                return root.dataset.stage === 'awaiting_paid_approval'
                    && Boolean(root.dataset.planHash)
                    && approveButton
                    && !approveButton.disabled;
            });
        const feedbackState = window.beginActionFeedback(button, `Starting ${expectedCount} videos…`);
        button.disabled = true;
        roots.forEach((root) => {
            const approveButton = action(root, 'approve-plan');
            if (approveButton) approveButton.disabled = true;
        });

        try {
            if (roots.length !== expectedCount) {
                throw new Error('The ready-video count changed. Reload the current production plans.');
            }

            const approvals = [];
            for (const root of roots) {
                const progress = await requestJson(
                    `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`,
                    {method: 'GET'},
                );
                updateProgress(root, progress);
                if (progress.stage !== 'awaiting_paid_approval') {
                    throw new Error('A production plan changed before batch approval. Reload the current plans.');
                }
                if (String(progress.plan_hash || '') !== String(root.dataset.planHash || '')) {
                    throw new Error('A production plan hash changed before batch approval. Reload the current plans.');
                }
                approvals.push({
                    root,
                    post_id: root.dataset.postId,
                    expected_revision: Number(progress.revision || 0),
                    plan_hash: progress.plan_hash,
                });
            }

            const result = await requestJson(
                `/semantic-videos/batches/${encodeURIComponent(workflow.dataset.batchId)}/approve`,
                {
                    method: 'POST',
                    body: JSON.stringify({
                        approvals: approvals.map(({post_id, plan_hash, expected_revision}) => ({
                            post_id,
                            plan_hash,
                            expected_revision,
                        })),
                        reason: null,
                    }),
                },
            );
            if (Number(result.approval_count || 0) !== expectedCount) {
                throw new Error('The server did not approve the complete ready batch. Reload the current plans.');
            }
            setBatchPlanStatus(workflow, `Started all ${expectedCount} videos.`);
            window.location.reload();
        } catch (error) {
            window.endActionFeedback(button, feedbackState);
            button.disabled = false;
            roots.forEach((root) => {
                const approveButton = action(root, 'approve-plan');
                if (approveButton) approveButton.disabled = false;
            });
            setBatchPlanStatus(workflow, error.message, true);
        }
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
        const progressBar = field(root, 'progress-bar');
        const progressPercent = field(root, 'progress-percent');
        const elapsed = field(root, 'elapsed');
        const remaining = field(root, 'remaining');
        const progressMessage = field(root, 'progress-message');
        const progressSpinner = field(root, 'progress-spinner');
        const isBusy = progress.candidate_generation_status === 'generating'
            || ['generating', 'transcript_qa', 'identity_qa', 'voice_qa', 'acoustic_qa', 'composing', 'captioning', 'uploading'].includes(progress.stage);
        updateCandidateStatus(root, progress);
        updateStatStatus(root, progress);
        if (progressBar) {
            progressBar.style.width = `${progress.progress_percent}%`;
            progressBar.setAttribute('aria-valuenow', String(progress.progress_percent));
        }
        if (progressPercent) progressPercent.textContent = `${progress.progress_percent}%`;
        if (elapsed) elapsed.textContent = formatDuration(progress.elapsed_seconds);
        if (remaining) {
            remaining.textContent = progress.estimated_remaining_seconds === null
                ? 'Not available'
                : (progress.estimated_remaining_seconds === 0
                    ? 'Less than a minute'
                    : `About ${formatDuration(progress.estimated_remaining_seconds)}`);
        }
        if (progressMessage) progressMessage.textContent = progress.status_message;
        if (progressSpinner) progressSpinner.classList.toggle('hidden', !isBusy);
        root.setAttribute('aria-busy', String(isBusy));
        root.dataset.revision = progress.revision;
        root.dataset.stage = progress.stage;
        root.dataset.runId = progress.run_id || '';
        if (progress.plan_hash) root.dataset.planHash = progress.plan_hash;
        root.dataset.candidateGenerationStatus = progress.candidate_generation_status || 'idle';
        root.dataset.candidateGenerationPhase = progress.candidate_generation_phase || '';
    }

    function updateCandidateStatus(root, progress) {
        const panel = field(root, 'candidate-progress');
        if (!panel) return;
        const status = String(progress.candidate_generation_status || 'idle');
        const phase = String(progress.candidate_generation_phase || '');
        const labels = {
            preparing_references: 'Preparing references',
            generating_images: 'Generating script image',
            checking_diversity: 'Checking image composition',
            regenerating_duplicates: 'Repairing image composition',
            checking_identity: 'Verifying actor identity',
            saving_candidates: 'Saving verified image',
            failed: 'Generation stopped — retry safely',
            ready: 'Ready for identity review',
        };
        const label = field(root, 'candidate-status-label');
        const detail = field(root, 'candidate-status-detail');
        const spinner = field(root, 'candidate-spinner');
        const percent = field(root, 'candidate-percent');
        const progressBar = field(root, 'candidate-progress-bar');
        const isGenerating = status === 'generating';
        const isVisible = isGenerating || status === 'ready' || status === 'stalled';

        panel.classList.toggle('hidden', !isVisible);
        if (!isVisible) return;
        panel.classList.toggle('border-amber-300', status === 'stalled');
        panel.classList.toggle('bg-amber-50', status === 'stalled');
        if (label) {
            label.textContent = status === 'stalled'
                ? 'Generation needs attention'
                : (labels[phase] || (status === 'ready' ? labels.ready : 'Preparing scene plates'));
        }
        if (detail) detail.textContent = progress.status_message || '';
        if (spinner) spinner.classList.toggle('hidden', !isGenerating);
        if (percent) percent.textContent = `${progress.progress_percent}%`;
        if (progressBar) {
            progressBar.style.width = `${progress.progress_percent}%`;
            progressBar.setAttribute('aria-valuenow', String(progress.progress_percent));
        }
    }

    function updateStatStatus(root, progress) {
        const total = Number(progress.total_takes || 0);
        const generatedCount = Number(progress.generated_takes || 0);
        const verifiedCount = Number(progress.verified_takes || 0);
        const stage = String(progress.stage || '');
        const blocked = ['failed', 'retry_approval_required'].includes(stage);
        const generationBusy = stage === 'generating';
        const verificationBusy = ['transcript_qa', 'identity_qa', 'voice_qa', 'acoustic_qa', 'composing', 'captioning', 'uploading'].includes(stage);

        const generatedStatus = field(root, 'generated-status');
        const generatedSpinner = field(root, 'generated-spinner');
        const verifiedStatus = field(root, 'verified-status');
        const verifiedSpinner = field(root, 'verified-spinner');

        setStatStatus(generatedStatus, generatedSpinner, {
            text: blocked ? 'Needs review' : (total && generatedCount >= total ? 'Complete' : (generationBusy ? 'Generating' : 'Queued')),
            tone: blocked ? 'attention' : (total && generatedCount >= total ? 'complete' : (generationBusy ? 'active' : 'pending')),
            loading: generationBusy && !(total && generatedCount >= total),
        });
        setStatStatus(verifiedStatus, verifiedSpinner, {
            text: blocked ? 'Needs review' : (total && verifiedCount >= total ? 'Complete' : (verificationBusy ? 'Verifying' : (generationBusy ? 'Waiting for takes' : 'Queued'))),
            tone: blocked ? 'attention' : (total && verifiedCount >= total ? 'complete' : (verificationBusy ? 'active' : 'pending')),
            loading: verificationBusy && !(total && verifiedCount >= total),
        });
    }

    function setStatStatus(target, spinner, next) {
        if (target) {
            target.childNodes.forEach((node) => {
                if (node.nodeType === Node.TEXT_NODE) node.textContent = '';
            });
            target.append(document.createTextNode(next.text));
            target.classList.toggle('text-emerald-800', next.tone === 'complete');
            target.classList.toggle('text-[#006AAB]', next.tone === 'active');
            target.classList.toggle('text-amber-800', next.tone === 'attention');
            target.classList.toggle('text-[#1C2740]/55', next.tone === 'pending');
        }
        if (spinner) spinner.classList.toggle('hidden', !next.loading);
    }

    function showCandidateLoading(root) {
        const candidatePanel = field(root, 'candidate-progress');
        const candidateLabel = field(root, 'candidate-status-label');
        const candidateDetail = field(root, 'candidate-status-detail');
        const candidateSpinner = field(root, 'candidate-spinner');
        const candidatePercent = field(root, 'candidate-percent');
        const candidateProgressBar = field(root, 'candidate-progress-bar');
        const progressBar = field(root, 'progress-bar');
        const progressPercent = field(root, 'progress-percent');
        const progressMessage = field(root, 'progress-message');
        const progressSpinner = field(root, 'progress-spinner');
        const elapsed = field(root, 'elapsed');
        const remaining = field(root, 'remaining');
        root.dataset.candidateGenerationStatus = 'generating';
        root.dataset.candidateGenerationPhase = 'preparing_references';
        root.setAttribute('aria-busy', 'true');
        if (candidatePanel) candidatePanel.classList.remove('hidden');
        if (candidateLabel) candidateLabel.textContent = 'Preparing references';
        if (candidateDetail) candidateDetail.textContent = 'Loading and verifying the actor and location references.';
        if (candidateSpinner) candidateSpinner.classList.remove('hidden');
        if (candidatePercent) candidatePercent.textContent = '5%';
        if (candidateProgressBar) {
            candidateProgressBar.style.width = '5%';
            candidateProgressBar.setAttribute('aria-valuenow', '5');
        }
        if (progressBar) {
            progressBar.style.width = '5%';
            progressBar.setAttribute('aria-valuenow', '5');
        }
        if (progressPercent) progressPercent.textContent = '5%';
        if (progressMessage) {
            progressMessage.textContent = 'Loading and verifying the actor and location references.';
        }
        if (progressSpinner) progressSpinner.classList.remove('hidden');
        if (elapsed) elapsed.textContent = '0s';
        if (remaining) remaining.textContent = 'Calculating…';
    }

    function setPlanStatLoading(root, name, isLoading) {
        const target = field(root, `plan-${name}-status`);
        const spinner = field(root, `plan-${name}-spinner`);
        if (target) {
            target.childNodes.forEach((node) => {
                if (node.nodeType === Node.TEXT_NODE) node.textContent = '';
            });
            target.append(document.createTextNode(isLoading ? 'Calculating' : 'Pending plan'));
            target.classList.toggle('text-[#006AAB]', isLoading);
            target.classList.toggle('text-[#1C2740]/55', !isLoading);
        }
        if (spinner) spinner.classList.toggle('hidden', !isLoading);
    }

    function showPlanLoading(root) {
        const panel = field(root, 'plan-progress');
        const spinner = field(root, 'plan-spinner');
        const label = field(root, 'plan-status-label');
        const badge = field(root, 'plan-status-badge');
        const detail = field(root, 'plan-status-detail');
        if (panel) {
            panel.classList.remove('hidden', 'border-amber-300', 'bg-amber-50');
        }
        if (spinner) spinner.classList.remove('hidden');
        if (label) label.textContent = 'Building production plan';
        if (badge) badge.textContent = 'In progress';
        if (detail) {
            detail.textContent = 'Validating the approved scene, calculating takes and provider seconds, and preparing the exact cost.';
        }
        ['takes', 'seconds', 'cost'].forEach((name) => setPlanStatLoading(root, name, true));
        root.setAttribute('aria-busy', 'true');
    }

    function showPlanError(root, message) {
        const panel = field(root, 'plan-progress');
        const spinner = field(root, 'plan-spinner');
        const label = field(root, 'plan-status-label');
        const badge = field(root, 'plan-status-badge');
        const detail = field(root, 'plan-status-detail');
        if (panel) panel.classList.add('border-amber-300', 'bg-amber-50');
        if (spinner) spinner.classList.add('hidden');
        if (label) label.textContent = 'Plan could not be built';
        if (badge) badge.textContent = 'Needs attention';
        if (detail) detail.textContent = message;
        ['takes', 'seconds', 'cost'].forEach((name) => setPlanStatLoading(root, name, false));
        root.setAttribute('aria-busy', 'false');
    }

    function formatDuration(value) {
        const seconds = Math.max(0, Number(value) || 0);
        if (seconds < 60) return `${Math.round(seconds)}s`;
        const minutes = Math.floor(seconds / 60);
        const remainder = Math.round(seconds % 60);
        return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
    }

    async function pollProgress(root) {
        const terminalWithoutImageRecovery = (
            ['completed', 'failed'].includes(root.dataset.stage)
            && root.dataset.waitingForCandidates !== 'true'
        );
        if (!root.isConnected || terminalWithoutImageRecovery) return;
        try {
            const postId = root.dataset.postId;
            const progress = await requestJson(`/semantic-videos/posts/${encodeURIComponent(postId)}/progress`, {method: 'GET'});
            updateProgress(root, progress);
            const requestAdvanced = (
                String(progress.run_id || '') !== String(root.dataset.candidateBaselineRunId || '')
                || String(progress.revision ?? '') !== String(root.dataset.candidateBaselineRevision || '')
            );
            if (
                root.dataset.waitingForCandidates === 'true'
                && progress.candidate_generation_status === 'ready'
                && progress.stage === 'awaiting_reference_approval'
                && requestAdvanced
            ) {
                settleCandidateAction(root);
                return;
            }
            if (progress.stage === 'retry_approval_required' || progress.stage === 'completed') {
                window.location.reload();
            }
        } catch (error) {
            if (root.dataset.stage !== 'not_started') setStatus(root, error.message, true);
        }
    }

    async function recoverCandidateProgress(root) {
        if (!field(root, 'candidate-progress')) return;
        try {
            const progress = await requestJson(`/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`, {method: 'GET'});
            updateProgress(root, progress);
            if (progress.candidate_generation_status === 'generating') {
                root.dataset.waitingForCandidates = 'true';
                startPolling(root, true, false);
            }
        } catch (_error) {
            // The ordinary action status remains the error surface for explicit user actions.
        }
    }

    function stopPolling(root) {
        const timer = activePolls.get(root);
        if (!timer) return;
        window.clearInterval(timer);
        activePolls.delete(root);
    }

    function startPolling(root, force = false, immediate = true) {
        if (activePolls.has(root) || (!force && ['not_started', 'awaiting_reference_approval', 'awaiting_paid_approval', 'retry_approval_required', 'completed', 'failed'].includes(root.dataset.stage))) return;
        const timer = window.setInterval(() => pollProgress(root), force ? 2000 : 8000);
        activePolls.set(root, timer);
        if (immediate) pollProgress(root);
    }

    async function reconcileMasterApproval(root) {
        for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
                const progress = await requestJson(`/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`, {method: 'GET'});
                updateProgress(root, progress);
                if (progress.stage !== 'awaiting_reference_approval') {
                    reloadAtWorkflow(root);
                    return true;
                }
            } catch (_error) {
                // Preserve the original approval error when persisted state is unavailable.
            }
            if (attempt < 2) {
                await new Promise((resolve) => window.setTimeout(resolve, 750));
            }
        }
        return false;
    }

    async function synchronizePaidAction(root, path, body) {
        const progress = await requestJson(
            `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`,
            {method: 'GET'},
        );
        updateProgress(root, progress);
        const expectedStage = path === 'approve' ? 'awaiting_paid_approval' : 'retry_approval_required';
        if (progress.stage !== expectedStage) {
            reloadAtWorkflow(root);
            return null;
        }
        if (String(progress.plan_hash || '') !== String(body.plan_hash || '')) {
            throw new Error('Semantic video approval hash is stale. Reload the current production plan.');
        }
        return {...body, expected_revision: Number(progress.revision || 0)};
    }

    async function reconcilePaidAction(root, path) {
        try {
            const progress = await requestJson(
                `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`,
                {method: 'GET'},
            );
            updateProgress(root, progress);
            const expectedStage = path === 'approve' ? 'awaiting_paid_approval' : 'retry_approval_required';
            if (progress.stage !== expectedStage) {
                reloadAtWorkflow(root);
                return true;
            }
        } catch (_error) {
            // Preserve the original approval error when persisted state is unavailable.
        }
        return false;
    }

    async function runAction(root, button, path, body, pendingMessage) {
        const pendingLabels = {
            candidates: 'Generating scene plates…',
            'scene-image': 'Generating script image…',
            'master-approve': 'Approving scene plate…',
            plan: 'Building plan…',
            approve: 'Starting generation…',
            'retry-approve': 'Starting retry…',
            'qa-resume': 'Continuing QA…',
        };
        const pendingLabel = pendingLabels[path] || 'Working…';
        const feedbackState = window.beginActionFeedback(button, pendingLabel);
        button.disabled = true;
        if (!['candidates', 'scene-image', 'plan'].includes(path)) setStatus(root, pendingMessage);
        try {
            const isSceneImageAction = path === 'scene-image';
            if (['approve', 'retry-approve'].includes(path)) {
                body = await synchronizePaidAction(root, path, body);
                if (!body) return;
            }
            let result = null;
            const maxAttempts = 1;
            for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
                try {
                    result = await requestJson(`/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/${path}`, {
                        method: 'POST',
                        body: JSON.stringify(body),
                    });
                    break;
                } catch (error) {
                    if (!isSceneImageAction || attempt + 1 >= maxAttempts) throw error;
                    await pollProgress(root);
                    if (root.dataset.candidateGenerationStatus === 'generating') {
                        return;
                    }
                    body = {
                        ...body,
                        expected_revision: Number(root.dataset.revision || 0),
                    };
                    await new Promise((resolve) => window.setTimeout(resolve, 500));
                }
            }
            if (isSceneImageAction) {
                root.dataset.runId = result.job_id || root.dataset.runId || '';
                root.dataset.revision = String(result.revision ?? root.dataset.revision ?? '');
                root.dataset.candidateGenerationStatus = 'generating';
                startPolling(root, true, true);
                window.endActionFeedback(button, feedbackState);
                return;
            }
            reloadAtWorkflow(root);
        } catch (error) {
            if (path === 'master-approve' && await reconcileMasterApproval(root)) {
                return;
            }
            if (
                ['approve', 'retry-approve'].includes(path)
                && error.status === 409
                && await reconcilePaidAction(root, path)
            ) {
                return;
            }
            if (isSceneImageAction) {
                await pollProgress(root);
                if (root.dataset.candidateGenerationStatus === 'generating') {
                    return;
                }
                root.dataset.waitingForCandidates = 'false';
                stopPolling(root);
            }
            if (path === 'plan') showPlanError(root, error.message);
            window.endActionFeedback(button, feedbackState);
            button.disabled = false;
            if (path !== 'plan') setStatus(root, error.message, true);
        }
    }

    function openIdentityComparison(workflow, trigger) {
        const dialog = workflow.querySelector('[data-identity-compare-dialog]');
        if (!dialog) return;
        const root = trigger.closest('[data-semantic-video-controller]');
        const frontUri = trigger.dataset.actorFrontUri || root?.dataset.actorFrontUri || '';
        const threeQuarterUri = trigger.dataset.actorThreeQuarterUri || root?.dataset.actorThreeQuarterUri || '';
        if (!frontUri || !threeQuarterUri) return;

        const frontImage = dialog.querySelector('[data-compare-front]');
        const threeQuarterImage = dialog.querySelector('[data-compare-three-quarter]');
        const candidateImage = dialog.querySelector('[data-compare-candidate]');
        const candidateFigure = dialog.querySelector('[data-compare-candidate-figure]');
        const candidateLabel = dialog.querySelector('[data-compare-candidate-label]');
        const summary = dialog.querySelector('[data-compare-summary]');
        const candidateUri = trigger.dataset.candidateUri || '';

        frontImage.src = frontUri;
        threeQuarterImage.src = threeQuarterUri;
        candidateFigure.hidden = !candidateUri;
        if (candidateUri) {
            candidateImage.src = candidateUri;
            candidateLabel.textContent = trigger.dataset.candidateLabel || 'Scene plate candidate';
            summary.textContent = trigger.dataset.gateSummary || 'Compare the candidate directly with both immutable actor references.';
        } else {
            candidateImage.removeAttribute('src');
            summary.textContent = 'Review the immutable front and three-quarter references used for this scene review.';
        }
        dialog.showModal();
    }

    function bindIdentityComparison(workflow) {
        const dialog = workflow.querySelector('[data-identity-compare-dialog]');
        if (!dialog) return;
        workflow.addEventListener('click', (event) => {
            const trigger = event.target.closest('[data-action="compare-candidate"], [data-action="compare-references"]');
            if (trigger) openIdentityComparison(workflow, trigger);
        });
        dialog.querySelector('[data-action="close-identity-compare"]')?.addEventListener('click', () => dialog.close());
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) dialog.close();
        });
    }

    function bind(root) {
        if (root.dataset.semanticBound === 'true') return;
        root.dataset.semanticBound = 'true';
        const revision = () => Number(root.dataset.revision || 0);
        const approvalButton = action(root, 'approve-master');
        const updateMasterApprovalState = () => {
            if (!approvalButton || root.dataset.stage !== 'awaiting_reference_approval') return;
            const selected = root.querySelector('input[type="radio"][data-identity-passed="true"]:checked');
            approvalButton.disabled = !selected;
        };
        root.querySelectorAll('input[type="radio"][data-identity-passed="true"]').forEach((input) => {
            input.addEventListener('change', updateMasterApprovalState);
        });
        updateMasterApprovalState();

        action(root, 'generate-candidates')?.addEventListener('click', (event) => {
            const expected = root.dataset.revision === '' ? null : revision();
            root.dataset.candidateBaselineRunId = root.dataset.runId || '';
            root.dataset.candidateBaselineRevision = root.dataset.revision || '';
            root.dataset.waitingForCandidates = 'true';
            showCandidateLoading(root);
            startPolling(root, true, false);
            runAction(root, event.currentTarget, 'scene-image', {expected_revision: expected}, 'Queueing script-image generation…');
        });
        action(root, 'approve-master')?.addEventListener('click', (event) => {
            const selected = root.querySelector('input[type="radio"][data-identity-passed="true"]:checked');
            if (!selected) return setStatus(root, 'Select a candidate whose identity gate passed.', true);
            runAction(root, event.currentTarget, 'master-approve', {
                candidate_index: Number(selected.value),
                expected_revision: revision(),
                identity_attestation: true,
                attestation_version: 'semantic-actor-identity-v1',
                reason: null,
            }, 'Approving the selected scene plate and confirming identity…');
        });
        action(root, 'create-plan')?.addEventListener('click', (event) => {
            showPlanLoading(root);
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
        action(root, 'resume-qa')?.addEventListener('click', (event) => {
            runAction(root, event.currentTarget, 'qa-resume', {
                plan_hash: root.dataset.planHash,
                expected_revision: revision(),
            }, 'Continuing with the existing generated videos at no additional Veo cost…');
        });
        recoverCandidateProgress(root);
        startPolling(root);
    }

    function init(scope = document) {
        scope.querySelectorAll('[data-semantic-video-controller]').forEach(bind);
        scope.querySelectorAll('[data-semantic-video-workflow]').forEach((workflow) => {
            if (workflow.dataset.semanticBatchBound === 'true') return;
            workflow.dataset.semanticBatchBound = 'true';
            bindIdentityComparison(workflow);
            const button = workflow.querySelector('[data-action="approve-batch-plans"]');
            button?.addEventListener('click', () => approveReadyBatchPlans(workflow, button));
        });
    }

    document.addEventListener('DOMContentLoaded', () => init());
    document.addEventListener('htmx:afterSwap', (event) => init(event.target));
})();
