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
      return splitContextKeyLines(raw.join("\n")).join("\n");
    }
    return splitContextKeyLines(raw).join("\n");
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
        run_if_context_keys: "",
        skip_if_context_keys: "",
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
        run_if_context_keys: formatContextKeyLines(source.run_if_context_keys),
        skip_if_context_keys: formatContextKeyLines(source.skip_if_context_keys),
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
        run_if_context_keys: formatContextKeyLines(parts[4] || ""),
        skip_if_context_keys: formatContextKeyLines(parts[5] || ""),
      };
    },
    formatChain(step, helpers) {
      const payload = {
        module_type: "keyboard_button",
        text_template: String(step.text_template || "Choose an option."),
        parse_mode: String(step.parse_mode || "").trim(),
        buttons: helpers.normalizeKeyboardButtons(step.buttons || []),
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
        `<div class="template-editor" v-if="isStepType(${ctx}, 'keyboard_button')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<i>', '</i>', $event)">Italic</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<code>', '</code>', $event)">Code</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<blockquote>', '</blockquote>', $event)">Quote</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<tg-spoiler>', '</tg-spoiler>', $event)">Spoiler</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;https://example.com&quot;>', '</a>', $event)">Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{bot_name}', $event)">Bot</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{user_first_name}', $event)">Name</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea${textIdAttr} ` +
        `placeholder="Choose an option." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'keyboard_button')">Run If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'keyboard_button')">` +
        `<select :value="contextKeyDraft(${ctx}, 'run_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'run_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'keyboard-run-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'run_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'keyboard_button')" placeholder="One rule per line&#10;Example: profile.i_am_18=true" :value="currentStepField(${ctx}, 'run_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'run_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'keyboard_button')">Use either a plain context key or a value rule like profile.i_am_18=true. All run_if rules must match before this keyboard_button sends.</p>` +
        `<label v-if="isStepType(${ctx}, 'keyboard_button')">Skip If Context Keys</label>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'keyboard_button')">` +
        `<select :value="contextKeyDraft(${ctx}, 'skip_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'skip_if_context_keys', $event.target.value)">` +
        `<option value="">Select context key</option>` +
        `<option v-for="contextKey in contextKeyOptions" :key="'keyboard-skip-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
        `</select>` +
        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'skip_if_context_keys')">Add Key</button>` +
        `</div>` +
        `<textarea v-if="isStepType(${ctx}, 'keyboard_button')" placeholder="One rule per line&#10;Example: profile.i_am_18=false" :value="currentStepField(${ctx}, 'skip_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'skip_if_context_keys', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'keyboard_button')">If any skip_if rule matches, including value rules like profile.i_am_18=false, this keyboard_button is skipped.</p>` +
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
