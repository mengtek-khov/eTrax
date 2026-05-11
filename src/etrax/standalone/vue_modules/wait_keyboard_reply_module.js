(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "wait_keyboard_reply",
    label: "wait_keyboard_reply",
    defaultStep() {
      return {
        module_type: "wait_keyboard_reply",
        text_template: "Please choose one option.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        button_text: "keyboard_reply",
        save_reply_to_key: "keyboard_reply",
        click_timestamp_format: "%Y-%m-%d %H:%M:%S",
        success_text_template: "",
        invalid_text_template: "Please choose from the keyboard.",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
      };
    },
    parsePrimary(source, helpers) {
      const buttons = Array.isArray(source.buttons)
        ? helpers.normalizeKeyboardReplyButtons(source.buttons)
        : helpers.parseKeyboardReplyButtons(source.inline_buttons || "");
      const saveKey = source.save_reply_to_key || source.button_text || "keyboard_reply";
      return {
        module_type: "wait_keyboard_reply",
        text_template: source.text_template ? String(source.text_template) : "Please choose one option.",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons,
        button_text: String(saveKey || "keyboard_reply"),
        save_reply_to_key: String(saveKey || "keyboard_reply"),
        click_timestamp_format: source.click_timestamp_format
          ? String(source.click_timestamp_format)
          : "%Y-%m-%d %H:%M:%S",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        invalid_text_template: source.invalid_text_template
          ? String(source.invalid_text_template)
          : "Please choose from the keyboard.",
        require_finish_current_command: Boolean(source.require_finish_current_command),
        finish_current_command_text_template: source.finish_current_command_text_template
          ? String(source.finish_current_command_text_template)
          : "",
      };
    },
    parseChain(parts, helpers) {
      if (String(parts[0] || "").trim().toLowerCase() !== "wait_keyboard_reply") {
        return null;
      }
      let buttonsRaw = [];
      try {
        buttonsRaw = JSON.parse(parts[2] || "[]");
      } catch (_error) {
        buttonsRaw = [];
      }
      const saveKey = parts[3] || "keyboard_reply";
      return {
        module_type: "wait_keyboard_reply",
        text_template: parts[1] || "Please choose one option.",
        buttons: helpers.normalizeKeyboardReplyButtons(buttonsRaw),
        save_reply_to_key: saveKey,
        button_text: saveKey,
        click_timestamp_format: "%Y-%m-%d %H:%M:%S",
        success_text_template: parts[4] || "",
        invalid_text_template: parts[5] || "Please choose from the keyboard.",
        parse_mode: parts[6] || "",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
        title: "Main Menu",
        items: [],
      };
    },
    formatChain(step, helpers) {
      return JSON.stringify({
        module_type: "wait_keyboard_reply",
        text_template: String(step.text_template || "Please choose one option."),
        parse_mode: String(step.parse_mode || "").trim(),
        buttons: helpers.normalizeKeyboardReplyButtons(step.buttons || []),
        save_reply_to_key: String(step.save_reply_to_key || step.button_text || "keyboard_reply").trim() || "keyboard_reply",
        click_timestamp_format: String(step.click_timestamp_format || "%Y-%m-%d %H:%M:%S").trim() || "%Y-%m-%d %H:%M:%S",
        success_text_template: String(step.success_text_template || "").trim(),
        invalid_text_template: String(step.invalid_text_template || "Please choose from the keyboard.").trim(),
        require_finish_current_command: Boolean(step.require_finish_current_command),
        finish_current_command_text_template: String(step.finish_current_command_text_template || "").trim(),
      });
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const saveKey = String(step.save_reply_to_key || step.button_text || "keyboard_reply").trim();
      const buttonCount = Array.isArray(step.buttons) ? step.buttons.length : 0;
      return `#${stepNo} wait_keyboard_reply - ${saveKey} (${buttonCount} choices)`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'wait_keyboard_reply')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'wait_keyboard_reply')" ` +
        `placeholder="Please choose one option." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'wait_keyboard_reply')">` +
        `<div>` +
        `<label>Save Reply To Context Key</label>` +
        `<input placeholder="keyboard_reply" ` +
        `:value="currentStepField(${ctx}, 'save_reply_to_key') || currentStepField(${ctx}, 'button_text')" ` +
        `@input="updateCurrentStepField(${ctx}, 'save_reply_to_key', $event.target.value); updateCurrentStepField(${ctx}, 'button_text', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Click Timestamp Format</label>` +
        `<input placeholder="%Y-%m-%d %H:%M:%S" ` +
        `:value="currentStepField(${ctx}, 'click_timestamp_format')" ` +
        `@input="updateCurrentStepField(${ctx}, 'click_timestamp_format', $event.target.value)">` +
        `</div>` +
        `</div>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'wait_keyboard_reply')">` +
        `<label class="hint">Button Text</label>` +
        `<input class="inline-button-input" placeholder="Yes" ` +
        `:value="inlineButtonDraft(${ctx}).text" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'text', $event.target.value)">` +
        `<label class="hint">Saved Value</label>` +
        `<input class="inline-button-input" placeholder="yes" ` +
        `:value="inlineButtonDraft(${ctx}).actual_value" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'actual_value', $event.target.value)">` +
        `<label class="hint">Row</label>` +
        `<input class="inline-button-input" type="number" min="1" placeholder="1" ` +
        `:value="inlineButtonDraft(${ctx}).row" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'row', $event.target.value)">` +
        `<button type="button" class="secondary" @click="saveKeyboardButton(${ctx})">[[ inlineButtonDraft(${ctx}).edit_index === null ? 'Add Choice' : 'Update Choice' ]]</button>` +
        `<button type="button" class="secondary" v-if="inlineButtonDraft(${ctx}).edit_index !== null" @click="cancelKeyboardButtonEdit(${ctx})">Cancel</button>` +
        `</div>` +
        `<div class="module-list" v-if="isStepType(${ctx}, 'wait_keyboard_reply')">` +
        `<div class="module-list-row" v-for="(button, buttonIndex) in currentStepButtons(${ctx})" :key="'wait-kbd-' + buttonIndex">` +
        `<div class="module-list-meta">[[ keyboardButtonLabel(button, buttonIndex) ]] -> [[ button.value || button.actual_value || button.text ]]</div>` +
        `<div class="module-list-actions">` +
        `<button type="button" @click="editKeyboardButton(${ctx}, buttonIndex)">Edit</button>` +
        `<button type="button" :disabled="buttonIndex === 0" @click="moveKeyboardButtonUp(${ctx}, buttonIndex)">Up</button>` +
        `<button type="button" :disabled="buttonIndex >= currentStepButtons(${ctx}).length - 1" @click="moveKeyboardButtonDown(${ctx}, buttonIndex)">Down</button>` +
        `<button type="button" @click="removeKeyboardButton(${ctx}, buttonIndex)">Remove</button>` +
        `</div>` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'wait_keyboard_reply')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'wait_keyboard_reply')" ` +
        `placeholder="Optional message after a valid choice" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'wait_keyboard_reply')">Invalid Reply Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'wait_keyboard_reply')" ` +
        `placeholder="Please choose from the keyboard." ` +
        `:value="currentStepField(${ctx}, 'invalid_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'wait_keyboard_reply')" class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_finish_current_command')" @change="updateCurrentStepToggle(${ctx}, 'require_finish_current_command', $event.target.checked)"><span>Require this reply before new actions</span></label>` +
        `<label v-if="isStepType(${ctx}, 'wait_keyboard_reply')">Blocked Action Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'wait_keyboard_reply')" placeholder="Please finish the current command before starting a new one." :value="currentStepField(${ctx}, 'finish_current_command_text_template')" @input="updateCurrentStepField(${ctx}, 'finish_current_command_text_template', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'wait_keyboard_reply')">If enabled, other commands and callbacks wait until the user chooses one reply. /restart is still allowed.</p>`
      );
    },
  });
})(window);
