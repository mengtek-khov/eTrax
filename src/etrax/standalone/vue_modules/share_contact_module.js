(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "share_contact",
    label: "share_contact",
    defaultStep() {
      return {
        module_type: "share_contact",
        text_template: "Please share your contact using the button below.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "Share My Contact",
        success_text_template: "Thanks {contact_first_name}, your contact was verified.",
        invalid_text_template: "Please share your own contact using the button below.",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "share_contact",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: source.button_text ? String(source.button_text) : "",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        invalid_text_template: source.invalid_text_template ? String(source.invalid_text_template) : "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "share_contact") {
        return null;
      }
      return {
        module_type: "share_contact",
        text_template: parts[1] || "",
        button_text: parts[2] || "",
        success_text_template: parts[3] || "",
        invalid_text_template: parts[4] || "",
        parse_mode: parts[5] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    formatChain(step) {
      const prompt = String(step.text_template || "").trim();
      const buttonText = String(step.button_text || "").trim();
      const successText = String(step.success_text_template || "").trim();
      const invalidText = String(step.invalid_text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `share_contact | ${prompt} | ${buttonText} | ${successText} | ${invalidText}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const prompt = String(step.text_template || "").trim();
      const buttonText = String(step.button_text || "").trim();
      const preview = prompt.length > 32 ? `${prompt.slice(0, 32)}...` : prompt;
      return `#${stepNo} share_contact - ${buttonText || "(share button)"} / ${preview || "(empty prompt)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'share_contact')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_contact')" ` +
        `placeholder="Ask the user to share their contact" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<div class="module-grid" v-if="isStepType(${ctx}, 'share_contact')">` +
        `<div>` +
        `<label>Button Text</label>` +
        `<input placeholder="Share My Contact" ` +
        `:value="currentStepField(${ctx}, 'button_text')" ` +
        `@input="updateCurrentStepField(${ctx}, 'button_text', $event.target.value)">` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'share_contact')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_contact')" ` +
        `placeholder="Shown after the user shares their own contact" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'share_contact')">Invalid Contact Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'share_contact')" ` +
        `placeholder="Shown when the shared contact does not belong to the current user" ` +
        `:value="currentStepField(${ctx}, 'invalid_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
