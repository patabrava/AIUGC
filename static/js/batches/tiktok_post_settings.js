(function () {
    window.tiktokPostSettings = function (options) {
        const creatorInfo = options.creatorInfo || {};
        const readinessStatus = options.readinessStatus || "disconnected";
        const initial = Object.assign(
            {
                title: "",
                privacyLevel: null,
                allowComment: false,
                allowDuet: false,
                allowStitch: false,
                commercialDisclosure: false,
                yourBrand: false,
                brandedContent: false,
                consentAcknowledged: false,
            },
            options.initial || {},
        );
        initial.consentAcknowledged = !!(
            initial.consent_acknowledged ||
            initial.consentAcknowledged
        );

        return {
            scope: options.scope || "post",
            postId: options.postId || null,
            batchId: options.batchId || null,
            readinessStatus,
            creatorInfo,
            accountAvatarUrl: options.accountAvatarUrl || "",
            privacyOptions: creatorInfo.privacy_level_options || [],
            commentDisabled: !!creatorInfo.comment_disabled,
            duetDisabled: !!creatorInfo.duet_disabled,
            stitchDisabled: !!creatorInfo.stitch_disabled,
            maxDurationSec: Number(creatorInfo.max_video_post_duration_sec || 0),
            durationSec: Number(options.durationSec || 0),
            saving: false,
            errorMessage: "",
            successMessage: "",
            settings: initial,

            privacyLabel(value) {
                switch (value) {
                    case "PUBLIC_TO_EVERYONE": return "Public · Anyone on TikTok";
                    case "MUTUAL_FOLLOW_FRIENDS": return "Friends · People you follow back";
                    case "FOLLOWER_OF_CREATOR": return "Followers · People who follow you";
                    case "SELF_ONLY": return "Only me · Private to you";
                    default: return value;
                }
            },

            get isBlocked() {
                return this.readinessStatus !== "publish_ready"
                    && this.readinessStatus !== "draft_ready";
            },

            get isOverDuration() {
                return this.maxDurationSec > 0
                    && this.durationSec > 0
                    && this.durationSec > this.maxDurationSec;
            },

            get disclosureChipLabel() {
                if (!this.settings.commercialDisclosure) return "";
                if (this.settings.brandedContent) return "Paid partnership";
                if (this.settings.yourBrand) return "Promotional content";
                return "";
            },

            get disclosureChipColor() {
                return this.settings.brandedContent ? "bg-amber-100 text-amber-800" : "bg-sky-100 text-sky-800";
            },

            get privateDisabledByBranded() {
                return this.settings.commercialDisclosure && this.settings.brandedContent;
            },

            get disclosureRequiresSubtype() {
                return this.settings.commercialDisclosure
                    && !this.settings.yourBrand
                    && !this.settings.brandedContent;
            },

            get consentLabel() {
                if (!this.settings.commercialDisclosure) {
                    return "By posting, you agree to TikTok's Music Usage Confirmation.";
                }
                if (this.settings.brandedContent) {
                    return "By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation.";
                }
                return "By posting, you agree to TikTok's Music Usage Confirmation.";
            },

            get isValid() {
                if (this.isBlocked) return false;
                if (this.isOverDuration) return false;
                if (!this.settings.title.trim()) return false;
                if (!this.settings.privacyLevel) return false;
                if (!this.privacyOptions.includes(this.settings.privacyLevel)) return false;
                if (this.disclosureRequiresSubtype) return false;
                if (this.settings.brandedContent && this.settings.privacyLevel === "SELF_ONLY") return false;
                if (this.scope === "post" && !this.settings.consentAcknowledged) return false;
                return true;
            },

            togglePrivacy(option) {
                if (this.privateDisabledByBranded && option === "SELF_ONLY") return;
                this.settings.privacyLevel = option;
            },

            buildPayload() {
                return {
                    title: this.settings.title.trim(),
                    privacy_level: this.settings.privacyLevel,
                    allow_comment: !this.commentDisabled && this.settings.allowComment,
                    allow_duet: !this.duetDisabled && this.settings.allowDuet,
                    allow_stitch: !this.stitchDisabled && this.settings.allowStitch,
                    commercial_disclosure: this.settings.commercialDisclosure,
                    your_brand: this.settings.commercialDisclosure && this.settings.yourBrand,
                    branded_content: this.settings.commercialDisclosure && this.settings.brandedContent,
                    consent_acknowledged: this.scope === 'post' ? !!this.settings.consentAcknowledged : false,
                };
            },

            async save() {
                if (!this.isValid) return;
                this.saving = true;
                this.errorMessage = "";
                this.successMessage = "";
                try {
                    const url = this.scope === "batch"
                        ? `/publish/batches/${this.batchId}/tiktok-defaults`
                        : `/publish/posts/${this.postId}/tiktok-settings`;
                    const body = this.scope === "batch"
                        ? Object.assign({ title_template: this.settings.title.trim() }, this.buildPayload())
                        : this.buildPayload();
                    const response = await fetch(url, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = "Saved.";
                    if (typeof options.onSaved === "function") {
                        options.onSaved(this.buildPayload());
                    }
                } catch (err) {
                    this.errorMessage = err?.message || "Failed to save TikTok settings.";
                } finally {
                    this.saving = false;
                }
            },
        };
    };
})();
