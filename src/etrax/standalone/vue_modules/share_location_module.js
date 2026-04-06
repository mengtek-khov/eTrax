(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  function splitContextKeyLines(raw) {
    return String(raw || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  }

  function formatContextKeyLines(raw) {
    if (Array.isArray(raw)) {
      return raw.join("\n");
    }
    return String(raw || "");
  }

  moduleSystem.register({
    type: "share_location",
    label: "share_location",
    defaultStep() {
      return {
        module_type: "share_location",
        text_template: "Please share your location using the button below.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "Share My Location",
        success_text_template: "Thanks, your location was received.",
        require_live_location: false,
        track_breadcrumb: false,
        store_history_by_day: false,
        breadcrumb_interval_minutes: "",
        breadcrumb_min_distance_meters: "5",
        run_if_context_keys: "",
        skip_if_context_keys: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "share_location",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: source.button_text ? String(source.button_text) : "",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        require_live_location: source.require_live_location,
        track_breadcrumb: Boolean(source.track_breadcrumb),
        store_history_by_day: Boolean(source.store_history_by_day),
        breadcrumb_interval_minutes: source.breadcrumb_interval_minutes ? String(source.breadcrumb_interval_minutes) : "",
        breadcrumb_min_distance_meters: source.breadcrumb_min_distance_meters
          ? String(source.breadcrumb_min_distance_meters)
          : Boolean(source.track_breadcrumb)
            ? "5"
            : "",
        run_if_context_keys: formatContextKeyLines(source.run_if_context_keys),
        skip_if_context_keys: formatContextKeyLines(source.skip_if_context_keys),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "share_location") {
        return null;
      }
      return {
        module_type: "share_location",
        text_template: parts[1] || "",
        button_text: parts[2] || "",
        success_text_template: parts[3] || "",
        parse_mode: parts[4] || "",
        require_live_location: String(parts[5] || "").trim().toLowerCase() === "require_live_location",
        track_breadcrumb: String(parts[8] || "").trim().toLowerCase() === "track_breadcrumb",
        store_history_by_day: String(parts[9] || "").trim().toLowerCase() === "store_history_by_day",
        breadcrumb_interval_minutes: parts[10] || "",
        breadcrumb_min_distance_meters: parts[11] || "",
        run_if_context_keys: String(parts[6] || ""),
        skip_if_context_keys: String(parts[7] || ""),
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    formatChain(step) {
      const payload = {
        module_type: "share_location",
        text_template: String(step.text_template || "").trim(),
        button_text: String(step.button_text || "").trim(),
        success_text_template: String(step.success_text_template || "").trim(),
      };
      const parseMode = String(step.parse_mode || "").trim();
      const requireLiveLocation = Boolean(step.require_live_location);
      const trackBreadcrumb = Boolean(step.require_live_location) && Boolean(step.track_breadcrumb);
      const storeHistoryByDay = Boolean(step.store_history_by_day);
      const breadcrumbIntervalMinutes = String(step.breadcrumb_interval_minutes || "").trim();
      const breadcrumbMinDistanceMeters = String(step.breadcrumb_min_distance_meters || "").trim();
      const runIfContextKeys = splitContextKeyLines(step.run_if_context_keys || "");
      const skipIfContextKeys = splitContextKeyLines(step.skip_if_context_keys || "");
      if (parseMode) {
        payload.parse_mode = parseMode;
      }
      if (requireLiveLocation) {
        payload.require_live_location = true;
      }
      if (trackBreadcrumb) {
        payload.track_breadcrumb = true;
        if (breadcrumbIntervalMinutes) {
          payload.breadcrumb_interval_minutes = breadcrumbIntervalMinutes;
        }
        if (breadcrumbMinDistanceMeters) {
          payload.breadcrumb_min_distance_meters = breadcrumbMinDistanceMeters;
        }
      }
      if (storeHistoryByDay) {
        payload.store_history_by_day = true;
      }
      if (runIfContextKeys.length > 0) {
        payload.run_if_context_keys = runIfContextKeys;
      }
      if (skipIfContextKeys.length > 0) {
        payload.skip_if_context_keys = skipIfContextKeys;
      }
      return JSON.stringify(payload);
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const prompt = String(step.text_template || "").trim();
      const buttonText = String(step.button_text || "").trim();
      const preview = prompt.length > 32 ? `${prompt.slice(0, 32)}...` : prompt;
      return `#${stepNo} share_location - ${buttonText || "(share button)"} / ${preview || "(empty prompt)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'share_location')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_location')" ` +
        `placeholder="Ask the user to share their location" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div v-if="isStepType(${ctx}, 'share_location') && !currentStepChecked(${ctx}, 'require_live_location')">` +
        `<label>Button Text</label>` +
        `<input placeholder="Share My Location" ` +
        `:value="currentStepField(${ctx}, 'button_text')" ` +
        `@input="updateCurrentStepField(${ctx}, 'button_text', $event.target.value)">` +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'share_location')">` +
        `<label class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_live_location')" @change="updateCurrentStepToggle(${ctx}, 'require_live_location', $event.target.checked)"><span>Allow Only Live Location</span></label>` +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location')">` +
        `<label class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'track_breadcrumb')" @change="updateCurrentStepToggle(${ctx}, 'track_breadcrumb', $event.target.checked)"><span>Track As Breadcrumb</span></label>` +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'share_location')">` +
        `<label class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'store_history_by_day')" @change="updateCurrentStepToggle(${ctx}, 'store_history_by_day', $event.target.checked)"><span>Store Location/Breadcrumb By Day</span></label>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location')">When enabled, later live-location updates are stored as breadcrumb points for this request.</p>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'store_history_by_day')">Accepted location shares are appended to <code>location_history_by_day</code>. Accepted breadcrumb points are appended to <code>location_breadcrumb_by_day</code>.</p>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'track_breadcrumb')">` +
        `<div>` +
        `<label>Store Point Every (minutes)</label>` +
        `<input type="number" min="0" step="0.1" placeholder="10" :value="currentStepField(${ctx}, 'breadcrumb_interval_minutes')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_interval_minutes', $event.target.value)">` +
        `<p class="hint">Leave blank or 0 to disable time-based sampling.</p>` +
        `</div>` +
        `<div>` +
        `<label>Or When Moved (meters)</label>` +
        `<input type="number" min="0" step="0.1" placeholder="50" :value="currentStepField(${ctx}, 'breadcrumb_min_distance_meters')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_min_distance_meters', $event.target.value)">` +
        `<p class="hint">Default is 5 meters if you leave this blank.</p>` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'share_location')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_location')" ` +
        `placeholder="Shown after the user shares a location" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'share_location')">Run If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'share_location')">` +
        `<select :value="contextKeyDraft(${ctx}, 'run_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'run_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'share-location-run-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'run_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'share_location')" placeholder="One rule per line&#10;Example: profile.i_am_18=true" :value="currentStepField(${ctx}, 'run_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'run_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location')">All run_if rules must match before the share_location step runs.</p>` +
        `<label v-if="isStepType(${ctx}, 'share_location')">Skip If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'share_location')">` +
        `<select :value="contextKeyDraft(${ctx}, 'skip_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'skip_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'share-location-skip-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'skip_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'share_location')" placeholder="One rule per line&#10;Example: location_latitude" :value="currentStepField(${ctx}, 'skip_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'skip_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location')">If any skip_if rule matches, the share_location step is skipped.</p>`
      );
    },
  });
})(window);
