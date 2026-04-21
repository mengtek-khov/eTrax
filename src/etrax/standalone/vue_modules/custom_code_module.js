(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "custom_code",
    label: "custom_code",
    defaultStep() {
      return {
        module_type: "custom_code",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        function_name: "",
        success_text_template: "",
        invalid_text_template: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "custom_code",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        function_name: source.function_name ? String(source.function_name) : "",
        success_text_template: "",
        invalid_text_template: "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "custom_code") {
        return null;
      }
      return {
        module_type: "custom_code",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        function_name: parts[1] || "",
        success_text_template: "",
        invalid_text_template: "",
      };
    },
    formatChain(step) {
      return `custom_code | ${String(step.function_name || "").trim()}`;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const functionName = String(step.function_name || "").trim();
      return `#${stepNo} custom_code - ${functionName || "(select function)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'custom_code')">` +
        `<div>` +
        `<label>Function</label>` +
        `<select :value="currentStepField(${ctx}, 'function_name')" @change="updateCurrentStepField(${ctx}, 'function_name', $event.target.value)">` +
        `<option value="">Select custom function</option>` +
        `<option v-for="functionName in customCodeFunctionOptions" :key="'custom-code-fn-' + functionName" :value="functionName">[[ functionName ]]</option>` +
        `</select>` +
        `</div>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'custom_code')">Functions come from <code>src/etrax/standalone/custom_code_functions.py</code>. Add public methods to <code>StandaloneCustomCodeFunctions</code> and reload the page.</p>`
      );
    },
  });
})(window);
