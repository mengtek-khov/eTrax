(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "send_message",
    label: "send_message",
    defaultStep() {
      return {
        module_type: "send_message",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source) {
      return {
        module_type: "send_message",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "send_message") {
        return null;
      }
      return {
        module_type: "send_message",
        text_template: parts[1] || "",
        parse_mode: parts[2] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      const text = String(step.text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `send_message | ${text}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const text = String(step.text_template || "").trim();
      const preview = text.length > 50 ? `${text.slice(0, 50)}...` : text;
      return `#${stepNo} send_message - ${preview || "(empty text)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const textId = `${prefix}text_template`;
      const hasId = textId.length > 0;
      const labelAttr = hasId ? ` for=\"${textId}\"` : "";
      const idAttr = hasId ? ` id=\"${textId}\"` : "";
      return (
        `<label${labelAttr} v-if=\"isStepType(${ctx}, 'send_message')\">Message Template</label>` +
        `<div class=\"template-editor\" v-if=\"isStepType(${ctx}, 'send_message')\">` +
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
        `<textarea${idAttr} ` +
        `:value=\"currentStepField(${ctx}, 'text_template')\" ` +
        `@input=\"updateCurrentStepField(${ctx}, 'text_template', $event.target.value)\"></textarea>` +
        `</div>`
      );
    },
  });
})(window);
