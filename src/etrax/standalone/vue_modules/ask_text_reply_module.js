(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "ask_text_reply",
    label: "ask_text_reply",
    defaultStep() {
      return {
        module_type: "ask_text_reply",
        text_template: "Please reply with text.",
        parse_mode: "",
        save_reply_to_key: "text_reply",
        success_text_template: "",
        invalid_text_template: "Please reply with a text message.",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "ask_text_reply",
        text_template: source.text_template ? String(source.text_template) : "Please reply with text.",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        save_reply_to_key: source.save_reply_to_key ? String(source.save_reply_to_key) : "text_reply",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        invalid_text_template: source.invalid_text_template
          ? String(source.invalid_text_template)
          : "Please reply with a text message.",
        require_finish_current_command: Boolean(source.require_finish_current_command),
        finish_current_command_text_template: source.finish_current_command_text_template
          ? String(source.finish_current_command_text_template)
          : "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "ask_text_reply") {
        return null;
      }
      return {
        module_type: "ask_text_reply",
        text_template: parts[1] || "Please reply with text.",
        save_reply_to_key: parts[2] || "text_reply",
        success_text_template: parts[3] || "",
        invalid_text_template: parts[4] || "Please reply with a text message.",
        parse_mode: parts[5] || "",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
      };
    },
    formatChain(step) {
      return JSON.stringify({
        module_type: "ask_text_reply",
        text_template: String(step.text_template || "Please reply with text."),
        parse_mode: String(step.parse_mode || "").trim(),
        save_reply_to_key: String(step.save_reply_to_key || "text_reply").trim() || "text_reply",
        success_text_template: String(step.success_text_template || "").trim(),
        invalid_text_template: String(step.invalid_text_template || "Please reply with a text message.").trim(),
        require_finish_current_command: Boolean(step.require_finish_current_command),
        finish_current_command_text_template: String(step.finish_current_command_text_template || "").trim(),
      });
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const saveKey = String(step.save_reply_to_key || "text_reply").trim() || "text_reply";
      return `#${stepNo} ask_text_reply - ${saveKey}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'ask_text_reply')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_text_reply')" ` +
        `placeholder="Please reply with text." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'ask_text_reply')">` +
        `<div>` +
        `<label>Save Reply To Context Key</label>` +
        `<input placeholder="text_reply" ` +
        `:value="currentStepField(${ctx}, 'save_reply_to_key')" ` +
        `@input="updateCurrentStepField(${ctx}, 'save_reply_to_key', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Parse Mode</label>` +
        `<select :value="currentStepField(${ctx}, 'parse_mode')" @change="updateCurrentStepField(${ctx}, 'parse_mode', $event.target.value)">` +
        `<option value="">None</option>` +
        `<option value="HTML">HTML</option>` +
        `<option value="Markdown">Markdown</option>` +
        `<option value="MarkdownV2">MarkdownV2</option>` +
        `</select>` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'ask_text_reply')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_text_reply')" ` +
        `placeholder="Optional message after saving {text_reply}" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_text_reply')">Invalid Reply Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_text_reply')" ` +
        `placeholder="Please reply with a text message." ` +
        `:value="currentStepField(${ctx}, 'invalid_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_text_reply')" class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_finish_current_command')" @change="updateCurrentStepToggle(${ctx}, 'require_finish_current_command', $event.target.checked)"><span>Require this reply before new actions</span></label>` +
        `<label v-if="isStepType(${ctx}, 'ask_text_reply')">Blocked Action Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_text_reply')" placeholder="Please finish the current command before starting a new one." :value="currentStepField(${ctx}, 'finish_current_command_text_template')" @input="updateCurrentStepField(${ctx}, 'finish_current_command_text_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
