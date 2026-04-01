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
    type: "inline_button",
    label: "inline_button",
    defaultStep() {
      return {
        module_type: "inline_button",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        run_if_context_keys: "",
        skip_if_context_keys: "",
        save_callback_data_to_key: "",
      };
    },
    parsePrimary(source, helpers) {
      const buttons = Array.isArray(source.buttons)
        ? helpers.normalizeInlineButtons(source.buttons)
        : helpers.parseInlineButtons(source.inline_buttons || "");
      return {
        module_type: "inline_button",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons,
        run_if_context_keys: formatContextKeyLines(source.run_if_context_keys),
        skip_if_context_keys: formatContextKeyLines(source.skip_if_context_keys),
        save_callback_data_to_key: source.save_callback_data_to_key ? String(source.save_callback_data_to_key) : "",
      };
    },
    parseChain(parts, helpers) {
      if (String(parts[0] || "").trim().toLowerCase() !== "inline_button") {
        return null;
      }
      let buttonsRaw = [];
      try {
        buttonsRaw = JSON.parse(parts[2] || "[]");
      } catch (_error) {
        buttonsRaw = [];
      }
      return {
        module_type: "inline_button",
        text_template: parts[1] || "",
        parse_mode: parts[3] || "",
        title: "Main Menu",
        items: [],
        buttons: helpers.normalizeInlineButtons(buttonsRaw),
        run_if_context_keys: formatContextKeyLines(parts[4] || ""),
        skip_if_context_keys: formatContextKeyLines(parts[5] || ""),
        save_callback_data_to_key: parts[6] ? String(parts[6]) : "",
      };
    },
    formatChain(step, helpers) {
      const payload = {
        module_type: "inline_button",
        text_template: String(step.text_template || ""),
        parse_mode: String(step.parse_mode || "").trim(),
        buttons: helpers.normalizeInlineButtons(step.buttons || []),
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
      const text = String(step.text_template || "").trim();
      const preview = text.length > 40 ? `${text.slice(0, 40)}...` : text;
      const buttonCount = Array.isArray(step.buttons) ? step.buttons.length : 0;
      return `#${stepNo} inline_button - ${preview || "(empty text)"} (${buttonCount} buttons)`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const textId = `${prefix}inline_text_template`;
      const textIdAttr = textId ? ` id="${textId}"` : "";
      const textFor = textId ? ` for="${textId}"` : "";
      const actionId = `${prefix}inline_button_action`;
      const valueId = `${prefix}inline_button_value`;
      const rowId = `${prefix}inline_button_row`;
      const actionIdAttr = actionId ? ` id="${actionId}"` : "";
      const valueIdAttr = valueId ? ` id="${valueId}"` : "";
      const rowIdAttr = rowId ? ` id="${rowId}"` : "";
      const actionFor = actionId ? ` for="${actionId}"` : "";
      const rowFor = rowId ? ` for="${rowId}"` : "";
      return (
        `<label${textFor} v-if="isStepType(${ctx}, 'inline_button')">Message Template</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'inline_button')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<i>', '</i>', $event)">Italic</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<code>', '</code>', $event)">Code</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<blockquote>', '</blockquote>', $event)">Quote</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<tg-spoiler>', '</tg-spoiler>', $event)">Spoiler</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;https://example.com&quot;>', '</a>', $event)">Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;tg://user?id=123456789&quot;>', '</a>', $event)">Mention</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{bot_name}', $event)">Bot</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{user_first_name}', $event)">Name</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{callback_data}', $event)">Callback</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea${textIdAttr} ` +
        `placeholder="Command response text" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>` +
	        `<label v-if="isStepType(${ctx}, 'inline_button')">Run If Context Keys</label>` +
	        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'inline_button')">` +
	        `<select :value="contextKeyDraft(${ctx}, 'run_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'run_if_context_keys', $event.target.value)">` +
	        `<option value="">Select context key</option>` +
	        `<option v-for="contextKey in contextKeyOptions" :key="'run-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
	        `</select>` +
	        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'run_if_context_keys')">Add Key</button>` +
	        `</div>` +
	        `<textarea v-if="isStepType(${ctx}, 'inline_button')" placeholder="One rule per line&#10;Example: profile.i_am_18=true" :value="currentStepField(${ctx}, 'run_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'run_if_context_keys', $event.target.value)"></textarea>` +
	        `<p class="hint" v-if="isStepType(${ctx}, 'inline_button')">Use either a plain context key or a value rule like profile.i_am_18=true. All run_if rules must match before this inline_button sends.</p>` +
	        `<label v-if="isStepType(${ctx}, 'inline_button')">Skip If Context Keys</label>` +
	        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'inline_button')">` +
	        `<select :value="contextKeyDraft(${ctx}, 'skip_if_context_keys')" @change="updateContextKeyDraftField(${ctx}, 'skip_if_context_keys', $event.target.value)">` +
	        `<option value="">Select context key</option>` +
	        `<option v-for="contextKey in contextKeyOptions" :key="'skip-if-' + contextKey" :value="contextKey">[[ contextKey ]]</option>` +
	        `</select>` +
	        `<button type="button" class="secondary" @click="appendContextKey(${ctx}, 'skip_if_context_keys')">Add Key</button>` +
	        `</div>` +
	        `<textarea v-if="isStepType(${ctx}, 'inline_button')" placeholder="One rule per line&#10;Example: profile.i_am_18=false" :value="currentStepField(${ctx}, 'skip_if_context_keys')" @input="updateCurrentStepField(${ctx}, 'skip_if_context_keys', $event.target.value)"></textarea>` +
	        `<p class="hint" v-if="isStepType(${ctx}, 'inline_button')">If any skip_if rule matches, including value rules like profile.i_am_18=false, this inline_button is skipped.</p>` +
        `<label v-if="isStepType(${ctx}, 'inline_button')">Save Clicked Value To</label>` +
        `<input v-if="isStepType(${ctx}, 'inline_button')" placeholder="selected_option" :value="currentStepField(${ctx}, 'save_callback_data_to_key')" @input="updateCurrentStepField(${ctx}, 'save_callback_data_to_key', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'inline_button')">When a callback button in this module is clicked, the button actual_value is saved here if set; otherwise callback_data is used.</p>` +
        `<div class="module-list-tools" v-if="isStepType(${ctx}, 'inline_button')">` +
        `<input class="inline-button-input" placeholder="Button Text" ` +
        `:value="inlineButtonDraft(${ctx}).text" ` +
        `@input="updateInlineButtonDraftField(${ctx}, 'text', $event.target.value)">` +
        `<label${actionFor} class="hint">Action</label>` +
            `<select${actionIdAttr} :value="inlineButtonDraft(${ctx}).action" ` +
            `@change="updateInlineButtonDraftField(${ctx}, 'action', $event.target.value)">` +
            `<option value="callback_data">callback_data</option>` +
            `<option value="url">url</option>` +
            `</select>` +
            `<input${valueIdAttr} class="inline-button-input" :list="inlineButtonDraft(${ctx}).action === 'callback_data' ? 'callback-data-options' : null" placeholder="Action Value" ` +
            `:value="inlineButtonDraft(${ctx}).value" ` +
            `@input="updateInlineButtonDraftField(${ctx}, 'value', $event.target.value)">` +
            `<input class="inline-button-input" v-if="inlineButtonDraft(${ctx}).action === 'callback_data'" placeholder="Actual Value (optional)" ` +
            `:value="inlineButtonDraft(${ctx}).actual_value" ` +
            `@input="updateInlineButtonDraftField(${ctx}, 'actual_value', $event.target.value)">` +
            `<select class="inline-button-input" v-if="inlineButtonDraft(${ctx}).action === 'callback_data'" ` +
            `:value="inlineButtonDraft(${ctx}).value" ` +
            `@change="applyInlineButtonDraftCallbackSuggestion(${ctx}, $event.target.value)">` +
            `<option value="">Select callback_data</option>` +
            `<option v-for="callbackKey in callbackOptions" :key="'inline-callback-' + callbackKey" :value="callbackKey">[[ callbackKey ]]</option>` +
            `</select>` +
            `<label${rowFor} class="hint">Row</label>` +
            `<input${rowIdAttr} class="inline-button-input" type="number" min="1" placeholder="1" ` +
            `:value="inlineButtonDraft(${ctx}).row" ` +
            `@input="updateInlineButtonDraftField(${ctx}, 'row', $event.target.value)">` +
        `<button type="button" class="secondary" @click="saveInlineButton(${ctx})">[[ inlineButtonDraft(${ctx}).edit_index === null ? 'Add Button' : 'Update Button' ]]</button>` +
        `<button type="button" class="secondary" v-if="inlineButtonDraft(${ctx}).edit_index !== null" @click="cancelInlineButtonEdit(${ctx})">Cancel</button>` +
        `</div>` +
        `<div class="module-list" v-if="isStepType(${ctx}, 'inline_button')">` +
        `<div class="module-list-row" v-for="(button, buttonIndex) in currentStepButtons(${ctx})" :key="'btn-' + buttonIndex">` +
        `<div class="module-list-meta">[[ inlineButtonLabel(button, buttonIndex) ]]</div>` +
        `<div class="module-list-actions">` +
        `<button type="button" @click="editInlineButton(${ctx}, buttonIndex)">Edit</button>` +
        `<button type="button" :disabled="buttonIndex === 0" @click="moveInlineButtonUp(${ctx}, buttonIndex)">Up</button>` +
        `<button type="button" :disabled="buttonIndex >= currentStepButtons(${ctx}).length - 1" @click="moveInlineButtonDown(${ctx}, buttonIndex)">Down</button>` +
        `<button type="button" @click="removeInlineButton(${ctx}, buttonIndex)">Remove</button>` +
        `</div>` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
