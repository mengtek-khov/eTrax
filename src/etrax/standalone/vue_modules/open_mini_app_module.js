(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "open_mini_app",
    label: "open_mini_app",
    defaultStep() {
      return {
        module_type: "open_mini_app",
        text_template: "Tap the button below to open the mini app.",
        parse_mode: "",
        button_text: "Open Mini App",
        return_url: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "open_mini_app",
        text_template: source.text_template ? String(source.text_template) : "Tap the button below to open the mini app.",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        button_text: source.mini_app_button_text
          ? String(source.mini_app_button_text)
          : (source.contact_button_text ? String(source.contact_button_text) : (source.button_text ? String(source.button_text) : "Open Mini App")),
        return_url: source.mini_app_url
          ? String(source.mini_app_url)
          : (source.payment_return_url ? String(source.payment_return_url) : (source.url ? String(source.url) : (source.return_url ? String(source.return_url) : ""))),
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "open_mini_app") {
        return null;
      }
      return {
        module_type: "open_mini_app",
        text_template: parts[1] || "Tap the button below to open the mini app.",
        button_text: parts[2] || "Open Mini App",
        return_url: parts[3] || "",
        parse_mode: parts[4] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    formatChain(step) {
      const text = String(step.text_template || "").trim();
      const buttonText = String(step.button_text || "").trim() || "Open Mini App";
      const url = String(step.return_url || step.url || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `open_mini_app | ${text} | ${buttonText} | ${url}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const text = String(step.text_template || "").trim();
      const preview = text.length > 36 ? `${text.slice(0, 36)}...` : text;
      const buttonText = String(step.button_text || "").trim() || "Open Mini App";
      return `#${stepNo} open_mini_app - ${buttonText} / ${preview || "(empty text)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'open_mini_app')">Message Template</label>` +
        `<textarea v-if="isStepType(${ctx}, 'open_mini_app')" ` +
        `placeholder="Tap the button below to open the mini app." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'open_mini_app')">` +
        `<div>` +
        `<label>Button Text</label>` +
        `<input placeholder="Open Mini App" :value="currentStepField(${ctx}, 'button_text')" @input="updateCurrentStepField(${ctx}, 'button_text', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Mini App URL</label>` +
        `<input placeholder="https://example.com/mini-app" :value="currentStepField(${ctx}, 'return_url')" @input="updateCurrentStepField(${ctx}, 'return_url', $event.target.value)">` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
