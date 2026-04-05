(() => {
    const BERLIN_TIME_ZONE = 'Europe/Berlin';

    window.isBatchDetailPlaybackActive = function () {
        return Array.from(document.querySelectorAll('#batch-detail-root video')).some((video) => {
            return !video.paused && !video.ended && video.currentTime > 0;
        });
    };

    window.promptModalComponent = function (postId, initialPrompt = {}) {
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
        } catch (_error) {
            return `Request failed (${response.status})`;
        }
        return `Request failed (${response.status})`;
    };

    window.videoSettingsComponent = function (options = {}) {
        return {
            batchId: options.batchId || null,
            targetLengthTier: options.targetLengthTier || null,
            pipelineRoute: options.pipelineRoute || null,
            provider: 'veo_3_1',
            aspectRatio: '9:16',
            duration: String(options.targetLengthTier || 8),
            resolution: '720p',
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
                    const providerName = this.provider === 'vertex_ai' ? 'Vertex AI' : 'Veo 3.1';
                    if (submittedCount > 0) {
                        this.submitStatusKind = 'success';
                        this.submitStatusMessage = `Submitted ${submittedCount} prompt(s) to ${providerName}.`;
                        window.setTimeout(() => window.location.reload(), 250);
                    } else {
                        this.submitStatusKind = 'warning';
                        this.submitStatusMessage = payload?.message
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
                    this.provider = 'veo_3_1';
                    this.duration = String(this.targetLengthTier || 8);
                }
                this.$watch('provider', () => {
                    this.resolution = this.aspectRatio === '16:9' ? '1080p' : '720p';
                });
                this.$watch('aspectRatio', (value) => {
                    this.resolution = this.pipelineRoute === 'veo_extended'
                        ? '720p'
                        : value === '16:9' ? '1080p' : '720p';
                });
            },
        };
    };

    window.batchPublishComponent = function (options = {}) {
        return {
            batchId: options.batchId,
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
            })),
            expanded: null,
            showReviewModal: false,
            saving: false,
            successMessage: '',
            errorMessage: '',
            postNowTarget: null,
            showPostNowModal: false,
            postNowSaving: false,
            postNowError: null,

            _buildSlots(count) {
                const total = Math.max(1, count || 0);
                return Array.from({ length: total }, () => ({ day: 'Mon', date: '', time: '' }));
            },

            get slotGridStyle() {
                return {
                    gridTemplateColumns: `repeat(${Math.max(this.slots.length, 1)}, minmax(0, 1fr))`,
                };
            },

            init() {
                // Default week start to next Monday (or today if Monday)
                const now = new Date();
                const dayOfWeek = now.getDay();
                const daysUntilMonday = dayOfWeek <= 1 ? (1 - dayOfWeek) : (8 - dayOfWeek);
                const nextMonday = new Date(now);
                nextMonday.setDate(now.getDate() + daysUntilMonday);
                this.weekStart = nextMonday.toISOString().split('T')[0];
                this.slots = this._buildSlots(this.posts.length);
                this._syncSlotDays();

                // Watch weekStart and update slot days when it changes
                this.$watch('weekStart', () => this._syncSlotDays());

                // Auto-enable connected networks
                const meta = options.metaState || {};
                const tiktok = options.tiktokState || {};
                if (meta.publish_ready) {
                    if (meta.selected_instagram?.id) this.networks.push('instagram');
                    if (meta.selected_page?.id) this.networks.push('facebook');
                }
                if (tiktok.publish_ready) {
                    this.networks.push('tiktok');
                }
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
                return `${this.posts.length} posts \u00b7 ${dayRange} \u00b7 ${nets || 'No networks selected'}`;
            },
            get warnings() {
                const w = [];
                this.posts.forEach((p, i) => {
                    if (!p.caption || !p.caption.trim()) w.push(`"${p.title}" has no caption`);
                    if (i >= this.slots.length && !p.timeOverride) w.push(`"${p.title}" has no time slot`);
                });
                if (this.networks.length === 0) w.push('No networks selected');
                return w;
            },
            get canArm() {
                return this.posts.length > 0
                    && this.slots.length >= this.posts.length
                    && this.allSlotsSet
                    && this.networks.length > 0
                    && this.posts.every((p, i) => p.caption?.trim() && (i < this.slots.length || p.timeOverride))
                    && !this.saving;
            },

            slotDateISO(i) {
                return this.slots[i].date || '';
            },
            updateSlotDate(i, dateStr) {
                if (!dateStr) return;
                const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
                const d = new Date(dateStr);
                this.slots[i].date = dateStr;
                this.slots[i].day = dayNames[d.getDay()];
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
                            week_start: this.weekStart,
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
                                };
                            }),
                        }),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'Dispatch armed successfully.';
                    this.showReviewModal = false;
                    setTimeout(() => window.location.reload(), 1500);
                } catch (error) {
                    this.errorMessage = error.message || 'Failed to arm dispatch';
                } finally {
                    this.saving = false;
                }
            },

            async postNow() {
                if (!this.postNowTarget) return;
                this.postNowSaving = true;
                this.postNowError = null;
                try {
                    const resp = await fetch(`/publish/posts/${this.postNowTarget.id}/now`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `post_now_${this.postNowTarget.id}`,
                        },
                        body: JSON.stringify({
                            post_id: this.postNowTarget.id,
                            publish_caption: this.postNowTarget.caption,
                            social_networks: this.networks,
                        }),
                    });
                    if (!resp.ok) {
                        throw new Error(await window.extractApiError(resp));
                    }
                    const data = await resp.json();
                    // Update local post state
                    const idx = this.posts.findIndex(p => p.id === this.postNowTarget.id);
                    if (idx !== -1) {
                        this.posts[idx].publishStatus = data.data?.publish_status || 'published';
                    }
                    this.showPostNowModal = false;
                    this.successMessage = 'Post published successfully!';
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
