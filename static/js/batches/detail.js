(() => {
    const BERLIN_TIME_ZONE = 'Europe/Berlin';

    window.isBatchDetailPlaybackActive = function () {
        return Array.from(document.querySelectorAll('#batch-detail-root video')).some((video) => {
            return !video.paused && !video.ended && video.currentTime > 0;
        });
    };

    window.promptModalComponent = function (postId) {
        return {
            expanded: false,
            postId,
            open() {
                this.expanded = true;
                window.batchDetailExpanded = true;
                document.body.style.overflow = 'hidden';
            },
            close() {
                this.expanded = false;
                window.batchDetailExpanded = false;
                document.body.style.overflow = '';
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
                    if (submittedCount > 0) {
                        this.submitStatusKind = 'success';
                        this.submitStatusMessage = `Submitted ${submittedCount} prompt(s) to Veo 3.1.`;
                    } else {
                        this.submitStatusKind = 'warning';
                        this.submitStatusMessage = payload?.message
                            || `No prompts were submitted to Veo 3.1.${skippedCount ? ` ${skippedCount} post(s) were skipped.` : ''} Check the batch details or retry later.`;
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
                    if (this.isDurationRouted) {
                        this.provider = 'veo_3_1';
                        return;
                    }
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
            slots: [
                { day: 'Mon', time: '' },
                { day: 'Tue', time: '' },
                { day: 'Wed', time: '' },
                { day: 'Thu', time: '' },
                { day: 'Fri', time: '' },
            ],
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

            init() {
                // Default week start to next Monday (or today if Monday)
                const now = new Date();
                const dayOfWeek = now.getDay();
                const daysUntilMonday = dayOfWeek <= 1 ? (1 - dayOfWeek) : (8 - dayOfWeek);
                const nextMonday = new Date(now);
                nextMonday.setDate(now.getDate() + daysUntilMonday);
                this.weekStart = nextMonday.toISOString().split('T')[0];

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
                return this.slots.every((s) => s.time);
            },
            get slotsSetCount() {
                return this.slots.filter((s) => s.time).length;
            },
            get summaryLine() {
                const nets = this.networks
                    .map((n) => n === 'instagram' ? 'Instagram' : n === 'facebook' ? 'Facebook' : 'TikTok')
                    .join(' + ');
                return `${this.posts.length} posts \u00b7 Mon\u2013Fri \u00b7 ${nets || 'No networks selected'}`;
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
                return this.allSlotsSet
                    && this.networks.length > 0
                    && this.posts.every((p, i) => p.caption?.trim() && (i < this.slots.length || p.timeOverride))
                    && !this.saving;
            },

            slotDate(i) {
                if (!this.weekStart) return '';
                const d = new Date(this.weekStart);
                d.setDate(d.getDate() + i);
                return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
            },
            postSlotLabel(i) {
                const slot = this.slots[i];
                if (!slot) return 'No slot';
                return `${slot.day} ${slot.time || '\u2014'}`;
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
                    const dayMap = { Mon: 'mon', Tue: 'tue', Wed: 'wed', Thu: 'thu', Fri: 'fri' };
                    const response = await fetch(`/publish/batches/${this.batchId}/arm`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `arm_batch_${this.batchId}`,
                        },
                        body: JSON.stringify({
                            week_start: this.weekStart,
                            slots: this.slots.map((s) => ({ day: dayMap[s.day], time: s.time })),
                            default_networks: this.networks,
                            posts: this.posts.map((p) => ({
                                post_id: p.id,
                                caption: p.caption.trim(),
                                time_override: p.timeOverride || null,
                                networks_override: p.networksOverride,
                            })),
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
        };
    };
})();
