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

    window.publishSchedulerComponent = function (options = {}) {
        return {
            postId: options.postId,
            scheduledDateTime: '',
            selectedNetworks: (options.initialNetworks || []).filter((network) => ['facebook', 'instagram'].includes(network)),
            publishCaption: options.caption || '',
            metaConnection: options.meta || {},
            tiktokConnection: options.tiktok || {},
            facebookResult: options.facebookResult || {},
            instagramResult: options.instagramResult || {},
            tiktokResult: options.tiktokResult || {},
            videoUrl: options.videoUrl,
            metaConnectUrl: options.metaConnectUrl,
            tiktokConnectUrl: options.tiktokConnectUrl,
            selectedPrivacyLevel: '',
            disableComment: false,
            disableDuet: false,
            disableStitch: false,
            minDateTime: '',
            saving: false,
            tiktokPosting: false,
            tiktokUploading: false,
            successMessage: '',
            errorMessage: '',

            init() {
                const now = new Date();
                const minDate = new Date(now.getTime() + (60 * 60 * 1000));
                this.minDateTime = window.zoneDateToLocalValue(minDate, BERLIN_TIME_ZONE);

                if (options.scheduledAt) {
                    this.scheduledDateTime = window.zoneDateToLocalValue(new Date(options.scheduledAt), BERLIN_TIME_ZONE);
                }
                const creatorInfo = this.tiktokCreatorInfo;
                const privacyOptions = this.tiktokPrivacyOptions();
                this.selectedPrivacyLevel = privacyOptions.includes('SELF_ONLY') ? 'SELF_ONLY' : (privacyOptions[0] || 'SELF_ONLY');
                this.disableComment = Boolean(creatorInfo.comment_disabled);
                this.disableDuet = Boolean(creatorInfo.duet_disabled);
                this.disableStitch = Boolean(creatorInfo.stitch_disabled);

                if (this.selectedNetworks.length === 0) {
                    if (this.instagramAvailable) this.selectedNetworks.push('instagram');
                    if (this.facebookAvailable) this.selectedNetworks.push('facebook');
                }

                if (window.location.hash === `#post-${this.postId}`) {
                    const card = document.getElementById(`post-${this.postId}`);
                    if (card) {
                        card.classList.add('ring-2', 'ring-indigo-400', 'ring-offset-2');
                        setTimeout(() => {
                            card.classList.remove('ring-2', 'ring-indigo-400', 'ring-offset-2');
                        }, 2500);
                    }
                }
            },
            getUTCDisplay() {
                if (!this.scheduledDateTime) return '';
                const utcDate = window.zonedLocalValueToUtcDate(this.scheduledDateTime, BERLIN_TIME_ZONE);
                if (!utcDate) return '';
                return `${utcDate.toISOString().replace('T', ' ').substring(0, 16)} UTC`;
            },
            get facebookAvailable() {
                return Boolean(this.metaConnection?.selected_page?.id);
            },
            get instagramAvailable() {
                return Boolean(this.metaConnection?.selected_instagram?.id);
            },
            get tiktokCreatorInfo() {
                return this.tiktokConnection?.creator_info || {};
            },
            metaPublishReady() {
                return Boolean(this.metaConnection?.publish_ready);
            },
            metaStatusLabel() {
                if (this.metaConnection?.publish_ready) return 'Ready';
                if (this.metaConnection?.status === 'connected') return 'Action needed';
                if (this.metaConnection?.status === 'error') return 'Reconnect';
                return 'Connect';
            },
            metaStatusClass() {
                if (this.metaConnection?.publish_ready) return 'bg-green-100 text-green-700';
                if (this.metaConnection?.status === 'connected') return 'bg-amber-100 text-amber-700';
                if (this.metaConnection?.status === 'error') return 'bg-red-100 text-red-700';
                return 'bg-gray-100 text-gray-700';
            },
            metaTargetLabel() {
                const page = this.metaConnection?.selected_page || {};
                const instagram = this.metaConnection?.selected_instagram || {};
                if (page.id && instagram.id) {
                    return `${page.name} + @${instagram.username || instagram.id}`;
                }
                return 'Choose the Page and connected Instagram account';
            },
            metaReadinessReason() {
                return this.metaConnection?.readiness_reason || 'Connect Meta before publishing.';
            },
            tiktokReadinessClass() {
                return this.tiktokConnection?.publish_ready
                    ? 'border-green-200 bg-green-50'
                    : this.tiktokConnection?.draft_ready
                        ? 'border-amber-200 bg-amber-50'
                        : 'border-gray-200 bg-white';
            },
            tiktokReadinessBadgeClass() {
                if (this.tiktokConnection?.publish_ready) return 'bg-green-100 text-green-700';
                if (this.tiktokConnection?.draft_ready) return 'bg-amber-100 text-amber-700';
                if (this.tiktokConnection?.status === 'reconnect_required') return 'bg-red-100 text-red-700';
                return 'bg-gray-100 text-gray-700';
            },
            tiktokReadinessLabel() {
                if (this.tiktokConnection?.publish_ready) return 'Ready';
                if (this.tiktokConnection?.draft_ready) return 'Draft only';
                if (this.tiktokConnection?.status === 'reconnect_required') return 'Reconnect';
                if (this.tiktokConnection?.status === 'connected') return 'Not ready';
                return 'Connect';
            },
            tiktokReadinessCopy() {
                return this.tiktokConnection?.readiness_reason || 'Connect TikTok before posting.';
            },
            tiktokAccountLabel() {
                return this.tiktokConnection?.display_name || this.tiktokConnection?.open_id || 'No TikTok account connected';
            },
            tiktokPrivacyOptions() {
                return this.tiktokCreatorInfo?.privacy_level_options || [];
            },
            tiktokHasCreatorInfo() {
                return this.tiktokPrivacyOptions().length > 0;
            },
            tiktokErrorHint() {
                if (!this.videoUrl) return 'Generate the video before posting to TikTok.';
                if (!this.publishCaption.trim()) return 'Add a caption before posting to TikTok.';
                if (!this.tiktokConnection?.status || this.tiktokConnection?.status === 'disconnected') return 'Connect TikTok first.';
                if (!this.tiktokConnection?.publish_ready) return this.tiktokReadinessCopy();
                return '';
            },
            networkAvailable(network) {
                if (network === 'facebook') return this.facebookAvailable;
                if (network === 'instagram') return this.instagramAvailable;
                return false;
            },
            networkButtonClass(network) {
                if (this.selectedNetworks.includes(network)) {
                    return network === 'facebook'
                        ? 'bg-blue-50 border-blue-500 ring-2 ring-blue-500 cursor-pointer'
                        : 'bg-purple-50 border-purple-500 ring-2 ring-purple-500 cursor-pointer';
                }
                if (!this.networkAvailable(network)) {
                    return 'bg-amber-50 border-amber-300 cursor-pointer hover:border-amber-400';
                }
                return network === 'facebook'
                    ? 'bg-white border-gray-300 hover:border-blue-300 cursor-pointer'
                    : 'bg-white border-gray-300 hover:border-purple-300 cursor-pointer';
            },
            toggleNetwork(network) {
                if (!this.networkAvailable(network)) {
                    this.openAccountsHub('meta');
                    return;
                }
                if (this.selectedNetworks.includes(network)) {
                    this.selectedNetworks = this.selectedNetworks.filter((value) => value !== network);
                    return;
                }
                this.selectedNetworks = [...this.selectedNetworks, network];
            },
            openAccountsHub(network) {
                if (network === 'tiktok') {
                    const accountsHub = window.Alpine?.store('accountsHub');
                    if (accountsHub) {
                        accountsHub.open('tiktok', { postId: this.postId });
                        return;
                    }
                    window.location.assign(this.tiktokConnectUrl);
                    return;
                }
                const accountsHub = window.Alpine?.store('accountsHub');
                if (accountsHub) {
                    accountsHub.open('meta', { postId: this.postId });
                    return;
                }
                window.location.assign(this.metaConnectUrl);
            },
            canSave() {
                return this.scheduledDateTime
                    && Boolean(this.videoUrl)
                    && this.publishCaption.trim().length > 0
                    && this.selectedNetworks.length > 0
                    && this.selectedNetworks.every((network) => this.networkAvailable(network))
                    && !this.saving;
            },
            canPostTikTok() {
                return this.tiktokConnection?.publish_ready
                    && this.tiktokHasCreatorInfo()
                    && Boolean(this.videoUrl)
                    && this.publishCaption.trim().length > 0
                    && Boolean(this.selectedPrivacyLevel)
                    && !this.tiktokPosting;
            },
            canUploadTikTokDraft() {
                return this.tiktokConnection?.draft_ready
                    && Boolean(this.videoUrl)
                    && this.publishCaption.trim().length > 0
                    && !this.tiktokUploading;
            },
            resultClass(status) {
                if (status === 'published') return 'bg-green-100 text-green-700';
                if (status === 'failed') return 'bg-red-100 text-red-700';
                if (status === 'publishing' || status === 'processing' || status === 'awaiting_user_action') return 'bg-amber-100 text-amber-700';
                return 'bg-gray-100 text-gray-700';
            },
            tiktokResultClass() {
                return this.resultClass(this.tiktokResult?.status);
            },
            tiktokStatusLabel() {
                if (this.tiktokResult?.status === 'awaiting_user_action') return 'Awaiting creator action';
                if (this.tiktokResult?.provider_status) return this.tiktokResult.provider_status;
                return this.tiktokResult?.status || 'idle';
            },
            hasPublishResults() {
                return Object.keys(this.facebookResult || {}).length > 0
                    || Object.keys(this.instagramResult || {}).length > 0
                    || Object.keys(this.tiktokResult || {}).length > 0;
            },
            async saveSchedule() {
                if (!this.canSave()) return;
                this.saving = true;
                this.successMessage = '';
                this.errorMessage = '';
                try {
                    const utcDate = window.zonedLocalValueToUtcDate(this.scheduledDateTime, BERLIN_TIME_ZONE);
                    if (!utcDate) {
                        throw new Error('Invalid scheduled time');
                    }
                    const response = await fetch(`/publish/posts/${this.postId}/schedule`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `schedule_${this.postId}`,
                        },
                        body: JSON.stringify({
                            post_id: this.postId,
                            scheduled_at: utcDate.toISOString(),
                            publish_caption: this.publishCaption.trim(),
                            social_networks: this.selectedNetworks,
                        }),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'Publish plan saved successfully.';
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                } catch (error) {
                    this.errorMessage = error.message || 'Failed to save schedule';
                    console.error('Save schedule error:', error);
                } finally {
                    this.saving = false;
                }
            },
            async postToTikTok() {
                if (!this.canPostTikTok()) {
                    if (!this.tiktokConnection?.publish_ready) {
                        this.openAccountsHub('tiktok');
                    }
                    return;
                }
                this.tiktokPosting = true;
                this.successMessage = '';
                this.errorMessage = '';
                try {
                    const response = await fetch('/api/tiktok/publish', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `tiktok_direct_${this.postId}`,
                        },
                        body: JSON.stringify({
                            post_id: this.postId,
                            caption: this.publishCaption.trim(),
                            privacy_level: this.selectedPrivacyLevel,
                            disable_comment: this.disableComment,
                            disable_duet: this.disableDuet,
                            disable_stitch: this.disableStitch,
                        }),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'TikTok post submitted successfully.';
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                } catch (error) {
                    this.errorMessage = error.message || 'TikTok publish failed';
                } finally {
                    this.tiktokPosting = false;
                }
            },
            async uploadTikTokDraft() {
                if (!this.canUploadTikTokDraft()) {
                    if (!this.tiktokConnection?.draft_ready) {
                        this.openAccountsHub('tiktok');
                    }
                    return;
                }
                this.tiktokUploading = true;
                this.successMessage = '';
                this.errorMessage = '';
                try {
                    const response = await fetch('/api/tiktok/upload-draft', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Correlation-ID': `tiktok_${this.postId}`,
                        },
                        body: JSON.stringify({
                            post_id: this.postId,
                            caption: this.publishCaption.trim(),
                        }),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'TikTok draft export submitted successfully.';
                    setTimeout(() => {
                        window.location.reload();
                    }, 1000);
                } catch (error) {
                    this.errorMessage = error.message || 'TikTok draft upload failed';
                } finally {
                    this.tiktokUploading = false;
                }
            },
        };
    };
})();
