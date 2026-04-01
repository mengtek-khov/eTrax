(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "forget_user_data",
    label: "forget_user_data",
    defaultStep() {
      return {
        module_type: "forget_user_data",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    parsePrimary() {
      return {
        module_type: "forget_user_data",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "forget_user_data") {
        return null;
      }
      return {
        module_type: "forget_user_data",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
      };
    },
    formatChain() {
      return "forget_user_data";
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      return `#${stepNo} forget_user_data - Clear current user profile and cart`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="hint" v-if="isStepType(${ctx}, 'forget_user_data')">` +
        `Clears the current user's persisted profile log entry, cart state, and any pending contact request. ` +
        `Add a later <code>send_message</code> step if you want to confirm the reset.` +
        `</div>`
      );
    },
  });
})(window);
