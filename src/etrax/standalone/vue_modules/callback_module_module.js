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

  function normalizeTargetCallbackKey(rawValue) {
    return String(rawValue || "").trim();
  }

  moduleSystem.register({
    type: "callback_module",
    label: "callback_module",
    defaultStep() {
      return {
        module_type: "callback_module",
        text_template: "",
        parse_mode: "",
        target_callback_key: "",
        run_if_context_keys: "",
        skip_if_context_keys: "",
        save_callback_data_to_key: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "callback_module",
        text_template: "",
        parse_mode: "",
        target_callback_key: normalizeTargetCallbackKey(source.target_callback_key),
        run_if_context_keys: Array.isArray(source.run_if_context_keys)
          ? source.run_if_context_keys.join("\n")
          : String(source.run_if_context_keys || ""),
        skip_if_context_keys: Array.isArray(source.skip_if_context_keys)
          ? source.skip_if_context_keys.join("\n")
          : String(source.skip_if_context_keys || ""),
        save_callback_data_to_key: String(source.save_callback_data_to_key || ""),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "callback_module") {
        return null;
      }
      return {
        module_type: "callback_module",
        text_template: "",
        parse_mode: "",
        target_callback_key: normalizeTargetCallbackKey(parts[1] || ""),
        run_if_context_keys: String(parts[2] || ""),
        skip_if_context_keys: String(parts[3] || ""),
        save_callback_data_to_key: String(parts[4] || ""),
      };
    },
    formatChain(step) {
      const payload = {
        module_type: "callback_module",
        target_callback_key: normalizeTargetCallbackKey(step.target_callback_key),
      };
      const runIfContextKeys = splitContextKeyLines(step.run_if_context_keys || "");
      const skipIfContextKeys = splitContextKeyLines(step.skip_if_context_keys || "");
      if (runIfContextKeys.length > 0) {
        payload.run_if_context_keys = runIfContextKeys;
      }
      if (skipIfContextKeys.length > 0) {
        payload.skip_if_context_keys = skipIfContextKeys;
      }
      if (String(step.save_callback_data_to_key || "").trim()) {
        payload.save_callback_data_to_key = String(step.save_callback_data_to_key).trim();
      }
      return JSON.stringify(payload);
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const target = normalizeTargetCallbackKey(step.target_callback_key);
      return `#${stepNo} callback_module - ${target || "(select callback key)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'callback_module')">Existing Callback Key</label>` +
        `<input v-if="isStepType(${ctx}, 'callback_module')" list="callback-key-options" placeholder="share_contact" :value="currentStepField(${ctx}, 'target_callback_key')" @input="updateCurrentStepField(${ctx}, 'target_callback_key', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'callback_module')">Loads and runs the selected existing callback pipeline inside the current route.</p>` +
        `<label v-if="isStepType(${ctx}, 'callback_module')">Save Clicked Value To</label>` +
        `<input v-if="isStepType(${ctx}, 'callback_module')" placeholder="selected_option" :value="currentStepField(${ctx}, 'save_callback_data_to_key')" @input="updateCurrentStepField(${ctx}, 'save_callback_data_to_key', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'callback_module')">If set, this overrides inline_button Save Clicked Value To targets inside the loaded callback pipeline.</p>` +
        `<label v-if="isStepType(${ctx}, 'callback_module')">Run If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'callback_module')">` +
        `<select :value="contextKeyDraft(${ctx}, 'run_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'run_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'callback-module-run-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'run_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'callback_module')" placeholder="One rule per line&#10;Example: profile.i_am_18=true" :value="currentStepField(${ctx}, 'run_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'run_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'callback_module')">Use a plain key or a value rule like profile.i_am_18=true. All run_if rules must match before the callback pipeline runs.</p>` +
        `<label v-if="isStepType(${ctx}, 'callback_module')">Skip If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'callback_module')">` +
        `<select :value="contextKeyDraft(${ctx}, 'skip_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'skip_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'callback-module-skip-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'skip_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'callback_module')" placeholder="One rule per line&#10;Example: profile.i_am_18=false" :value="currentStepField(${ctx}, 'skip_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'skip_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'callback_module')">If any skip_if rule matches, including value rules like profile.i_am_18=false, the callback pipeline is not loaded.</p>`
      );
    },
  });
})(window);
