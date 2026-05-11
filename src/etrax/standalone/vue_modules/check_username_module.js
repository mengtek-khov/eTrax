(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "check_username",
    label: "check_username",
    defaultStep() {
      return {
        module_type: "check_username",
        text_template: "Please set a Telegram username before continuing.",
        failure_text_template: "Please set a Telegram username before continuing.",
        required_username: "",
        button_text: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source) {
      const requiredUsername = source.required_username ? String(source.required_username) : "";
      const failureText = source.failure_text_template
        ? String(source.failure_text_template)
        : String(source.text_template || "Please set a Telegram username before continuing.");
      return {
        module_type: "check_username",
        text_template: failureText,
        failure_text_template: failureText,
        required_username: requiredUsername,
        button_text: requiredUsername,
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "check_username") {
        return null;
      }
      const requiredUsername = parts[1] || "";
      const failureText = parts[2] || "Please set a Telegram username before continuing.";
      return {
        module_type: "check_username",
        text_template: failureText,
        failure_text_template: failureText,
        required_username: requiredUsername,
        button_text: requiredUsername,
        parse_mode: parts[3] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      const requiredUsername = String(step.required_username || step.button_text || "").trim();
      const failureText = String(step.failure_text_template || step.text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      const parts = ["check_username", requiredUsername, failureText];
      if (parseMode) {
        parts.push(parseMode);
      }
      return parts.join(" | ");
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const requiredUsername = String(step.required_username || step.button_text || "").trim();
      return `#${stepNo} check_username - ${requiredUsername || "any username"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'check_username')">Required Username</label>` +
        `<input v-if="isStepType(${ctx}, 'check_username')" placeholder="username or blank for any username" :value="currentStepField(${ctx}, 'required_username')" @input="updateCurrentStepField(${ctx}, 'required_username', $event.target.value); updateCurrentStepField(${ctx}, 'button_text', $event.target.value)">` +
        `<label v-if="isStepType(${ctx}, 'check_username')">Failure Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'check_username')" :value="currentStepField(${ctx}, 'failure_text_template')" @input="updateCurrentStepField(${ctx}, 'failure_text_template', $event.target.value); updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'check_username')">Stops the pipeline when the current Telegram user has no username, or when Required Username is set and does not match.</p>`
      );
    },
  });
})(window);
