(() => {
    const BERLIN_TIME_ZONE = 'Europe/Berlin';

    window.isBatchDetailPlaybackActive = function () {
        return Array.from(document.querySelectorAll('#batch-detail-root video')).some((video) => {
            return !video.paused && !video.ended && video.currentTime > 0;
        });
    };

    window.promptModalComponent = function (postId, initialPrompt = {}, options = {}) {
        const toText = (value) => (typeof value === 'string' ? value : '');
        const buildDraft = (prompt) => {
            const audio = prompt?.audio || {};
            return {
                character: toText(prompt?.character),
                style: toText(prompt?.style),
                action: toText(prompt?.action),
                scene: toText(prompt?.scene),
                cinematography: toText(prompt?.cinematography),
                dialogue: toText(audio?.dialogue),
                ending: toText(prompt?.ending_directive),
                audio_block: toText(prompt?.audio_block),
                universal_negatives: toText(prompt?.universal_negatives),
                veo_prompt: toText(prompt?.veo_prompt),
                veo_negative_prompt: toText(prompt?.veo_negative_prompt),
            };
        };

        return {
            expanded: false,
            editing: false,
            saving: false,
            error: null,
            postId,
            prompt: initialPrompt || {},
            batchScenePlan: options.batchScenePlan || null,
            postType: options.postType || '',
            draft: buildDraft(initialPrompt || {}),
            open() {
                this.expanded = true;
                window.batchDetailExpanded = true;
                document.body.style.overflow = 'hidden';
                this.error = null;
            },
            init() {
                const rawPrompt = this.$el?.dataset?.promptJson;
                if (rawPrompt) {
                    try {
                        this.prompt = JSON.parse(rawPrompt);
                        this.draft = buildDraft(this.prompt);
                    } catch (_error) {
                        this.error = 'Failed to load prompt data';
                    }
                }
            },
            close() {
                this.cancelEditing();
                this.expanded = false;
                window.batchDetailExpanded = false;
                document.body.style.overflow = '';
            },
            startEditing() {
                this.draft = buildDraft(this.prompt);
                this.error = null;
                this.editing = true;
            },
            cancelEditing() {
                this.editing = false;
                this.saving = false;
                this.error = null;
                this.draft = buildDraft(this.prompt);
            },
            async save() {
                if (this.saving) {
                    return;
                }
                this.saving = true;
                this.error = null;
                try {
                    const response = await fetch(`/posts/${this.postId}/prompt`, {
                        method: 'PATCH',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `prompt_edit_${this.postId}`,
                        },
                        body: JSON.stringify(this.draft),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.editing = false;
                    this.expanded = false;
                    window.batchDetailExpanded = false;
                    document.body.style.overflow = '';
                    window.location.reload();
                } catch (error) {
                    this.error = error instanceof Error ? error.message : 'Failed to update prompt';
                } finally {
                    this.saving = false;
                }
            },
        };
    };

    window.timeZoneParts = function (date, timeZone) {
        const formatter = new Intl.DateTimeFormat('en-CA', {
            timeZone,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
        });
        const parts = Object.fromEntries(
            formatter.formatToParts(date)
                .filter((part) => part.type !== 'literal')
                .map((part) => [part.type, part.value]),
        );
        return {
            year: Number(parts.year),
            month: Number(parts.month),
            day: Number(parts.day),
            hour: Number(parts.hour),
            minute: Number(parts.minute),
        };
    };

    window.partsToLocalValue = function (parts) {
        const pad = (value) => String(value).padStart(2, '0');
        return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
    };

    window.zoneDateToLocalValue = function (date, timeZone) {
        return window.partsToLocalValue(window.timeZoneParts(date, timeZone));
    };

    window.zonedLocalValueToUtcDate = function (localValue, timeZone) {
        const [datePart, timePart] = localValue.split('T');
        if (!datePart || !timePart) {
            return null;
        }

        const [year, month, day] = datePart.split('-').map(Number);
        const [hour, minute] = timePart.split(':').map(Number);
        const desiredMinutes = Date.UTC(year, month - 1, day, hour, minute) / 60000;

        let candidate = new Date(Date.UTC(year, month - 1, day, hour, minute));
        for (let attempt = 0; attempt < 2; attempt += 1) {
            const actualParts = window.timeZoneParts(candidate, timeZone);
            const actualMinutes = Date.UTC(
                actualParts.year,
                actualParts.month - 1,
                actualParts.day,
                actualParts.hour,
                actualParts.minute,
            ) / 60000;
            const deltaMinutes = desiredMinutes - actualMinutes;
            if (deltaMinutes === 0) {
                break;
            }
            candidate = new Date(candidate.getTime() + (deltaMinutes * 60 * 1000));
        }

        return candidate;
    };

    window.extractApiError = async function (response) {
        try {
            const payload = await response.json();
            if (payload?.message) return payload.message;
            if (typeof payload?.detail === 'string') return payload.detail;
            if (payload?.detail?.message) return payload.detail.message;
            if (Array.isArray(payload?.detail)) {
                const messages = payload.detail
                    .map((item) => item?.msg || item?.message)
                    .filter(Boolean)
                    .map((message) => String(message).replace(/^Value error,\s*/i, ''));
                if (messages.length) return [...new Set(messages)].join(' ');
            }
        } catch (_error) {
            return `Request failed (${response.status})`;
        }
        return `Request failed (${response.status})`;
    };

    function scriptCard(postId) {
        return document.getElementById(`post-${postId}`);
    }

    function showScriptSaveStatus(card, message) {
        const status = card?.querySelector('[data-script-save-status]');
        if (!status) return;
        status.textContent = message;
        window.setTimeout(() => {
            if (status.textContent === message) status.textContent = '';
        }, 3000);
    }

    window.handleScriptSaveResponse = function (event, postId) {
        if (!event.detail?.successful) return;
        const card = scriptCard(postId);
        // Editing an already-approved script resets review to pending and needs
        // the server-rendered approval control restored. Pending drafts can stay
        // in place and avoid the expensive full batch-detail reconstruction.
        if (card?.dataset?.scriptReviewStatus === 'approved') {
            window.location.reload();
            return;
        }
        showScriptSaveStatus(card, 'Saved');
    };

    window.handleScriptReviewResponse = function (event, postId) {
        const card = scriptCard(postId);
        const approveButton = card?.querySelector('[data-script-approve]');
        if (!event.detail?.successful) {
            if (approveButton) {
                approveButton.disabled = false;
                approveButton.textContent = 'Approve script';
            }
            showScriptSaveStatus(card, 'Approval failed');
            return;
        }
        let data = {};
        try {
            data = JSON.parse(event.detail.xhr?.responseText || '{}')?.data || {};
        } catch (_error) {
            window.location.reload();
            return;
        }
        // The final review changes the whole workflow from Scripts to Scene.
        if (data.batch_state === 'S4_SCRIPTED') {
            window.location.reload();
            return;
        }

        if (!card) return;
        card.dataset.scriptReviewStatus = 'approved';
        const badge = card.querySelector('[data-script-review-badge]');
        if (badge) {
            badge.textContent = 'Approved';
            badge.classList.remove('bg-amber-100', 'text-amber-800');
            badge.classList.add('bg-emerald-100', 'text-emerald-800');
        }
        card.querySelector('[data-script-approve]')?.remove();
        showScriptSaveStatus(card, 'Approved');
        window.dispatchEvent(new CustomEvent('script-review-updated', {
            detail: { postId, status: 'approved' },
        }));
    };

    window.handleScriptReviewStart = function (postId) {
        const card = scriptCard(postId);
        const approveButton = card?.querySelector('[data-script-approve]');
        if (!approveButton) return;
        approveButton.disabled = true;
        approveButton.textContent = 'Approving…';
        showScriptSaveStatus(card, 'Saving approval…');
    };

    window.handleScriptRemovalResponse = function (event, postId) {
        const card = scriptCard(postId);
        const removeButton = card?.querySelector('[data-script-remove]');
        if (!event.detail?.successful) {
            if (removeButton) {
                removeButton.disabled = false;
                removeButton.textContent = 'Remove script';
            }
            showScriptSaveStatus(card, 'Removal failed');
            return;
        }
        let data = {};
        try {
            data = JSON.parse(event.detail.xhr?.responseText || '{}')?.data || {};
        } catch (_error) {
            showScriptSaveStatus(card, 'Removed — refresh to update the workflow');
            return;
        }
        if (data.batch_state === 'S4_SCRIPTED') {
            window.location.reload();
            return;
        }
        card?.remove();
        window.dispatchEvent(new CustomEvent('script-review-updated', {
            detail: { postId, status: 'removed' },
        }));
    };

    window.handleScriptRemovalStart = function (postId) {
        const card = scriptCard(postId);
        const removeButton = card?.querySelector('[data-script-remove]');
        if (!removeButton) return;
        removeButton.disabled = true;
        removeButton.textContent = 'Removing…';
        showScriptSaveStatus(card, 'Removing script…');
    };

    document.body.addEventListener('htmx:responseError', async (event) => {
        const root = document.querySelector('#batch-detail-root');
        const target = event.detail?.target;
        if (!root || !target || !root.contains(target)) {
            return;
        }
        const xhr = event.detail.xhr;
        if (!xhr || !xhr.responseURL?.includes('/posts/')) {
            return;
        }
        let message = `Request failed (${xhr.status})`;
        try {
            const payload = JSON.parse(xhr.responseText || '{}');
            message = payload?.message || payload?.detail?.message || payload?.detail || message;
        } catch (_error) {
            message = xhr.responseText || message;
        }
        window.alert(message);
    });

    window.videoSettingsComponent = function (options = {}) {
        const DEFAULT_MODEL = 'veo-3.1-fast-generate-001';
        const CHARACTER_CONSISTENCY_MODEL = 'veo-3.1-generate-001';
        const supportedModels = {
            'veo-3.1-generate-001': 'Veo 3.1',
            'veo-3.1-fast-generate-001': 'Veo 3.1 Fast',
            'veo-3.1-lite-generate-001': 'Veo 3.1 Lite',
        };
        const defaultPricingTable = {
            'veo-3.1-generate-001': { '720p': 0.40, '1080p': 0.40 },
            'veo-3.1-fast-generate-001': { '720p': 0.10, '1080p': 0.12 },
            'veo-3.1-lite-generate-001': { '720p': 0.05, '1080p': 0.08 },
        };
        const storageKey = options.batchId ? `batch-video-settings:${options.batchId}` : null;
        const numberFormatter = new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });

        return {
            batchId: options.batchId || null,
            targetLengthTier: options.targetLengthTier || null,
            pipelineRoute: options.pipelineRoute || null,
            isCharacterConsistencyMode: Boolean(options.isCharacterConsistencyMode),
            videoSubmissionReadyCount: Number(options.videoSubmissionReadyCount || 0),
            provider: 'vertex_ai',
            model: DEFAULT_MODEL,
            aspectRatio: '9:16',
            duration: String(options.targetLengthTier || 8),
            resolution: '720p',
            modelLabels: supportedModels,
            pricingTable: options.pricingTable || defaultPricingTable,
            pricingModelOrder: [
                'veo-3.1-generate-001',
                'veo-3.1-fast-generate-001',
                'veo-3.1-lite-generate-001',
            ],
            supportedSizes: {
                veo_3_1: {
                    '9:16': { '720p': '720x1280', '1080p': '1080x1920' },
                    '16:9': { '720p': '1280x720', '1080p': '1920x1080' },
                },
                vertex_ai: {
                    '9:16': { '720p': '720x1280', '1080p': '1080x1920' },
                    '16:9': { '720p': '1280x720', '1080p': '1920x1080' },
                },
            },
            isSubmitting: false,
            submitError: null,
            submitStatusMessage: '',
            submitStatusKind: 'info',
            get isDurationRouted() {
                return this.targetLengthTier !== null;
            },
            getProviderSize(aspect, resolution) {
                const providerMap = this.supportedSizes[this.provider] || {};
                const aspectMap = providerMap[aspect] || {};
                return aspectMap[resolution] || null;
            },
            get modelLabel() {
                return this.modelLabels[this.model] || this.model;
            },
            get selectedPricePerSecond() {
                const rates = this.pricingTable[this.model] || {};
                return Number(rates[this.resolution] || 0);
            },
            get selectedPriceLabel() {
                return this.formatCurrency(this.selectedPricePerSecond);
            },
            get estimatedBatchTotal() {
                return this.selectedPricePerSecond * Number(this.duration || 0) * this.videoSubmissionReadyCount;
            },
            get pricingRows() {
                return this.pricingModelOrder.map((model) => {
                    const rates = this.pricingTable[model] || {};
                    return {
                        model,
                        label: this.modelLabels[model] || model,
                        rates: {
                            '720p': this.formatCurrency(rates['720p'] || 0),
                            '1080p': this.formatCurrency(rates['1080p'] || 0),
                        },
                    };
                });
            },
            formatCurrency(value) {
                return numberFormatter.format(Number(value || 0));
            },
            formatSkippedSubmitMessage(skippedPosts, providerName, skippedCount) {
                const firstSkip = Array.isArray(skippedPosts) && skippedPosts.length ? skippedPosts[0] : null;
                const message = String(firstSkip?.message || '').trim();
                const stage = String(firstSkip?.stage || '').trim();
                if (!message) {
                    return '';
                }
                const modelQuota = message.toLowerCase().includes('per_base_model')
                    || message.toLowerCase().includes('base model:')
                    || message.toLowerCase().includes('quota exceeded');
                const prefix = `${providerName} rejected ${skippedCount || 1} post(s)${stage ? ` during ${stage}` : ''}`;
                if (modelQuota) {
                    return `${prefix}: ${message} Try Veo 3.1 Fast or Lite, or request quota for the selected model.`;
                }
                return `${prefix}: ${message}`;
            },
            restorePersistedSettings() {
                if (!storageKey) {
                    return;
                }
                try {
                    const rawValue = window.localStorage.getItem(storageKey);
                    if (!rawValue) {
                        return;
                    }
                    const persisted = JSON.parse(rawValue);
                    if (persisted?.provider === 'vertex_ai') {
                        this.provider = 'vertex_ai';
                    }
                    if (!this.isCharacterConsistencyMode && persisted?.model && this.modelLabels[persisted.model]) {
                        this.model = persisted.model;
                    }
                    if (!this.isDurationRouted && (persisted?.aspectRatio === '9:16' || persisted?.aspectRatio === '16:9')) {
                        this.aspectRatio = persisted.aspectRatio;
                    }
                    if (!this.isDurationRouted && ['720p', '1080p'].includes(persisted?.resolution)) {
                        this.resolution = persisted.resolution;
                    }
                    if (!this.isDurationRouted && ['8', '16', '32'].includes(String(persisted?.duration))) {
                        this.duration = String(persisted.duration);
                    }
                } catch (_error) {
                    window.localStorage.removeItem(storageKey);
                }
            },
            persistSettings() {
                if (!storageKey) {
                    return;
                }
                window.localStorage.setItem(storageKey, JSON.stringify({
                    provider: this.provider,
                    model: this.model,
                    aspectRatio: this.aspectRatio,
                    resolution: this.resolution,
                    duration: this.duration,
                }));
            },
            async submitBatch() {
                if (this.isSubmitting || !this.batchId) {
                    return;
                }
                this.isSubmitting = true;
                this.submitError = null;
                this.submitStatusKind = 'info';
                this.submitStatusMessage = 'Submitting request to the video provider…';
                try {
                    const response = await fetch(`/videos/batch/${this.batchId}/generate-all`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `batch_video_settings_${this.batchId}`,
                        },
                    body: JSON.stringify({
                        provider: this.provider,
                        model: this.model,
                        aspect_ratio: this.aspectRatio,
                        resolution: this.resolution,
                        seconds: Number(this.duration),
                            target_length_tier: this.isDurationRouted ? Number(this.duration) : null,
                            size: this.getProviderSize(this.aspectRatio, this.resolution),
                        }),
                    });

                    const rawBody = await response.text();
                    let payload = null;
                    if (rawBody) {
                        try {
                            payload = JSON.parse(rawBody);
                        } catch (_error) {
                            payload = null;
                        }
                    }

                    if (!response.ok) {
                        const errorMessage = payload?.message
                            || payload?.detail?.message
                            || payload?.error?.message
                            || rawBody
                            || `Submission failed (${response.status})`;
                        const errorCode = payload?.code ? ` [${payload.code}]` : '';
                        throw new Error(`${errorMessage}${errorCode}`);
                    }

                    const submittedCount = payload?.data?.submitted_count ?? 0;
                    const skippedCount = payload?.data?.skipped_count ?? 0;
                    const skippedPosts = payload?.data?.skipped_posts || [];
                    const providerName = this.provider === 'vertex_ai' ? 'Vertex AI' : 'Veo 3.1';
                    if (submittedCount > 0) {
                        this.submitStatusKind = 'success';
                        this.submitStatusMessage = `Submitted ${submittedCount} prompt(s) to ${providerName}.`;
                        window.setTimeout(() => window.location.reload(), 250);
                    } else {
                        this.submitStatusKind = 'warning';
                        const skipMessage = this.formatSkippedSubmitMessage(skippedPosts, providerName, skippedCount);
                        this.submitStatusMessage = payload?.message
                            || skipMessage
                            || `No prompts were submitted to ${providerName}.${skippedCount ? ` ${skippedCount} post(s) were skipped.` : ''} Check the batch details or retry later.`;
                    }
                } catch (error) {
                    this.submitError = error instanceof Error ? error.message : 'Submission failed';
                    this.submitStatusKind = 'warning';
                    this.submitStatusMessage = error instanceof Error ? error.message : 'Submission failed. Check the server logs or quota status.';
                } finally {
                    this.isSubmitting = false;
                }
            },
            init() {
                if (this.isDurationRouted) {
                    this.provider = 'vertex_ai';
                    this.duration = String(this.targetLengthTier || 8);
                }
                if (this.modelLabels[options.initialModel]) {
                    this.model = options.initialModel;
                }
                this.restorePersistedSettings();
                if (this.isCharacterConsistencyMode) {
                    this.model = CHARACTER_CONSISTENCY_MODEL;
                }
                if (!this.modelLabels[this.model]) {
                    this.model = DEFAULT_MODEL;
                }
                this.$watch('provider', () => {
                    this.resolution = this.aspectRatio === '16:9' ? '1080p' : '720p';
                    this.persistSettings();
                });
                this.$watch('aspectRatio', (value) => {
                    this.resolution = this.pipelineRoute === 'veo_extended'
                        ? '720p'
                        : value === '16:9' ? '1080p' : '720p';
                    this.persistSettings();
                });
                this.$watch('model', () => {
                    this.persistSettings();
                });
                this.$watch('resolution', () => {
                    this.persistSettings();
                });
                this.$watch('duration', () => {
                    this.persistSettings();
                });
            },
        };
    };

    window.batchPublishComponent = function (options = {}) {
        const networkCatalog = [
            { id: 'instagram', label: 'Instagram' },
            { id: 'facebook', label: 'Facebook' },
            { id: 'tiktok', label: 'TikTok' },
        ];
        return {
            batchId: options.batchId,
            tiktokDefaults: options.tiktokDefaults || {},
            weekStart: '',
            slots: [],
            timezone: 'Europe/Berlin',
            networks: [],
            posts: (options.posts || []).map((p) => ({
                ...p,
                timeOverride: '',
                networksOverride: null,
                caption: (p.caption || '').trim() || ((p.captionOptions || []).find((item) => item.key === p.selectedCaptionKey)?.body || ''),
                selectedCaptionKey: p.selectedCaptionKey || ((p.captionOptions || [])[0]?.key || ''),
                publishResults: p.publishResults || {},
                platformIds: p.platformIds || {},
                blogScheduleLocal: '',
            })),
            expanded: null,
            saving: false,
            successMessage: '',
            errorMessage: '',
            postNowTarget: null,
            showPostNowModal: false,
            postNowSaving: false,
            postNowError: null,
            tiktokModalReady: false,
            selectedPostId: null,
            showTikTokDefaults: false,
            showSelectedDetails: false,
            itemFeedback: '',
            itemFeedbackError: false,
            savingItem: false,
            placementKind: 'social',
            draggedCalendarItem: null,
            dragOverDay: null,
            savedItemFingerprints: {},
            get canPostNow() {
                if (!this.postNowTarget) return false;
                if (!this.networks.length) return false;
                if (!this.networks.includes('tiktok')) return true;
                const s = this.postNowTarget.tiktokSettings || {};
                if (!s.title || !s.title.trim()) return false;
                if (!s.privacy_level) return false;
                if (s.commercial_disclosure && !s.your_brand && !s.branded_content) return false;
                if (s.branded_content && s.privacy_level === 'SELF_ONLY') return false;
                if (!(s.consent_acknowledged || s.consentAcknowledged)) return false;
                return true;
            },

            tiktokActionLabel() {
                const tiktok = options.tiktokState || {};
                if (!this.networks.includes('tiktok')) return 'Publish Now';
                if (tiktok.publish_ready) return 'Post to TikTok';
                return 'Upload Draft';
            },

            _buildSlots(count) {
                const total = Math.max(1, count || 0);
                return Array.from({ length: total }, () => ({ day: 'Mon', date: '', time: '' }));
            },

            get slotGridStyle() {
                return {
                    gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                };
            },

            networkAvailable(networkId) {
                const meta = options.metaState || {};
                const tiktok = options.tiktokState || {};
                if (networkId === 'instagram') {
                    return !!(meta.publish_ready && meta.selected_instagram?.id);
                }
                if (networkId === 'facebook') {
                    return !!(meta.publish_ready && meta.selected_page?.id);
                }
                if (networkId === 'tiktok') return !!tiktok.publish_ready;
                return false;
            },

            networkAvailabilityLabel(networkId) {
                return this.networkAvailable(networkId) ? 'Connected' : 'Connect account first';
            },

            init() {
                const now = new Date();
                this.slots = this._buildSlots(this.posts.length);

                const persistedLocalValues = this.posts.map((post) => {
                    if (!post.scheduledAt) return '';
                    const scheduled = new Date(post.scheduledAt);
                    if (Number.isNaN(scheduled.getTime())) return '';
                    return window.zoneDateToLocalValue(scheduled, this.timezone);
                });
                const persistedDates = persistedLocalValues.filter(Boolean).map((value) => value.split('T')[0]);

                this.posts.forEach((post) => {
                    if (!post.blogScheduledAt) return;
                    const scheduled = new Date(post.blogScheduledAt);
                    if (Number.isNaN(scheduled.getTime())) return;
                    post.blogScheduleLocal = window.zoneDateToLocalValue(scheduled, this.timezone);
                });

                if (persistedDates.length) {
                    const earliest = new Date(`${[...persistedDates].sort()[0]}T12:00:00`);
                    const monday = new Date(earliest);
                    const mondayOffset = (earliest.getDay() + 6) % 7;
                    monday.setDate(earliest.getDate() - mondayOffset);
                    this.weekStart = monday.toISOString().split('T')[0];
                    this._syncSlotDays();
                    persistedLocalValues.forEach((value, index) => {
                        if (!value) return;
                        const [date, time] = value.split('T');
                        this.slots[index].date = date;
                        this.slots[index].time = time;
                        this.updateSlotDate(index, date);
                    });
                } else {
                    // Default new schedules to next Monday (or today if Monday).
                    const dayOfWeek = now.getDay();
                    const daysUntilMonday = dayOfWeek <= 1 ? (1 - dayOfWeek) : (8 - dayOfWeek);
                    const nextMonday = new Date(now);
                    nextMonday.setDate(now.getDate() + daysUntilMonday);
                    this.weekStart = nextMonday.toISOString().split('T')[0];
                    this._syncSlotDays();
                }

                this.selectedPostId = this.posts[0]?.id || null;

                // Watch weekStart and update slot days when it changes
                this.$watch('weekStart', () => this._syncSlotDays());

                // Rehydrate persisted targets; use connected defaults only for a new plan.
                const meta = options.metaState || {};
                const tiktok = options.tiktokState || {};
                const persistedNetworks = [...new Set(this.posts.flatMap((post) => post.socialNetworks || []))]
                    .filter((networkId) => this.networkAvailable(networkId));
                if (persistedNetworks.length) {
                    this.networks = persistedNetworks;
                } else {
                    if (meta.publish_ready) {
                        if (meta.selected_instagram?.id) this.networks.push('instagram');
                        if (meta.selected_page?.id) this.networks.push('facebook');
                    }
                    if (tiktok.publish_ready) this.networks.push('tiktok');
                }
                this.posts.forEach((_post, index) => {
                    this.savedItemFingerprints[this.posts[index].id] = this.itemFingerprint(index);
                });
            },

            get selectedPost() {
                return this.posts.find((post) => post.id === this.selectedPostId) || this.posts[0] || null;
            },

            get selectedPostIndex() {
                return this.selectedPost ? this.posts.findIndex((post) => post.id === this.selectedPost.id) : -1;
            },

            selectPost(postId) {
                this.selectedPostId = postId;
                this.itemFeedback = '';
                this.itemFeedbackError = false;
                if (!this.selectedPost?.blogEnabled && this.placementKind === 'blog') {
                    this.placementKind = 'social';
                }
            },

            setPlacementKind(kind) {
                if (kind === 'blog' && !this.selectedPost?.blogEnabled) return;
                this.placementKind = kind === 'blog' ? 'blog' : 'social';
            },

            durationLabel(post) {
                const raw = post?.videoMetadata?.duration_seconds
                    ?? post?.videoMetadata?.delivery_duration_seconds
                    ?? post?.videoMetadata?.output_duration_seconds;
                const seconds = Number(raw);
                if (!Number.isFinite(seconds) || seconds <= 0) return 'Video';
                return `${Math.round(seconds)}s video`;
            },

            syncVideoDuration(post, event) {
                const duration = Number(event?.currentTarget?.duration);
                if (!post || !Number.isFinite(duration) || duration <= 0) return;
                post.videoMetadata = {
                    ...(post.videoMetadata || {}),
                    duration_seconds: duration,
                };
            },

            formatCalendarDate(dateValue, options = {}) {
                if (!dateValue) return '';
                const date = new Date(`${dateValue}T12:00:00`);
                if (Number.isNaN(date.getTime())) return '';
                return new Intl.DateTimeFormat('en-GB', {
                    weekday: options.weekday || undefined,
                    day: 'numeric',
                    month: 'short',
                    year: options.year || undefined,
                }).format(date);
            },

            calendarWeekStartISO(dateValue) {
                if (!dateValue) return '';
                const date = dateValue instanceof Date
                    ? new Date(dateValue.getTime())
                    : new Date(`${dateValue}T12:00:00`);
                if (Number.isNaN(date.getTime())) return '';
                date.setDate(date.getDate() - ((date.getDay() + 6) % 7));
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            },

            get calendarDays() {
                if (!this.weekStart) return [];
                const monday = this.calendarWeekStartISO(this.weekStart);
                return Array.from({ length: 7 }, (_value, index) => {
                    const date = new Date(`${monday}T12:00:00`);
                    date.setDate(date.getDate() + index);
                    const iso = date.toISOString().slice(0, 10);
                    return {
                        iso,
                        weekday: new Intl.DateTimeFormat('en-GB', { weekday: 'short' }).format(date),
                        label: new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(date),
                    };
                });
            },

            get calendarRangeLabel() {
                const days = this.calendarDays;
                if (!days.length) return '';
                const start = new Date(`${days[0].iso}T12:00:00`);
                const end = new Date(`${days[days.length - 1].iso}T12:00:00`);
                const startLabel = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' }).format(start);
                const endLabel = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(end);
                return `${startLabel}–${endLabel}`;
            },

            get calendarHours() {
                return Array.from({ length: 13 }, (_value, index) => index + 8);
            },

            calendarEventsForDay(dayIso) {
                const events = [];
                this.posts.forEach((post, index) => {
                    const socialValue = this.scheduledLocalValue(index);
                    if (socialValue?.startsWith(`${dayIso}T`)) {
                        events.push({ post, index, kind: 'social', localValue: socialValue });
                    }
                    if (post.blogEnabled && post.blogScheduleLocal?.startsWith(`${dayIso}T`)) {
                        events.push({ post, index, kind: 'blog', localValue: post.blogScheduleLocal });
                    }
                });
                return events.sort((left, right) => left.localValue.localeCompare(right.localValue));
            },

            calendarEventStyle(event) {
                const time = String(event?.localValue || '').split('T')[1] || '08:00';
                const [hour, minute] = time.split(':').map(Number);
                const startMinutes = 8 * 60;
                const totalMinutes = 12 * 60;
                const eventMinutes = Math.min(totalMinutes, Math.max(0, (hour * 60 + minute) - startMinutes));
                const top = (eventMinutes / totalMinutes) * 100;
                const height = event.kind === 'blog' ? 6.25 : 8.35;
                return `top: calc(${top}% + 2px); height: calc(${height}% - 4px);`;
            },

            calendarEventTime(event) {
                return String(event?.localValue || '').split('T')[1]?.slice(0, 5) || '';
            },

            get calendarEventCount() {
                return this.calendarDays.reduce(
                    (total, day) => total + this.calendarEventsForDay(day.iso).length,
                    0,
                );
            },

            _calendarTimeFromEvent(event) {
                const lane = event?.currentTarget;
                const rect = lane?.getBoundingClientRect?.();
                if (!rect || !rect.height || typeof event.clientY !== 'number' || event.clientY <= 0) {
                    return '10:00';
                }
                const ratio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
                const rounded = Math.round((ratio * 12 * 60) / 15) * 15;
                const minutesFromMidnight = Math.min((19 * 60) + 45, (8 * 60) + rounded);
                const hour = Math.floor(minutesFromMidnight / 60);
                const minute = minutesFromMidnight % 60;
                return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
            },

            placeCalendarItem(postId, kind, dayIso, time) {
                const index = this.posts.findIndex((post) => post.id === postId);
                const post = this.posts[index];
                if (index < 0 || !post || this.isDispatchLocked(post)) return;
                if (kind === 'blog' && !post.blogEnabled) {
                    this.itemFeedback = 'This item does not have a blog post.';
                    this.itemFeedbackError = true;
                    return;
                }
                this.selectPost(postId);
                this.placementKind = kind;
                if (kind === 'blog') {
                    post.blogScheduleLocal = `${dayIso}T${time}`;
                } else {
                    this.slots[index].date = dayIso;
                    this.slots[index].time = time;
                    post.timeOverride = '';
                    this.updateSlotDate(index, dayIso);
                }
                const label = kind === 'blog' ? 'Blog post' : 'Social video';
                this.itemFeedback = `${label} placed at ${time}. Save the item to keep this placement.`;
                this.itemFeedbackError = false;
            },

            placeSelectedOnCalendar(dayIso, event) {
                if (!this.selectedPost) return;
                this.placeCalendarItem(
                    this.selectedPost.id,
                    this.placementKind,
                    dayIso,
                    this._calendarTimeFromEvent(event),
                );
            },

            placeSelectedWithKeyboard(dayIso, event) {
                if (!['Enter', ' '].includes(event.key)) return;
                event.preventDefault();
                this.placeSelectedOnCalendar(dayIso, event);
            },

            startCalendarDrag(event, postId, kind = 'social') {
                const post = this.posts.find((item) => item.id === postId);
                if (!post || this.isDispatchLocked(post) || (kind === 'blog' && !post.blogEnabled)) {
                    event.preventDefault();
                    return;
                }
                this.selectPost(postId);
                this.placementKind = kind;
                this.draggedCalendarItem = { postId, kind };
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', JSON.stringify(this.draggedCalendarItem));
                }
            },

            endCalendarDrag() {
                this.draggedCalendarItem = null;
                this.dragOverDay = null;
            },

            dropCalendarItem(dayIso, event) {
                let dragged = this.draggedCalendarItem;
                if (!dragged && event.dataTransfer) {
                    try {
                        dragged = JSON.parse(event.dataTransfer.getData('text/plain'));
                    } catch (_error) {
                        dragged = null;
                    }
                }
                if (!dragged?.postId) return;
                this.placeCalendarItem(
                    dragged.postId,
                    dragged.kind || 'social',
                    dayIso,
                    this._calendarTimeFromEvent(event),
                );
                this.endCalendarDrag();
            },

            shiftCalendarWeek(days) {
                if (!this.weekStart) return;
                const next = new Date(`${this.calendarWeekStartISO(this.weekStart)}T12:00:00`);
                next.setDate(next.getDate() + days);
                this.weekStart = this.calendarWeekStartISO(next);
            },

            goToCurrentWeek() {
                const now = new Date();
                this.weekStart = this.calendarWeekStartISO(now);
            },

            itemFingerprint(index) {
                const post = this.posts[index];
                if (!post) return '';
                return JSON.stringify({
                    social: this.scheduledLocalValue(index),
                    blog: post.blogEnabled ? post.blogScheduleLocal : '',
                    caption: String(post.caption || '').trim(),
                    networks: [...this.networks].sort(),
                    timezone: this.timezone,
                });
            },

            get selectedItemDirty() {
                if (this.selectedPostIndex < 0) return false;
                return this.savedItemFingerprints[this.selectedPost.id] !== this.itemFingerprint(this.selectedPostIndex);
            },

            get selectedItemPersisted() {
                if (!this.selectedPost?.scheduledAt) return false;
                return !this.selectedPost.blogEnabled || !!this.selectedPost.blogScheduledAt;
            },

            async saveSelectedItem() {
                if (this.selectedPostIndex < 0 || this.savingItem) return;
                const post = this.selectedPost;
                const index = this.selectedPostIndex;
                const issue = this.slotIssue(index)
                    || this.blogScheduleIssue(post)
                    || (!post.caption?.trim() ? 'Add a social caption' : '')
                    || (!this.networks.length ? 'Select at least one destination' : '');
                if (issue) {
                    this.itemFeedback = issue;
                    this.itemFeedbackError = true;
                    return;
                }
                this.savingItem = true;
                this.itemFeedback = '';
                try {
                    const response = await fetch(`/publish/posts/${post.id}/plan`, {
                        method: 'PUT',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `save_publish_plan_${post.id}`,
                        },
                        body: JSON.stringify({
                            scheduled_at: this.scheduledDate(index).toISOString(),
                            publish_caption: post.caption.trim(),
                            social_networks: this.networks,
                            blog_scheduled_at: post.blogEnabled
                                ? this.blogScheduledDate(post).toISOString()
                                : null,
                        }),
                    });
                    if (!response.ok) throw new Error(await window.extractApiError(response));
                    const payload = await response.json();
                    const saved = payload?.data || {};
                    post.scheduledAt = saved.scheduled_at;
                    post.blogScheduledAt = saved.blog_scheduled_at;
                    post.socialNetworks = saved.social_networks || [...this.networks];
                    post.publishStatus = saved.publish_status || 'pending';
                    if (post.blogEnabled) post.blogStatus = saved.blog_status || 'draft';
                    this.savedItemFingerprints[post.id] = this.itemFingerprint(index);
                    const attention = this.networks.includes('tiktok') ? this.tiktokPostIssue(post) : '';
                    this.itemFeedback = attention
                        ? `Item schedule saved. ${attention}.`
                        : 'Item schedule saved.';
                    this.itemFeedbackError = false;
                } catch (error) {
                    this.itemFeedback = error.message || 'Failed to save this item schedule.';
                    this.itemFeedbackError = true;
                } finally {
                    this.savingItem = false;
                }
            },

            openFirstCalendarIssue() {
                const target = this.posts.find((post, index) => {
                    if (this.isDispatchLocked(post)) return false;
                    return !!(this.slotIssue(index) || this.blogScheduleIssue(post) || this.postDetailIssue(post, index));
                });
                if (!target) return;
                this.selectPost(target.id);
                this.showSelectedDetails = !!this.postDetailIssue(target, this.posts.findIndex((post) => post.id === target.id));
                this.$nextTick(() => document.getElementById('selected-item-heading')?.scrollIntoView({ block: 'nearest' }));
            },

            isDispatchLocked(post) {
                return ['scheduled', 'publishing', 'published'].includes(String(post?.publishStatus || '').toLowerCase());
            },

            get editablePosts() {
                return this.posts.filter((post) => !this.isDispatchLocked(post));
            },

            get hasActiveSchedule() {
                return this.posts.length > 0 && this.editablePosts.length === 0;
            },

            get contentTotalCount() {
                return this.hasActiveSchedule ? this.posts.length : this.editablePosts.length;
            },

            get contentReadyCount() {
                if (this.hasActiveSchedule) return this.posts.length;
                return this.editablePosts.filter((post) => {
                    const index = this.posts.findIndex((item) => item.id === post.id);
                    return !this.postDetailIssue(post, index)
                        && !this.slotIssue(index)
                        && !this.blogScheduleIssue(post);
                }).length;
            },

            get scheduleStatusReady() {
                return this.hasActiveSchedule || this.canReview;
            },

            get scheduleStatusLabel() {
                if (this.hasActiveSchedule) return 'Schedule active';
                if (this.canReview) return 'Ready to review';
                return `${this.readinessIssues.length} item${this.readinessIssues.length === 1 ? '' : 's'} to fix`;
            },

            get allSlotsSet() {
                return this.slots.length > 0 && this.slots.every((s) => s.time);
            },
            get slotsSetCount() {
                return this.slots.filter((s) => s.time).length;
            },
            get summaryLine() {
                const nets = this.networks
                    .map((n) => n === 'instagram' ? 'Instagram' : n === 'facebook' ? 'Facebook' : 'TikTok')
                    .join(' + ');
                const days = [...new Set(this.slots.map(s => s.day))];
                const dayRange = days.length === 1 ? days[0] : `${this.slots[0].day}\u2013${this.slots[this.slots.length - 1].day}`;
                const blogCount = this.posts.filter((post) => post.blogEnabled).length;
                const blogSummary = blogCount
                    ? ` \u00b7 ${blogCount} blog post${blogCount === 1 ? '' : 's'}`
                    : '';
                const socialLabel = `${this.posts.length} social post${this.posts.length === 1 ? '' : 's'}`;
                return `${socialLabel} \u00b7 ${dayRange} \u00b7 ${nets || 'No networks selected'}${blogSummary}`;
            },
            scheduledLocalValue(index) {
                const override = this.posts[index]?.timeOverride;
                if (override) return override;
                const slot = this.slots[index];
                if (!slot?.date || !slot?.time) return '';
                return `${slot.date}T${slot.time}`;
            },
            scheduledDate(index) {
                const localValue = this.scheduledLocalValue(index);
                return localValue ? window.zonedLocalValueToUtcDate(localValue, this.timezone) : null;
            },
            get scheduleConflicts() {
                const entries = this.posts
                    .map((_post, index) => ({ index, date: this.scheduledDate(index) }))
                    .filter((entry) => entry.date instanceof Date && !Number.isNaN(entry.date.getTime()))
                    .sort((a, b) => a.date - b.date);
                const conflicts = [];
                for (let i = 1; i < entries.length; i += 1) {
                    const gapMinutes = (entries[i].date - entries[i - 1].date) / 60000;
                    if (gapMinutes < 30) {
                        conflicts.push({
                            first: entries[i - 1].index,
                            second: entries[i].index,
                            gapMinutes: Math.max(0, Math.round(gapMinutes)),
                        });
                    }
                }
                return conflicts;
            },
            slotIssue(index) {
                const slot = this.slots[index];
                if (!slot?.date || !slot?.time) return 'Choose a date and time';
                const scheduled = this.scheduledDate(index);
                if (!scheduled || Number.isNaN(scheduled.getTime())) return 'Choose a valid date and time';
                if (scheduled <= new Date()) return 'Choose a future time';
                const conflict = this.scheduleConflicts.find((item) => item.first === index || item.second === index);
                if (conflict) return `Only ${conflict.gapMinutes} min from another post`;
                return '';
            },
            blogScheduledDate(post) {
                if (!post?.blogScheduleLocal) return null;
                return window.zonedLocalValueToUtcDate(post.blogScheduleLocal, this.timezone);
            },
            blogScheduleIssue(post) {
                if (!post?.blogEnabled) return '';
                if (!post.blogTextReady) return 'Generate the blog text first';
                if (!post.blogImageReady) return 'Generate the blog preview image first';
                if (!post.blogScheduleLocal) return 'Choose a blog publication date and time';
                const scheduled = this.blogScheduledDate(post);
                if (!scheduled || Number.isNaN(scheduled.getTime())) return 'Choose a valid blog publication time';
                if (scheduled <= new Date()) return 'Choose a future blog publication time';
                return '';
            },
            blogScheduleLabel(post) {
                const scheduled = this.blogScheduledDate(post);
                if (!scheduled || Number.isNaN(scheduled.getTime())) return 'Not scheduled';
                return new Intl.DateTimeFormat('en-GB', {
                    timeZone: this.timezone,
                    dateStyle: 'medium',
                    timeStyle: 'short',
                }).format(scheduled);
            },
            tiktokPostIssue(post) {
                const settings = post.tiktokSettings || {};
                if (!settings.title?.trim()) return 'TikTok title is missing';
                if (!settings.privacy_level) return 'TikTok privacy is missing';
                if (settings.commercial_disclosure && !settings.your_brand && !settings.branded_content) {
                    return 'TikTok disclosure type is missing';
                }
                if (settings.branded_content && settings.privacy_level === 'SELF_ONLY') {
                    return 'Branded TikTok content cannot be private';
                }
                if (!settings.consent_acknowledged) return 'TikTok consent is not saved';
                return '';
            },
            postDetailIssue(post, index) {
                if (!post?.caption?.trim()) return 'Add a social caption';
                if (this.networks.includes('tiktok')) {
                    return this.tiktokPostIssue(post);
                }
                return '';
            },
            get readinessIssues() {
                const issues = [];
                this.posts.forEach((post, index) => {
                    if (this.isDispatchLocked(post)) return;
                    const scheduleIssue = this.slotIssue(index);
                    if (scheduleIssue) issues.push(`${post.title}: ${scheduleIssue}`);
                    if (!post.caption?.trim()) issues.push(`${post.title}: add a caption`);
                    const blogIssue = this.blogScheduleIssue(post);
                    if (blogIssue) issues.push(`${post.title}: ${blogIssue}`);
                    if (this.networks.includes('tiktok')) {
                        const tiktokIssue = this.tiktokPostIssue(post);
                        if (tiktokIssue) issues.push(`${post.title}: ${tiktokIssue}`);
                    }
                });
                if (!this.networks.length) issues.push('Select at least one connected destination');
                this.networks.forEach((networkId) => {
                    if (!this.networkAvailable(networkId)) {
                        const network = networkCatalog.find((item) => item.id === networkId);
                        issues.push(`${network?.label || networkId}: connect the account before scheduling`);
                    }
                });
                return [...new Set(issues)];
            },
            publishStatusLabel(post, index) {
                const tiktokStatus = (post.publishResults?.tiktok?.status || '').toLowerCase();
                if (post.publishStatus === 'publishing' && tiktokStatus === 'awaiting_user_action') {
                    return 'Draft uploaded';
                }
                if (post.publishStatus === 'failed' && tiktokStatus === 'published') {
                    return 'TikTok published';
                }
                if (post.publishStatus === 'failed' && tiktokStatus === 'failed') {
                    return 'TikTok failed';
                }
                if (post.publishStatus === 'published') return 'Published';
                if (post.publishStatus === 'scheduled') return 'Scheduled';
                if (post.publishStatus === 'publishing') return 'Publishing...';
                if (post.publishStatus === 'failed') return 'Failed';
                const tiktokReady = !this.networks.includes('tiktok') || !this.tiktokPostIssue(post);
                return (post.caption && !this.slotIssue(index) && tiktokReady) ? 'Ready' : 'Needs attention';
            },
            publishStatusClass(post, index) {
                const tiktokStatus = (post.publishResults?.tiktok?.status || '').toLowerCase();
                if (post.publishStatus === 'publishing' && tiktokStatus === 'awaiting_user_action') {
                    return 'bg-[#006AAB]/10 text-[#006AAB]';
                }
                if (post.publishStatus === 'failed' && tiktokStatus === 'published') {
                    return 'bg-green-100 text-green-700';
                }
                if (post.publishStatus === 'failed' && tiktokStatus === 'failed') {
                    return 'bg-red-100 text-red-700';
                }
                if (post.publishStatus === 'published') return 'bg-green-100 text-green-700';
                if (post.publishStatus === 'scheduled') return 'bg-[#006AAB]/10 text-[#006AAB]';
                if (post.publishStatus === 'publishing') return 'bg-amber-100 text-amber-700';
                if (post.publishStatus === 'failed') return 'bg-red-100 text-red-700';
                const tiktokReady = !this.networks.includes('tiktok') || !this.tiktokPostIssue(post);
                return (post.caption && !this.slotIssue(index) && tiktokReady) ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700';
            },
            get warnings() {
                return this.readinessIssues;
            },
            get canReview() {
                return this.editablePosts.length > 0 && this.readinessIssues.length === 0 && !this.saving;
            },
            get canArm() {
                return this.canReview;
            },

            openFirstIssue() {
                const target = this.posts.find((post, index) => {
                    if (this.isDispatchLocked(post)) return false;
                    return !!(
                        this.slotIssue(index)
                        || this.blogScheduleIssue(post)
                        || this.postDetailIssue(post, index)
                    );
                });
                if (!target) return;
                this.expanded = target.id;
                this.$nextTick(() => {
                    document.getElementById(`publish-post-${target.id}`)?.scrollIntoView({
                        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
                        block: 'center',
                    });
                });
            },

            slotDateISO(i) {
                return this.slots[i].date || '';
            },
            updateSlotDate(i, dateStr) {
                if (!dateStr) return;
                const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                const d = new Date(`${dateStr}T12:00:00`);
                this.slots[i].date = dateStr;
                this.slots[i].day = dayNames[d.getDay()];
            },
            spaceConflictingSlots() {
                const ordered = this.posts
                    .map((_post, index) => ({ index, date: this.scheduledDate(index) }))
                    .filter((entry) => entry.date instanceof Date && !Number.isNaN(entry.date.getTime()))
                    .sort((a, b) => a.date - b.date)
                    .map((entry) => entry.index);
                let previous = null;
                ordered.forEach((index) => {
                    let scheduled = this.scheduledDate(index);
                    if (!scheduled || Number.isNaN(scheduled.getTime())) return;
                    if (previous && scheduled - previous < 30 * 60 * 1000) {
                        scheduled = new Date(previous.getTime() + (30 * 60 * 1000));
                        const [date, time] = window.zoneDateToLocalValue(scheduled, this.timezone).split('T');
                        this.slots[index].date = date;
                        this.slots[index].time = time;
                        this.updateSlotDate(index, date);
                    }
                    previous = scheduled;
                });
            },
            postSlotLabel(i) {
                const post = this.posts[i];
                if (post?.scheduledAt) {
                    const d = new Date(post.scheduledAt);
                    const day = d.toLocaleDateString('en-GB', { weekday: 'short' });
                    const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
                    return `${day} ${time}`;
                }
                const slot = this.slots[i];
                if (!slot) return 'No slot';
                return `${slot.day} ${slot.time || '\u2014'}`;
            },
            slotDisplayDate(i) {
                const iso = this.slotDateISO(i);
                if (!iso) return '';
                const d = new Date(iso);
                return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
            },
            _syncSlotDays() {
                if (!this.weekStart) return;
                const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                for (let i = 0; i < this.slots.length; i++) {
                    const d = new Date(this.weekStart);
                    d.setDate(d.getDate() + i);
                    this.slots[i].day = dayNames[d.getDay()];
                    this.slots[i].date = d.toISOString().split('T')[0];
                }
            },

            toggleNetwork(id) {
                if (!this.networkAvailable(id)) return;
                if (this.networks.includes(id)) {
                    this.networks = this.networks.filter((n) => n !== id);
                } else {
                    this.networks.push(id);
                }
            },

            selectCaption(postId, variantKey) {
                const post = this.posts.find((item) => item.id === postId);
                if (!post) return;
                const variant = (post.captionOptions || []).find((item) => item.key === variantKey);
                if (!variant || !variant.body) return;
                post.selectedCaptionKey = variant.key;
                post.caption = variant.body;
            },

            async armDispatch() {
                if (!this.canArm) return;
                this.saving = true;
                this.errorMessage = '';
                this.successMessage = '';
                try {
                    const dayMap = { Mon: 'mon', Tue: 'tue', Wed: 'wed', Thu: 'thu', Fri: 'fri', Sat: 'sat', Sun: 'sun' };
                    const response = await fetch(`/publish/batches/${this.batchId}/arm`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `arm_batch_${this.batchId}`,
                        },
                        body: JSON.stringify({
                            week_start: this.calendarWeekStartISO(this.weekStart),
                            timezone: this.timezone,
                            slots: this.slots.map((s) => ({ day: dayMap[s.day], time: s.time })),
                            default_networks: this.networks,
                            posts: this.posts.map((p, i) => {
                                // Always send time_override using the slot's actual date
                                let timeOverride = p.timeOverride || null;
                                if (!timeOverride && i < this.slots.length && this.slots[i].date && this.slots[i].time) {
                                    timeOverride = `${this.slots[i].date}T${this.slots[i].time}`;
                                }
                                return {
                                    post_id: p.id,
                                    caption: p.caption.trim(),
                                    time_override: timeOverride,
                                    networks_override: p.networksOverride,
                                    blog_scheduled_at: p.blogEnabled && p.blogScheduleLocal
                                        ? this.blogScheduledDate(p).toISOString()
                                        : null,
                                };
                            }),
                        }),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'Social and blog schedules saved successfully.';
                    setTimeout(() => window.location.reload(), 1500);
                } catch (error) {
                    this.errorMessage = error.message || 'Failed to arm dispatch';
                } finally {
                    this.saving = false;
                }
            },

            async postNow() {
                if (!this.postNowTarget) return;
                if (!this.canPostNow) return;
                this.postNowSaving = true;
                this.postNowError = null;
                try {
                    const body = {
                        post_id: this.postNowTarget.id,
                        publish_caption: this.postNowTarget.caption,
                        social_networks: this.networks,
                    };
                    if (this.networks.includes('tiktok')) {
                        body.tiktok_settings = this.postNowTarget.tiktokSettings || null;
                    }
                    const resp = await fetch(`/publish/posts/${this.postNowTarget.id}/now`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `post_now_${this.postNowTarget.id}`,
                        },
                        body: JSON.stringify(body),
                    });
                    if (!resp.ok) {
                        throw new Error(await window.extractApiError(resp));
                    }
                    const data = await resp.json();
                    const idx = this.posts.findIndex(p => p.id === this.postNowTarget.id);
                    if (idx !== -1) {
                        this.posts[idx].publishStatus = data.data?.publish_status || 'published';
                        this.posts[idx].publishResults = data.data?.publish_results || this.posts[idx].publishResults || {};
                        this.posts[idx].platformIds = data.data?.platform_ids || this.posts[idx].platformIds || {};
                    }
                    this.showPostNowModal = false;
                    const tiktokStatus = data.data?.publish_results?.tiktok?.status;
                    this.successMessage = tiktokStatus === 'published'
                        ? 'TikTok published successfully — content may take a few minutes to appear on your profile.'
                        : tiktokStatus === 'awaiting_user_action'
                            ? 'TikTok draft uploaded.'
                            : 'Post published successfully.';
                    setTimeout(() => window.location.reload(), 1500);
                    setTimeout(() => this.successMessage = '', 5000);
                } catch (err) {
                    this.postNowError = err.message || 'Network error';
                } finally {
                    this.postNowSaving = false;
                }
            },
        };
    };
})();
