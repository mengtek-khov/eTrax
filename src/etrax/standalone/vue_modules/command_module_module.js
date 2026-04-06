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

  function normalizeTargetCommandKey(rawValue) {
    return String(rawValue || "").trim();
  }

  moduleSystem.register({
    type: "command_module",
    label: "command_module",
    defaultStep() {
      return {
        module_type: "command_module",
        text_template: "",
        parse_mode: "",
        target_command_key: "",
        run_if_context_keys: "",
        skip_if_context_keys: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "command_module",
        text_template: "",
        parse_mode: "",
        target_command_key: normalizeTargetCommandKey(source.target_command_key),
        run_if_context_keys: Array.isArray(source.run_if_context_keys)
          ? source.run_if_context_keys.join("\n")
          : String(source.run_if_context_keys || ""),
        skip_if_context_keys: Array.isArray(source.skip_if_context_keys)
          ? source.skip_if_context_keys.join("\n")
          : String(source.skip_if_context_keys || ""),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "command_module") {
        return null;
      }
      return {
        module_type: "command_module",
        text_template: "",
        parse_mode: "",
        target_command_key: normalizeTargetCommandKey(parts[1] || ""),
        run_if_context_keys: String(parts[2] || ""),
        skip_if_context_keys: String(parts[3] || ""),
      };
    },
    formatChain(step) {
      const payload = {
        module_type: "command_module",
        target_command_key: normalizeTargetCommandKey(step.target_command_key),
      };
      const runIfContextKeys = splitContextKeyLines(step.run_if_context_keys || "");
      const skipIfContextKeys = splitContextKeyLines(step.skip_if_context_keys || "");
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
      const target = normalizeTargetCommandKey(step.target_command_key);
      return `#${stepNo} command_module - ${target || "(select command key)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'command_module')">Existing Command Key</label>` +
        `<input v-if="isStepType(${ctx}, 'command_module')" list="command-key-options" placeholder="route" :value="currentStepField(${ctx}, 'target_command_key')" @input="updateCurrentStepField(${ctx}, 'target_command_key', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'command_module')">Loads and runs the selected existing command pipeline inside the current route.</p>` +
        `<label v-if="isStepType(${ctx}, 'command_module')">Run If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'command_module')">` +
        `<select :value="contextKeyDraft(${ctx}, 'run_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'run_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'command-module-run-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'run_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'command_module')" placeholder="One rule per line&#10;Example: profile.i_am_18=true" :value="currentStepField(${ctx}, 'run_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'run_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'command_module')">Use a plain key or a value rule like profile.i_am_18=true. All run_if rules must match before the command pipeline runs.</p>` +
        `<label v-if="isStepType(${ctx}, 'command_module')">Skip If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'command_module')">` +
        `<select :value="contextKeyDraft(${ctx}, 'skip_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'skip_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'command-module-skip-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'skip_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'command_module')" placeholder="One rule per line&#10;Example: profile.i_am_18=false" :value="currentStepField(${ctx}, 'skip_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'skip_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'command_module')">If any skip_if rule matches, including value rules like profile.i_am_18=false, the command pipeline is not loaded.</p>`
      );
    },
  });
})(window);
