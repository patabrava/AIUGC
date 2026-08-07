(() => {
    const activePolls = new WeakMap();
    const candidateProgressRequests = new WeakMap();
    const sceneImageButtonDomainDisabled = new WeakMap();
    const scheduledWorkflowReloads = new WeakSet();
    const SCENE_IMAGE_POST_TIMEOUT_MS = 15000;
    const RUN_PROGRESS_STAGES = [
        'generating',
        'transcript_qa',
        'identity_qa',
        'voice_qa',
        'acoustic_qa',
        'composing',
        'captioning',
        'uploading',
    ];

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

    function sceneImageWorkflow(root) {
        return root.closest('[data-semantic-video-workflow]') || document;
    }

    function rememberSceneImageButton(button) {
        if (button && !sceneImageButtonDomainDisabled.has(button)) {
            const declaredDomainState = button.dataset.sceneImageDomainDisabled;
            sceneImageButtonDomainDisabled.set(
                button,
                declaredDomainState === undefined
                    ? button.disabled
                    : declaredDomainState === 'true',
            );
        }
    }

    function syncSceneImageWorkflowGate(root) {
        const workflow = sceneImageWorkflow(root);
        const roots = Array.from(workflow.querySelectorAll('[data-semantic-video-controller]'));
        const workflowBusy = roots.some((candidateRoot) => (
            candidateRoot.dataset.waitingForCandidates === 'true'
            || candidateRoot.dataset.candidateGenerationStatus === 'generating'
        ));
        if (workflow?.dataset) {
            workflow.dataset.sceneImageBusy = String(workflowBusy);
        }
        roots.forEach((candidateRoot) => {
            const button = action(candidateRoot, 'generate-candidates');
            if (!button) return;
            rememberSceneImageButton(button);
            const domainDisabled = sceneImageButtonDomainDisabled.get(button) === true;
            button.disabled = domainDisabled || workflowBusy;
            button.dataset.sceneImageWorkflowBlocked = String(workflowBusy && !domainDisabled);
        });
        return workflowBusy;
    }

    function showCandidateTerminal(root, status, message, isError) {
        const candidatePanel = field(root, 'candidate-progress');
        const candidateLabel = field(root, 'candidate-status-label');
        const candidateDetail = field(root, 'candidate-status-detail');
        const candidateSpinner = field(root, 'candidate-spinner');
        const candidatePercent = field(root, 'candidate-percent');
        const candidateProgressBar = field(root, 'candidate-progress-bar');
        const progressSpinner = field(root, 'progress-spinner');
        const progressMessage = field(root, 'progress-message');
        const productionBusy = RUN_PROGRESS_STAGES.includes(root.dataset.stage);
        const needsAttention = status === 'idle' || status === 'stalled';

        root.setAttribute('aria-busy', String(productionBusy));
        if (candidatePanel) {
            candidatePanel.classList.remove('hidden');
            candidatePanel.classList.toggle('border-amber-300', needsAttention);
            candidatePanel.classList.toggle('bg-amber-50', needsAttention);
        }
        if (candidateLabel) {
            candidateLabel.textContent = needsAttention
                ? 'Generation needs attention'
                : 'Ready for identity review';
        }
        if (candidateDetail && message) candidateDetail.textContent = message;
        if (candidateSpinner) candidateSpinner.classList.add('hidden');
        if (!productionBusy && progressSpinner) progressSpinner.classList.add('hidden');
        if (!productionBusy && progressMessage && message) progressMessage.textContent = message;
        if (status === 'idle') {
            if (candidatePercent) candidatePercent.textContent = '0%';
            if (candidateProgressBar) {
                candidateProgressBar.style.width = '0%';
                candidateProgressBar.setAttribute('aria-valuenow', '0');
            }
        } else if (status === 'ready') {
            if (candidatePercent) candidatePercent.textContent = '100%';
            if (candidateProgressBar) {
                candidateProgressBar.style.width = '100%';
                candidateProgressBar.setAttribute('aria-valuenow', '100');
            }
        }
        if (message) setStatus(root, message, isError);
    }

    function finishCandidateAction(root, status, message = '', isError = false) {
        root.dataset.candidateStartPending = 'false';
        root.dataset.waitingForCandidates = 'false';
        root.dataset.candidateGenerationStatus = status;
        if (!RUN_PROGRESS_STAGES.includes(root.dataset.stage)) stopPolling(root);
        showCandidateTerminal(root, status, message, isError);
        const button = action(root, 'generate-candidates');
        if (button) window.endActionFeedback(button);
        const workflowBusy = syncSceneImageWorkflowGate(root);
        return workflowBusy;
    }

    function settleCandidateAction(root, shouldReload = true, message = '') {
        const workflowBusy = finishCandidateAction(root, 'ready', message);
        if (shouldReload && !workflowBusy) reloadAtWorkflow(root);
    }

    function expectedSceneImageRevision(root) {
        const hasActiveRun = (
            Boolean(root.dataset.runId)
            && !['completed', 'failed'].includes(root.dataset.stage)
        );
        return hasActiveRun ? Number(root.dataset.revision || 0) : null;
    }

    async function requestJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            cache: 'no-store',
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

    async function requestSceneImageStart(url, options) {
        const controller = new AbortController();
        const timeout = window.setTimeout(
            () => controller.abort(),
            SCENE_IMAGE_POST_TIMEOUT_MS,
        );
        try {
            return await requestJson(url, {...options, signal: controller.signal});
        } catch (error) {
            if (error.name !== 'AbortError') throw error;
            const timeoutError = new Error(
                'The script-image queue request timed out before the server confirmed it.',
            );
            timeoutError.code = 'scene_image_start_timeout';
            throw timeoutError;
        } finally {
            window.clearTimeout(timeout);
        }
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

    async function buildMissingPlans(workflow) {
        if (workflow.dataset.planBuildStarted === 'true') return;
        const roots = Array.from(workflow.querySelectorAll('[data-semantic-video-controller]'))
            .filter((root) => {
                const button = action(root, 'create-plan');
                return root.dataset.stage === 'awaiting_paid_approval'
                    && !root.dataset.planHash
                    && button
                    && !button.disabled;
            });
        if (!roots.length) return;

        workflow.dataset.planBuildStarted = 'true';
        setBatchPlanStatus(workflow, `Building ${roots.length} free production plan${roots.length === 1 ? '' : 's'}…`);
        const results = [];
        for (const root of roots) {
            const button = action(root, 'create-plan');
            const feedbackState = window.beginActionFeedback(button, 'Building plan…');
            button.disabled = true;
            showPlanLoading(root);
            try {
                const progress = await requestJson(
                    `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`,
                    {method: 'GET'},
                );
                updateProgress(root, progress);
                if (progress.plan_hash) {
                    results.push({ok: true, root, button, feedbackState});
                    continue;
                }
                if (progress.stage !== 'awaiting_paid_approval') {
                    throw new Error('The scene approval changed before its production plan could be built.');
                }
                await requestJson(
                    `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/plan`,
                    {
                        method: 'POST',
                        body: JSON.stringify({
                            expected_revision: Number(progress.revision || 0),
                            base_seed: 240713,
                            resolution: '1080p',
                        }),
                    },
                );
                results.push({ok: true, root, button, feedbackState});
            } catch (error) {
                window.endActionFeedback(button, feedbackState);
                button.disabled = false;
                showPlanError(root, error.message);
                results.push({ok: false, root, button, feedbackState});
            }
        }

        if (results.every((result) => result.ok)) {
            setBatchPlanStatus(workflow, `Built all ${roots.length} production plan${roots.length === 1 ? '' : 's'}.`);
            window.location.reload();
            return;
        }
        results.filter((result) => result.ok).forEach(({root, button, feedbackState}) => {
            window.endActionFeedback(button, feedbackState);
            setStatus(root, 'Production plan ready. Reload after resolving the remaining plan.');
            root.setAttribute('aria-busy', 'false');
        });
        setBatchPlanStatus(
            workflow,
            'At least one free production plan needs attention. The successful plans were preserved.',
            true,
        );
    }

    function updateProgress(root, progress) {
        if (progress.stage === 'completed' && Number(progress.total_takes || 0) > 0) {
            progress = {...progress, verified_takes: progress.total_takes};
        }
        const stage = field(root, 'stage');
        const candidateStatus = String(progress.candidate_generation_status || 'idle');
        const candidatePhase = String(progress.candidate_generation_phase || '');
        const sceneImageStageLabels = {
            scene_image_queued: 'image queued',
            scene_image_generating: 'generating image',
            scene_image_failed: 'image generation failed',
        };
        const displayStage = sceneImageStageLabels[progress.stage]
            || (
                progress.stage === 'not_started'
                && candidateStatus === 'generating'
                ? (candidatePhase === 'preparing_references' ? 'image queued' : 'generating image')
                : String(progress.stage || '').replaceAll('_', ' ')
            );
        if (stage) stage.textContent = displayStage;
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
            || RUN_PROGRESS_STAGES.includes(progress.stage);
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
        root.dataset.revision = String(progress.revision ?? '');
        root.dataset.stage = String(progress.stage || '');
        root.dataset.runId = String(progress.run_id || '');
        root.dataset.sceneImageJobId = String(progress.scene_image_job_id || '');
        if (progress.plan_hash) root.dataset.planHash = progress.plan_hash;
        root.dataset.candidateGenerationStatus = candidateStatus;
        root.dataset.candidateGenerationPhase = candidatePhase;
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
        const verificationBusy = RUN_PROGRESS_STAGES.includes(stage) && stage !== 'generating';

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

    function candidateRequestAdvanced(root, progress) {
        return (
            String(progress.run_id || '') !== String(root.dataset.candidateBaselineRunId || '')
            || String(progress.revision ?? '') !== String(root.dataset.candidateBaselineRevision || '')
            || String(progress.scene_image_job_id || '') !== String(root.dataset.candidateBaselineJobId || '')
        );
    }

    function hasSceneImageSurface(root) {
        return Boolean(field(root, 'candidate-progress') || action(root, 'generate-candidates'));
    }

    function advanceCandidateActionEpoch(root) {
        const nextEpoch = Number(root.dataset.candidateActionEpoch || 0) + 1;
        root.dataset.candidateActionEpoch = String(nextEpoch);
        return String(nextEpoch);
    }

    async function fetchCurrentProgress(root) {
        const epoch = String(root.dataset.candidateActionEpoch || '0');
        const activeRequest = candidateProgressRequests.get(root);
        if (activeRequest?.epoch === epoch) return activeRequest.promise;

        const request = requestJson(
            `/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/progress`,
            {method: 'GET'},
        ).then((progress) => (
            String(root.dataset.candidateActionEpoch || '0') === epoch ? progress : null
        ));
        const tracked = {epoch, promise: request};
        candidateProgressRequests.set(root, tracked);
        try {
            return await request;
        } finally {
            if (candidateProgressRequests.get(root) === tracked) {
                candidateProgressRequests.delete(root);
            }
        }
    }

    function applyCandidateProgress(
        root,
        progress,
        {settleTerminals = true, reloadReady = false} = {},
    ) {
        const status = String(progress.candidate_generation_status || 'idle');
        const wasWaiting = root.dataset.waitingForCandidates === 'true';
        const startPending = root.dataset.candidateStartPending === 'true';
        const requestAdvanced = candidateRequestAdvanced(root, progress);
        if (status === 'generating') {
            root.dataset.candidateStartPending = 'false';
            root.dataset.waitingForCandidates = 'true';
            syncSceneImageWorkflowGate(root);
            return status;
        }
        if (!settleTerminals) return status;
        if (status === 'ready') {
            const isCurrentReady = (
                progress.stage === 'awaiting_reference_approval'
                && requestAdvanced
            );
            if (startPending && !isCurrentReady) {
                syncSceneImageWorkflowGate(root);
                return status;
            }
            const shouldReload = (
                reloadReady
                && isCurrentReady
                && (wasWaiting || !root.querySelector('[data-identity-passed]'))
            );
            settleCandidateAction(root, shouldReload, progress.status_message || 'The script image is ready for identity review.');
            return status;
        }
        if (status === 'stalled') {
            if (startPending && !requestAdvanced) {
                syncSceneImageWorkflowGate(root);
                return status;
            }
            finishCandidateAction(
                root,
                status,
                progress.status_message || 'Script-image generation stopped. Retry generation safely.',
                true,
            );
            return status;
        }
        if (status === 'idle' && wasWaiting && !startPending) {
            finishCandidateAction(
                root,
                status,
                'Script-image generation did not start. Retry generation safely.',
                true,
            );
            return status;
        }
        syncSceneImageWorkflowGate(root);
        return status;
    }

    async function pollProgress(root, {settleCandidateTerminals = true} = {}) {
        const terminalWithoutImageRecovery = (
            ['completed', 'failed'].includes(root.dataset.stage)
            && root.dataset.waitingForCandidates !== 'true'
        );
        if (!root.isConnected) {
            stopPolling(root);
            return null;
        }
        if (terminalWithoutImageRecovery) return null;
        try {
            const progress = await fetchCurrentProgress(root);
            if (!root.isConnected) {
                stopPolling(root);
                return null;
            }
            if (!progress) return null;
            updateProgress(root, progress);
            const managesSceneImage = hasSceneImageSurface(root);
            const candidateStatus = managesSceneImage
                ? applyCandidateProgress(root, progress, {
                    settleTerminals: settleCandidateTerminals,
                    reloadReady: true,
                })
                : String(progress.candidate_generation_status || 'idle');
            if (progress.stage === 'retry_approval_required' || progress.stage === 'completed') {
                stopPolling(root);
                reloadWhenProductionSettled(root);
                return progress;
            }
            if (managesSceneImage && ['ready', 'stalled'].includes(candidateStatus)) {
                return progress;
            }
            return progress;
        } catch (error) {
            if (root.dataset.stage !== 'not_started') setStatus(root, error.message, true);
            return null;
        }
    }

    async function recoverCandidateProgress(root) {
        if (!field(root, 'candidate-progress')) return;
        try {
            const progress = await fetchCurrentProgress(root);
            if (!root.isConnected || !progress) return;
            updateProgress(root, progress);
            const status = applyCandidateProgress(root, progress, {
                reloadReady: !root.querySelector('[data-identity-passed]'),
            });
            if (status === 'generating') {
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
        if (activePolls.has(root)) {
            if (!force) return;
            stopPolling(root);
        }
        if (!force && ['not_started', 'awaiting_reference_approval', 'awaiting_paid_approval', 'retry_approval_required', 'completed', 'failed'].includes(root.dataset.stage)) return;
        const timer = window.setInterval(() => pollProgress(root), force ? 2000 : 8000);
        activePolls.set(root, timer);
        if (immediate) pollProgress(root);
    }

    function reloadWhenProductionSettled(root) {
        const workflow = root.closest('[data-semantic-video-workflow]') || document;
        const roots = Array.from(workflow.querySelectorAll('[data-semantic-video-controller]'));
        const hasActiveProduction = roots.some((candidateRoot) => (
            RUN_PROGRESS_STAGES.includes(candidateRoot.dataset.stage)
        ));
        if (hasActiveProduction || scheduledWorkflowReloads.has(workflow)) return false;
        scheduledWorkflowReloads.add(workflow);
        window.setTimeout(() => window.location.reload(), 100);
        return true;
    }

    async function hydrateRunProgress(root) {
        if (hasSceneImageSurface(root)) return;
        try {
            const progress = await fetchCurrentProgress(root);
            if (!root.isConnected || !progress) return;
            updateProgress(root, progress);
        } catch (error) {
            if (root.dataset.stage !== 'not_started') setStatus(root, error.message, true);
        }
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
        const isSceneImageAction = path === 'scene-image';
        if (isSceneImageAction) root.dataset.candidateStartPending = 'true';
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
            if (['approve', 'retry-approve'].includes(path)) {
                body = await synchronizePaidAction(root, path, body);
                if (!body) return;
            }
            let result = null;
            const maxAttempts = isSceneImageAction ? 2 : 1;
            for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
                try {
                    const request = isSceneImageAction ? requestSceneImageStart : requestJson;
                    result = await request(`/semantic-videos/posts/${encodeURIComponent(root.dataset.postId)}/${path}`, {
                        method: 'POST',
                        body: JSON.stringify(body),
                    });
                    break;
                } catch (error) {
                    if (!isSceneImageAction || attempt + 1 >= maxAttempts) throw error;
                    const progress = await pollProgress(root, {settleCandidateTerminals: false});
                    const reconciledStatus = String(progress?.candidate_generation_status || '');
                    if (reconciledStatus === 'generating') {
                        root.dataset.candidateStartPending = 'false';
                        window.endActionFeedback(button, feedbackState);
                        syncSceneImageWorkflowGate(root);
                        return;
                    }
                    const reconciledReady = (
                        reconciledStatus === 'ready'
                        && progress.stage === 'awaiting_reference_approval'
                        && candidateRequestAdvanced(root, progress)
                    );
                    if (reconciledReady) {
                        settleCandidateAction(
                            root,
                            true,
                            progress.status_message || 'The script image is ready for identity review.',
                        );
                        return;
                    }
                    body = {
                        ...body,
                        expected_revision: expectedSceneImageRevision(root),
                    };
                    root.dataset.waitingForCandidates = 'true';
                    showCandidateLoading(root);
                    syncSceneImageWorkflowGate(root);
                    await new Promise((resolve) => window.setTimeout(resolve, 500));
                }
            }
            if (isSceneImageAction) {
                root.dataset.sceneImageJobId = result.job_id || root.dataset.sceneImageJobId || '';
                if (result.run_id) root.dataset.runId = result.run_id;
                if (result.revision !== undefined && result.revision !== null) {
                    root.dataset.revision = String(result.revision);
                }
                root.dataset.waitingForCandidates = 'true';
                root.dataset.candidateGenerationStatus = 'generating';
                syncSceneImageWorkflowGate(root);
                startPolling(root, true, true);
                window.endActionFeedback(button, feedbackState);
                syncSceneImageWorkflowGate(root);
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
                const progress = await pollProgress(root, {settleCandidateTerminals: false});
                const reconciledStatus = String(progress?.candidate_generation_status || '');
                if (reconciledStatus === 'generating') {
                    root.dataset.candidateStartPending = 'false';
                    window.endActionFeedback(button, feedbackState);
                    syncSceneImageWorkflowGate(root);
                    return;
                }
                const reconciledReady = (
                    reconciledStatus === 'ready'
                    && progress.stage === 'awaiting_reference_approval'
                    && candidateRequestAdvanced(root, progress)
                );
                if (reconciledReady) {
                    settleCandidateAction(
                        root,
                        true,
                        progress.status_message || 'The script image is ready for identity review.',
                    );
                    return;
                }
                if (reconciledStatus === 'stalled') {
                    finishCandidateAction(
                        root,
                        'stalled',
                        progress.status_message || error.message,
                        true,
                    );
                    return;
                }
                finishCandidateAction(root, 'idle', error.message, true);
                return;
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
        const generateButton = action(root, 'generate-candidates');
        rememberSceneImageButton(generateButton);
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

        generateButton?.addEventListener('click', (event) => {
            advanceCandidateActionEpoch(root);
            const expected = expectedSceneImageRevision(root);
            root.dataset.candidateBaselineRunId = root.dataset.runId || '';
            root.dataset.candidateBaselineRevision = root.dataset.revision || '';
            root.dataset.candidateBaselineJobId = root.dataset.sceneImageJobId || '';
            root.dataset.candidateStartPending = 'true';
            root.dataset.waitingForCandidates = 'true';
            showCandidateLoading(root);
            syncSceneImageWorkflowGate(root);
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
        hydrateRunProgress(root);
        startPolling(root);
    }

    function init(scope = document) {
        scope.querySelectorAll('[data-semantic-video-controller]').forEach(bind);
        scope.querySelectorAll('[data-semantic-video-workflow]').forEach((workflow) => {
            if (workflow.dataset.semanticBatchBound === 'true') {
                syncSceneImageWorkflowGate(workflow);
                return;
            }
            workflow.dataset.semanticBatchBound = 'true';
            bindIdentityComparison(workflow);
            const button = workflow.querySelector('[data-action="approve-batch-plans"]');
            button?.addEventListener('click', () => approveReadyBatchPlans(workflow, button));
            syncSceneImageWorkflowGate(workflow);
            buildMissingPlans(workflow);
        });
    }

    document.addEventListener('DOMContentLoaded', () => init());
    document.addEventListener('htmx:afterSwap', (event) => init(event.target));
})();
