(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "ask_selfie",
    label: "ask_selfie",
    defaultStep() {
      return {
        module_type: "ask_selfie",
        text_template: "Please send a selfie photo.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "Thanks, your selfie was received.",
        invalid_text_template: "Please send a selfie photo.",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "ask_selfie",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        invalid_text_template: source.invalid_text_template ? String(source.invalid_text_template) : "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "ask_selfie") {
        return null;
      }
      return {
        module_type: "ask_selfie",
        text_template: parts[1] || "",
        success_text_template: parts[2] || "",
        invalid_text_template: parts[3] || "",
        parse_mode: parts[4] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
      };
    },
    formatChain(step) {
      const prompt = String(step.text_template || "").trim();
      const successText = String(step.success_text_template || "").trim();
      const invalidText = String(step.invalid_text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `ask_selfie | ${prompt} | ${successText} | ${invalidText}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const prompt = String(step.text_template || "").trim();
      const preview = prompt.length > 32 ? `${prompt.slice(0, 32)}...` : prompt;
      return `#${stepNo} ask_selfie - ${preview || "(empty prompt)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Ask the user to send a selfie photo" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Shown after the user sends a selfie photo" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Invalid Selfie Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Shown when the user sends something other than a photo" ` +
        `:value="currentStepField(${ctx}, 'invalid_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
