(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "userinfo",
    label: "userinfo",
    defaultStep() {
      return {
        module_type: "userinfo",
        title: "Current User Information",
        empty_text_template: "No user information has been gathered yet.",
        parse_mode: "",
        text_template: "",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source) {
      return {
        module_type: "userinfo",
        title: source.title ? String(source.title) : "Current User Information",
        empty_text_template: source.empty_text_template
          ? String(source.empty_text_template)
          : "No user information has been gathered yet.",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        text_template: "",
        items: [],
        buttons: [],
      };
    },
    parseChain(parts) {
      const moduleType = String(parts[0] || "").trim().toLowerCase();
      if (moduleType !== "userinfo" && moduleType !== "user_info") {
        return null;
      }
      return {
        module_type: "userinfo",
        title: parts[1] || "Current User Information",
        empty_text_template: parts[2] || "No user information has been gathered yet.",
        parse_mode: parts[3] || "",
        text_template: "",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      const title = String(step.title || "").trim();
      const emptyText = String(step.empty_text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      const parts = ["userinfo"];
      if (title || emptyText || parseMode) {
        parts.push(title);
      }
      if (emptyText || parseMode) {
        parts.push(emptyText);
      }
      if (parseMode) {
        parts.push(parseMode);
      }
      return parts.join(" | ");
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const title = String(step.title || "").trim();
      return `#${stepNo} userinfo - ${title || "Current User Information"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'userinfo')">Title</label>` +
        `<input v-if="isStepType(${ctx}, 'userinfo')" :value="currentStepField(${ctx}, 'title')" @input="updateCurrentStepField(${ctx}, 'title', $event.target.value)">` +
        `<label v-if="isStepType(${ctx}, 'userinfo')">Empty State Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'userinfo')" :value="currentStepField(${ctx}, 'empty_text_template')" @input="updateCurrentStepField(${ctx}, 'empty_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'userinfo')">Parse Mode</label>` +
        `<select v-if="isStepType(${ctx}, 'userinfo')" :value="currentStepField(${ctx}, 'parse_mode')" @change="updateCurrentStepField(${ctx}, 'parse_mode', $event.target.value)">` +
        `<option value="">None</option>` +
        `<option value="HTML">HTML</option>` +
        `<option value="Markdown">Markdown</option>` +
        `<option value="MarkdownV2">MarkdownV2</option>` +
        `</select>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'userinfo')">Sends the current user's gathered profile fields from the profile log and active runtime context.</p>`
      );
    },
  });
})(window);
