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

  function normalizeLiveLocationMode(source) {
    const requireLiveLocation = Boolean(source && source.require_live_location);
    if (!requireLiveLocation) {
      return {
        require_live_location: false,
        find_closest_saved_location: false,
        match_closest_saved_location: false,
        track_breadcrumb: false,
      };
    }
    if (Boolean(source && source.track_breadcrumb)) {
      return {
        require_live_location: true,
        find_closest_saved_location: false,
        match_closest_saved_location: false,
        track_breadcrumb: true,
      };
    }
    if (Boolean(source && source.match_closest_saved_location)) {
      return {
        require_live_location: true,
        find_closest_saved_location: false,
        match_closest_saved_location: true,
        track_breadcrumb: false,
      };
    }
    if (Boolean(source && source.find_closest_saved_location)) {
      return {
        require_live_location: true,
        find_closest_saved_location: true,
        match_closest_saved_location: false,
        track_breadcrumb: false,
      };
    }
    return {
      require_live_location: true,
      find_closest_saved_location: false,
      match_closest_saved_location: false,
      track_breadcrumb: false,
    };
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
        success_text_template: "",
        closest_location_group_text_template: "",
        closest_location_group_send_timing: "end",
        closest_location_group_send_after_step: "",
        invalid_text_template: "You are at the wrong location.",
        require_live_location: false,
        find_closest_saved_location: false,
        match_closest_saved_location: false,
        closest_location_tolerance_meters: "100",
        track_breadcrumb: false,
        breadcrumb_interval_minutes: "",
        breadcrumb_min_distance_meters: "5",
        breadcrumb_started_text_template: "",
        breadcrumb_interrupted_text_template: "",
        breadcrumb_resumed_text_template: "",
        breadcrumb_ended_text_template: "",
        run_if_context_keys: "",
        skip_if_context_keys: "",
      };
    },
    parsePrimary(source) {
      const liveLocationMode = normalizeLiveLocationMode(source);
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
        closest_location_group_text_template: source.closest_location_group_text_template
          ? String(source.closest_location_group_text_template)
          : "",
        closest_location_group_send_timing: source.closest_location_group_send_timing
          ? String(source.closest_location_group_send_timing)
          : "end",
        closest_location_group_send_after_step: source.closest_location_group_send_after_step
          ? String(source.closest_location_group_send_after_step)
          : "",
        invalid_text_template: source.invalid_text_template ? String(source.invalid_text_template) : "",
        require_live_location: liveLocationMode.require_live_location,
        find_closest_saved_location: liveLocationMode.find_closest_saved_location,
        match_closest_saved_location: liveLocationMode.match_closest_saved_location,
        closest_location_tolerance_meters: source.closest_location_tolerance_meters
          ? String(source.closest_location_tolerance_meters)
          : liveLocationMode.find_closest_saved_location || liveLocationMode.match_closest_saved_location
            ? "100"
            : "",
        track_breadcrumb: liveLocationMode.track_breadcrumb,
        breadcrumb_interval_minutes: source.breadcrumb_interval_minutes ? String(source.breadcrumb_interval_minutes) : "",
        breadcrumb_min_distance_meters: source.breadcrumb_min_distance_meters
          ? String(source.breadcrumb_min_distance_meters)
          : liveLocationMode.track_breadcrumb
            ? "5"
            : "",
        breadcrumb_started_text_template: source.breadcrumb_started_text_template ? String(source.breadcrumb_started_text_template) : "",
        breadcrumb_interrupted_text_template: source.breadcrumb_interrupted_text_template ? String(source.breadcrumb_interrupted_text_template) : "",
        breadcrumb_resumed_text_template: source.breadcrumb_resumed_text_template ? String(source.breadcrumb_resumed_text_template) : "",
        breadcrumb_ended_text_template: source.breadcrumb_ended_text_template ? String(source.breadcrumb_ended_text_template) : "",
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
      const liveLocationMode = normalizeLiveLocationMode(step);
      const requireLiveLocation = liveLocationMode.require_live_location;
      const findClosestSavedLocation = liveLocationMode.find_closest_saved_location;
      const matchClosestSavedLocation = liveLocationMode.match_closest_saved_location;
      const closestLocationToleranceMeters = String(step.closest_location_tolerance_meters || "").trim();
      const trackBreadcrumb = liveLocationMode.track_breadcrumb;
      const breadcrumbIntervalMinutes = String(step.breadcrumb_interval_minutes || "").trim();
      const breadcrumbMinDistanceMeters = String(step.breadcrumb_min_distance_meters || "").trim();
      const invalidTextTemplate = String(step.invalid_text_template || "").trim();
      const closestLocationGroupTextTemplate = String(step.closest_location_group_text_template || "").trim();
      const closestLocationGroupSendTimingRaw = String(step.closest_location_group_send_timing || "").trim().toLowerCase();
      const closestLocationGroupSendTiming = closestLocationGroupSendTimingRaw === "immediate" || closestLocationGroupSendTimingRaw === "after_step"
        ? closestLocationGroupSendTimingRaw
        : "end";
      const closestLocationGroupSendAfterStep = String(step.closest_location_group_send_after_step || "").trim();
      const breadcrumbStartedText = String(step.breadcrumb_started_text_template || "").trim();
      const breadcrumbInterruptedText = String(step.breadcrumb_interrupted_text_template || "").trim();
      const breadcrumbResumedText = String(step.breadcrumb_resumed_text_template || "").trim();
      const breadcrumbEndedText = String(step.breadcrumb_ended_text_template || "").trim();
      const runIfContextKeys = splitContextKeyLines(step.run_if_context_keys || "");
      const skipIfContextKeys = splitContextKeyLines(step.skip_if_context_keys || "");
      if (parseMode) {
        payload.parse_mode = parseMode;
      }
      if (requireLiveLocation) {
        payload.require_live_location = true;
      }
      if (findClosestSavedLocation) {
        payload.find_closest_saved_location = true;
        if (closestLocationGroupTextTemplate) {
          payload.closest_location_group_text_template = closestLocationGroupTextTemplate;
          payload.closest_location_group_send_timing = closestLocationGroupSendTiming;
          if (closestLocationGroupSendTiming === "after_step" && closestLocationGroupSendAfterStep) {
            payload.closest_location_group_send_after_step = closestLocationGroupSendAfterStep;
          }
        }
      }
      if (matchClosestSavedLocation) {
        payload.match_closest_saved_location = true;
        if (closestLocationToleranceMeters) {
          payload.closest_location_tolerance_meters = closestLocationToleranceMeters;
        }
        if (invalidTextTemplate) {
          payload.invalid_text_template = invalidTextTemplate;
        }
      }
      if (trackBreadcrumb) {
        payload.track_breadcrumb = true;
        if (breadcrumbIntervalMinutes) {
          payload.breadcrumb_interval_minutes = breadcrumbIntervalMinutes;
        }
        if (breadcrumbMinDistanceMeters) {
          payload.breadcrumb_min_distance_meters = breadcrumbMinDistanceMeters;
        }
        if (breadcrumbStartedText) {
          payload.breadcrumb_started_text_template = breadcrumbStartedText;
        }
        if (breadcrumbInterruptedText) {
          payload.breadcrumb_interrupted_text_template = breadcrumbInterruptedText;
        }
        if (breadcrumbResumedText) {
          payload.breadcrumb_resumed_text_template = breadcrumbResumedText;
        }
        if (breadcrumbEndedText) {
          payload.breadcrumb_ended_text_template = breadcrumbEndedText;
        }
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
        `<label class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_live_location')" @change="updateCurrentStepToggle(${ctx}, 'require_live_location', $event.target.checked); if (!$event.target.checked) { updateCurrentStepToggle(${ctx}, 'find_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'match_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'track_breadcrumb', false); }"><span>Allow Only Live Location</span></label>` +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location')">` +
        `<label>Live Location Mode</label>` +
        `<p class="hint">Choose how this step should treat the incoming live location stream.</p>` +
        `<div class="share-location-mode-grid">` +
        `<label :class="['checkbox', 'compact', 'share-location-mode', { 'is-selected': !currentStepChecked(${ctx}, 'find_closest_saved_location') && !currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb') }]"><input type="radio" :name="'share-location-live-mode-' + ${ctx}" :checked="!currentStepChecked(${ctx}, 'find_closest_saved_location') && !currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb')" @change="if ($event.target.checked) { updateCurrentStepToggle(${ctx}, 'find_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'match_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'track_breadcrumb', false); }"><span class="share-location-mode-copy"><span class="share-location-mode-title">Standard Live Location</span><span class="share-location-mode-note">Accept any live location share without checking against saved places.</span></span></label>` +
        `<label :class="['checkbox', 'compact', 'share-location-mode', { 'is-selected': currentStepChecked(${ctx}, 'find_closest_saved_location') && !currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb') }]"><input type="radio" :name="'share-location-live-mode-' + ${ctx}" :checked="currentStepChecked(${ctx}, 'find_closest_saved_location') && !currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb')" @change="if ($event.target.checked) { updateCurrentStepToggle(${ctx}, 'find_closest_saved_location', true); updateCurrentStepToggle(${ctx}, 'match_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'track_breadcrumb', false); }"><span class="share-location-mode-copy"><span class="share-location-mode-title">Find Closest Saved Location</span><span class="share-location-mode-note">Accept the share and attach the nearest saved location details for later steps.</span></span></label>` +
        `<label :class="['checkbox', 'compact', 'share-location-mode', { 'is-selected': currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb') }]"><input type="radio" :name="'share-location-live-mode-' + ${ctx}" :checked="currentStepChecked(${ctx}, 'match_closest_saved_location') && !currentStepChecked(${ctx}, 'track_breadcrumb')" @change="if ($event.target.checked) { updateCurrentStepToggle(${ctx}, 'find_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'match_closest_saved_location', true); updateCurrentStepToggle(${ctx}, 'track_breadcrumb', false); }"><span class="share-location-mode-copy"><span class="share-location-mode-title">Match Closest Saved Location</span><span class="share-location-mode-note">Only accept the live location when it is within your allowed distance tolerance.</span></span></label>` +
        `<label :class="['checkbox', 'compact', 'share-location-mode', { 'is-selected': currentStepChecked(${ctx}, 'track_breadcrumb') }]"><input type="radio" :name="'share-location-live-mode-' + ${ctx}" :checked="currentStepChecked(${ctx}, 'track_breadcrumb')" @change="if ($event.target.checked) { updateCurrentStepToggle(${ctx}, 'find_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'match_closest_saved_location', false); updateCurrentStepToggle(${ctx}, 'track_breadcrumb', true); }"><span class="share-location-mode-copy"><span class="share-location-mode-title">Track As Breadcrumb</span><span class="share-location-mode-note">Keep collecting follow-up live points as a breadcrumb trail until the session ends.</span></span></label>` +
        `</div>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'find_closest_saved_location')">The closest saved location details are added to context for later steps and message templates.</p>` +
        `<div v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'find_closest_saved_location')">` +
        `<label>Closest Location Group Message</label>` +
        `<textarea placeholder="Leave blank to skip group notification. Example: {user_first_name} checked in near {closest_location_name} at {location_latitude},{location_longitude}." :value="currentStepField(${ctx}, 'closest_location_group_text_template')" @input="updateCurrentStepField(${ctx}, 'closest_location_group_text_template', $event.target.value)"></textarea>` +
        `<p class="hint">If the matched saved location has a Telegram Group ID, this message is sent to that group.</p>` +
        `<div class="module-grid">` +
        `<div>` +
        `<label>Group Message Timing</label>` +
        `<select :value="currentStepField(${ctx}, 'closest_location_group_send_timing') || 'end'" @change="updateCurrentStepField(${ctx}, 'closest_location_group_send_timing', $event.target.value)">` +
        `<option value="immediate">Right Away</option>` +
        `<option value="end">At End</option>` +
        `<option value="after_step">After Step #</option>` +
        `</select>` +
        `</div>` +
        `<div v-if=\"(currentStepField(${ctx}, 'closest_location_group_send_timing') || 'end') === 'after_step'\">` +
        `<label>After Continuation Step</label>` +
        `<input type=\"number\" min=\"1\" step=\"1\" placeholder=\"4\" :value=\"currentStepField(${ctx}, 'closest_location_group_send_after_step')\" @input=\"updateCurrentStepField(${ctx}, 'closest_location_group_send_after_step', $event.target.value)\">` +
        `<p class=\"hint\">Uses the chained modules after this share_location step. Example: 4 means send after continuation step #4.</p>` +
        `</div>` +
        `</div>` +
        `</div>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'match_closest_saved_location')">` +
        `<div>` +
        `<label>Allowed Distance To Closest Saved Location (meters)</label>` +
        `<input type="number" min="0" step="0.1" placeholder="100" :value="currentStepField(${ctx}, 'closest_location_tolerance_meters')" @input="updateCurrentStepField(${ctx}, 'closest_location_tolerance_meters', $event.target.value)">` +
        `<p class="hint">Saved locations come from the standalone Locations page.</p>` +
        `</div>` +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'match_closest_saved_location')">` +
        `<label>Outside Distance Text</label>` +
        `<textarea placeholder="You are at the wrong location." :value="currentStepField(${ctx}, 'invalid_text_template')" @input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>` +
        `<p class="hint">Available keys include <code>{closest_location_name}</code>, <code>{closest_location_code}</code>, <code>{closest_location_distance_text}</code>, and <code>{closest_location_tolerance_text}</code>.</p>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location')">When enabled, later live-location updates are stored as breadcrumb points for this request.</p>` +
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
        `<div v-if="isStepType(${ctx}, 'share_location') && currentStepChecked(${ctx}, 'require_live_location') && currentStepChecked(${ctx}, 'track_breadcrumb')">` +
        `<label>Breadcrumb Started Text</label>` +
        `<textarea placeholder="Sent after the first live location is accepted." :value="currentStepField(${ctx}, 'breadcrumb_started_text_template')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_started_text_template', $event.target.value)"></textarea>` +
        `<label>Breadcrumb Interrupted Text</label>` +
        `<textarea placeholder="Sent when live location stops before breadcrumb is ended." :value="currentStepField(${ctx}, 'breadcrumb_interrupted_text_template')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_interrupted_text_template', $event.target.value)"></textarea>` +
        `<label>Breadcrumb Resumed Text</label>` +
        `<textarea placeholder="Sent when live location is shared again after interruption." :value="currentStepField(${ctx}, 'breadcrumb_resumed_text_template')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_resumed_text_template', $event.target.value)"></textarea>` +
        `<label>Breadcrumb Ended Text</label>` +
        `<textarea placeholder="Sent when the user taps End Breadcrumb." :value="currentStepField(${ctx}, 'breadcrumb_ended_text_template')" @input="updateCurrentStepField(${ctx}, 'breadcrumb_ended_text_template', $event.target.value)"></textarea>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'share_location')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_location')" ` +
        `placeholder="Leave blank for the automatic reply. Example: Closest saved location is {closest_location_name}." ` +
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

