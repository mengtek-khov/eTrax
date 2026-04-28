(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "keyboard_button",
    label: "keyboard_button",
    defaultStep() {
      return {
        module_type: "keyboard_button",
        text_template: "Choose an option.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source, helpers) {
      const buttons = Array.isArray(source.buttons)
        ? helpers.normalizeKeyboardButtons(source.buttons)
        : helpers.parseKeyboardButtons(source.inline_buttons || "");
      return {
        module_type: "keyboard_button",
        text_template: source.text_template ? String(source.text_template) : "Choose an option.",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons,
      };
    },
    parseChain(parts, helpers) {
      if (String(parts[0] || "").trim().toLowerCase() !== "keyboard_button") {
        return null;
      }
      let buttonsRaw = [];
      try {
        buttonsRaw = JSON.parse(parts[2] || "[]");
      } catch (_error) {
        buttonsRaw = [];
      }
      return {
        module_type: "keyboard_button",
        text_template: parts[1] || "Choose an option.",
        parse_mode: parts[3] || "",
        title: "Main Menu",
        items: [],
        buttons: helpers.normalizeKeyboardButtons(buttonsRaw),
      };
    },
    formatChain(step, helpers) {
      return JSON.stringify({
        module_type: "keyboard_button",
        text_template: String(step.text_template || "Choose an option."),
        parse_mode: String(step.parse_mode || "").trim(),
        buttons: helpers.normalizeKeyboardButtons(step.buttons || []),
      });
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const text = String(step.text_template || "").trim();
      const preview = text.length > 40 ? `${text.slice(0, 40)}...` : text;
      const buttonCount = Array.isArray(step.buttons) ? step.buttons.length : 0;
      return `#${stepNo} keyboard_button - ${preview || "(empty text)"} (${buttonCount} buttons)`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const textId = `${prefix}keyboard_text_template`;
      const buttonId = `${prefix}keyboard_button_text`;
      const rowId = `${prefix}keyboard_button_row`;
      const textFor = textId ? ` for="${textId}"` : "";
      const buttonFor = buttonId ? ` for="${buttonId}"` : "";
      const rowFor = rowId ? ` for="${rowId}"` : "";
      const textIdAttr = textId ? ` id="${textId}"` : "";
      const buttonIdAttr = buttonId ? ` id="${buttonId}"` : "";
      const rowIdAttr = rowId ? ` id="${rowId}"` : "";
      return (
        `<label${textFor} v-if="isStepType(${ctx}, 'keyboard_button')">Message Template</label>` +
        `<textarea${textIdAttr} v-if="isStepType(${ctx}, 'keyboard_button')" ` +
        `placeholder="Choose an option." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'keyboard_button')">` +
        `<label${buttonFor} class="hint">Button Text</label>` +
        `<input${buttonIdAttr} class="inline-button-input" placeholder="/help" ` +
        `:value="inlineButtonDraft(${ctx}).text" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'text', $event.target.value)">` +
        `<label${rowFor} class="hint">Row</label>` +
        `<input${rowIdAttr} class="inline-button-input" type="number" min="1" placeholder="1" ` +
        `:value="inlineButtonDraft(${ctx}).row" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'row', $event.target.value)">` +
        `<button type="button" class="secondary" @click="saveKeyboardButton(${ctx})">[[ inlineButtonDraft(${ctx}).edit_index === null ? 'Add Button' : 'Update Button' ]]</button>` +
        `<button type="button" class="secondary" v-if="inlineButtonDraft(${ctx}).edit_index !== null" @click="cancelKeyboardButtonEdit(${ctx})">Cancel</button>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'keyboard_button')">Reply keyboard buttons send plain text back to the bot. Use values like /help if you want them to trigger command flows.</p>` +
        `<div class="module-list" v-if="isStepType(${ctx}, 'keyboard_button')">` +
        `<div class="module-list-row" v-for="(button, buttonIndex) in currentStepButtons(${ctx})" :key="'kbd-' + buttonIndex">` +
        `<div class="module-list-meta">[[ keyboardButtonLabel(button, buttonIndex) ]]</div>` +
        `<div class="module-list-actions">` +
        `<button type="button" @click="editKeyboardButton(${ctx}, buttonIndex)">Edit</button>` +
        `<button type="button" :disabled="buttonIndex === 0" @click="moveKeyboardButtonUp(${ctx}, buttonIndex)">Up</button>` +
        `<button type="button" :disabled="buttonIndex >= currentStepButtons(${ctx}).length - 1" @click="moveKeyboardButtonDown(${ctx}, buttonIndex)">Down</button>` +
        `<button type="button" @click="removeKeyboardButton(${ctx}, buttonIndex)">Remove</button>` +
        `</div>` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
