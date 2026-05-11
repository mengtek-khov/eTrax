(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  function payload() {
    return {
      module_type: "reset_command_menu",
      text_template: "",
      parse_mode: "",
      title: "Main Menu",
      items: [],
      buttons: [],
      photo_url: "",
    };
  }

  moduleSystem.register({
    type: "reset_command_menu",
    label: "reset_command_menu",
    defaultStep() {
      return payload();
    },
    parsePrimary() {
      return payload();
    },
    parseChain(parts) {
      const type = String(parts[0] || "").trim().toLowerCase();
      if (!["reset_command_menu", "restore_command_menu", "reset_original_command_menu"].includes(type)) {
        return null;
      }
      return payload();
    },
    formatChain() {
      return "reset_command_menu";
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      return `#${stepNo} reset_command_menu - Restore original command menu`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="hint" v-if="isStepType(${ctx}, 'reset_command_menu')">` +
        `Restores the original Telegram command menu for the current chat and clears the active temporary command menu state.` +
        `</div>`
      );
    },
  });
})(window);
