(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "send_photo",
    label: "send_photo",
    defaultStep() {
      return {
        module_type: "send_photo",
        text_template: "",
        parse_mode: "",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    parsePrimary(source, helpers) {
      const buttons = Array.isArray(source.buttons)
        ? helpers.normalizeInlineButtons(source.buttons)
        : helpers.parseInlineButtons(source.inline_buttons || "");
      return {
        module_type: "send_photo",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        hide_caption: Boolean(source.hide_caption),
        title: "Main Menu",
        items: [],
        buttons,
        photo_url: source.photo_url ? String(source.photo_url) : "",
      };
    },
    parseChain(parts, helpers) {
      if (String(parts[0] || "").trim().toLowerCase() !== "send_photo") {
        return null;
      }
      let buttonsRaw = [];
      try {
        buttonsRaw = JSON.parse(parts[3] || "[]");
      } catch (_error) {
        buttonsRaw = [];
      }
      return {
        module_type: "send_photo",
        photo_url: parts[1] || "",
        text_template: parts[2] || "",
        parse_mode: parts[4] || "",
        hide_caption: String(parts[5] || "").trim().toLowerCase() === "hide_caption",
        title: "Main Menu",
        items: [],
        buttons: helpers.normalizeInlineButtons(buttonsRaw),
      };
    },
    formatChain(step, helpers) {
      const photoUrl = String(step.photo_url || "").trim();
      const text = String(step.text_template || "").trim();
      const buttons = JSON.stringify(helpers.normalizeInlineButtons(step.buttons || []));
      const parseMode = String(step.parse_mode || "").trim();
      const hideCaption = Boolean(step.hide_caption);
      let payload = `send_photo | ${photoUrl} | ${text} | ${buttons}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      if (hideCaption) {
        if (!parseMode) {
          payload += ` | `;
        }
        payload += ` | hide_caption`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const caption = String(step.text_template || "").trim();
      const preview = caption.length > 40 ? `${caption.slice(0, 40)}...` : caption;
      const photoUrl = String(step.photo_url || "").trim();
      return `#${stepNo} send_photo - ${preview || photoUrl || "(empty photo)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const textId = `${prefix}photo_caption_template`;
      const photoId = `${prefix}photo_url`;
      const actionId = `${prefix}photo_button_action`;
      const valueId = `${prefix}photo_button_value`;
      const rowId = `${prefix}photo_button_row`;
      const textIdAttr = textId ? ` id=\"${textId}\"` : "";
      const photoIdAttr = photoId ? ` id=\"${photoId}\"` : "";
      const actionIdAttr = actionId ? ` id=\"${actionId}\"` : "";
      const valueIdAttr = valueId ? ` id=\"${valueId}\"` : "";
      const rowIdAttr = rowId ? ` id=\"${rowId}\"` : "";
      const textFor = textId ? ` for=\"${textId}\"` : "";
      const photoFor = photoId ? ` for=\"${photoId}\"` : "";
      const actionFor = actionId ? ` for=\"${actionId}\"` : "";
      const rowFor = rowId ? ` for=\"${rowId}\"` : "";
      return (
        `<div class=\"module-grid\" v-if=\"isStepType(${ctx}, 'send_photo')\">` +
        `<div>` +
        `<label${photoFor}>Photo URL, File ID, or Template</label>` +
        `<div class=\"template-toolbar\">` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'photo_url', '{selfie_file_id}', $event)\">Use Selfie</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'photo_url', '{photo}', $event)\">Use {photo}</button>` +
        `</div>` +
        `<input${photoIdAttr} placeholder=\"https://example.com/photo.jpg or {selfie_file_id}\" :value=\"currentStepField(${ctx}, 'photo_url')\" ` +
        `@input=\"updateCurrentStepField(${ctx}, 'photo_url', $event.target.value)\">` +
        `<p class=\"hint\">Use a Telegram file ID or a context template like <code>{selfie_file_id}</code> after an ask_selfie step.</p>` +
        `</div>` +
        `<div>` +
        `<label class=\"checkbox compact\"><input type=\"checkbox\" :checked=\"currentStepChecked(${ctx}, 'hide_caption')\" @change=\"updateCurrentStepToggle(${ctx}, 'hide_caption', $event.target.checked)\"><span>Hide Caption</span></label>` +
        `</div>` +
        `</div>` +
        `<label${textFor} v-if=\"isStepType(${ctx}, 'send_photo') && !currentStepChecked(${ctx}, 'hide_caption')\">Caption Template</label>` +
        `<div class=\"template-editor\" v-if=\"isStepType(${ctx}, 'send_photo') && !currentStepChecked(${ctx}, 'hide_caption')\">` +
        `<div class=\"template-toolbar\">` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)\">Bold</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<i>', '</i>', $event)\">Italic</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<code>', '</code>', $event)\">Code</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<blockquote>', '</blockquote>', $event)\">Quote</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<tg-spoiler>', '</tg-spoiler>', $event)\">Spoiler</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;https://example.com&quot;>', '</a>', $event)\">Link</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;tg://user?id=123456789&quot;>', '</a>', $event)\">Mention</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'text_template', '{bot_name}', $event)\">Bot</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'text_template', '{user_first_name}', $event)\">Name</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'text_template', '{user_username}', $event)\">Username</button>` +
        `<button type=\"button\" class=\"secondary\" @mousedown.prevent=\"insertTemplateToken(${ctx}, 'text_template', '\\n', $event)\">Line</button>` +
        `</div>` +
        `<textarea${textIdAttr} placeholder=\"Optional caption for the photo\" :value=\"currentStepField(${ctx}, 'text_template')\" ` +
        `@input=\"updateCurrentStepField(${ctx}, 'text_template', $event.target.value)\"></textarea>` +
        `</div>` +
        `<div class=\"module-list-tools\" v-if=\"isStepType(${ctx}, 'send_photo')\">` +
        `<input class=\"inline-button-input\" placeholder=\"Button Text\" :value=\"inlineButtonDraft(${ctx}).text\" ` +
        `@input=\"updateInlineButtonDraftField(${ctx}, 'text', $event.target.value)\">` +
        `<label${actionFor} class=\"hint\">Action</label>` +
	        `<select${actionIdAttr} :value=\"inlineButtonDraft(${ctx}).action\" @change=\"updateInlineButtonDraftField(${ctx}, 'action', $event.target.value)\">` +
	        `<option value=\"callback_data\">callback_data</option>` +
	        `<option value=\"url\">url</option>` +
	        `</select>` +
	        `<input${valueIdAttr} class=\"inline-button-input\" :list=\"inlineButtonDraft(${ctx}).action === 'callback_data' ? 'callback-data-options' : null\" placeholder=\"Action Value\" :value=\"inlineButtonDraft(${ctx}).value\" ` +
	        `@input=\"updateInlineButtonDraftField(${ctx}, 'value', $event.target.value)\">` +
	        `<select class=\"inline-button-input\" v-if=\"inlineButtonDraft(${ctx}).action === 'callback_data'\" :value=\"inlineButtonDraft(${ctx}).value\" ` +
	        `@change=\"applyInlineButtonDraftCallbackSuggestion(${ctx}, $event.target.value)\">` +
	        `<option value=\"\">Select callback_data</option>` +
	        `<option v-for=\"callbackKey in callbackOptions\" :key=\"'photo-callback-' + callbackKey\" :value=\"callbackKey\">[[ callbackKey ]]</option>` +
	        `</select>` +
	        `<label${rowFor} class=\"hint\">Row</label>` +
	        `<input${rowIdAttr} class=\"inline-button-input\" type=\"number\" min=\"1\" placeholder=\"1\" :value=\"inlineButtonDraft(${ctx}).row\" ` +
	        `@input=\"updateInlineButtonDraftField(${ctx}, 'row', $event.target.value)\">` +
        `<button type=\"button\" class=\"secondary\" @click=\"saveInlineButton(${ctx})\">[[ inlineButtonDraft(${ctx}).edit_index === null ? 'Add Button' : 'Update Button' ]]</button>` +
        `<button type=\"button\" class=\"secondary\" v-if=\"inlineButtonDraft(${ctx}).edit_index !== null\" @click=\"cancelInlineButtonEdit(${ctx})\">Cancel</button>` +
        `</div>` +
        `<div class=\"module-list\" v-if=\"isStepType(${ctx}, 'send_photo')\">` +
        `<div class=\"module-list-row\" v-for=\"(button, buttonIndex) in currentStepButtons(${ctx})\" :key=\"'photo-btn-' + buttonIndex\">` +
        `<div class=\"module-list-meta\">[[ inlineButtonLabel(button, buttonIndex) ]]</div>` +
        `<div class=\"module-list-actions\">` +
        `<button type=\"button\" @click=\"editInlineButton(${ctx}, buttonIndex)\">Edit</button>` +
        `<button type=\"button\" :disabled=\"buttonIndex === 0\" @click=\"moveInlineButtonUp(${ctx}, buttonIndex)\">Up</button>` +
        `<button type=\"button\" :disabled=\"buttonIndex >= currentStepButtons(${ctx}).length - 1\" @click=\"moveInlineButtonDown(${ctx}, buttonIndex)\">Down</button>` +
        `<button type=\"button\" @click=\"removeInlineButton(${ctx}, buttonIndex)\">Remove</button>` +
        `</div>` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
