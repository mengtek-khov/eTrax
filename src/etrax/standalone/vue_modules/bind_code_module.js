(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "bind_code",
    label: "bind_code",
    defaultStep() {
      return {
        module_type: "bind_code",
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
        bind_code_prefix: "",
        bind_code_number_width: "4",
        bind_code_start_number: "1",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "bind_code",
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
        bind_code_prefix: source.bind_code_prefix ? String(source.bind_code_prefix) : "",
        bind_code_number_width:
          source.bind_code_number_width == null ? "4" : String(source.bind_code_number_width),
        bind_code_start_number:
          source.bind_code_start_number == null ? "1" : String(source.bind_code_start_number),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "bind_code") {
        return null;
      }
      return {
        module_type: "bind_code",
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
        bind_code_prefix: parts[1] || "",
        bind_code_number_width: parts[2] || "4",
        bind_code_start_number: parts[3] || "1",
      };
    },
    formatChain(step) {
      return `bind_code | ${String(step.bind_code_prefix || "").trim()} | ${String(step.bind_code_number_width || "4").trim()} | ${String(step.bind_code_start_number || "1").trim()}`;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const prefix = String(step.bind_code_prefix || "").trim() || "(no prefix)";
      const width = String(step.bind_code_number_width || "4").trim() || "4";
      const start = String(step.bind_code_start_number || "1").trim() || "1";
      return `#${stepNo} bind_code - ${prefix} / width ${width} / start ${start}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'bind_code')">` +
        `<div>` +
        `<label>Code Prefix</label>` +
        `<input placeholder="ETX-" :value="currentStepField(${ctx}, 'bind_code_prefix')" @input="updateCurrentStepField(${ctx}, 'bind_code_prefix', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Number Width</label>` +
        `<input type="number" min="0" step="1" placeholder="4" :value="currentStepField(${ctx}, 'bind_code_number_width')" @input="updateCurrentStepField(${ctx}, 'bind_code_number_width', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Start Number</label>` +
        `<input type="number" min="1" step="1" placeholder="1" :value="currentStepField(${ctx}, 'bind_code_start_number')" @input="updateCurrentStepField(${ctx}, 'bind_code_start_number', $event.target.value)">` +
        `</div>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'bind_code')">Generates and binds a new code each time this step runs. Prefix can include separators like <code>ETX-</code>, so width <code>4</code> becomes codes like <code>ETX-0001</code>, <code>ETX-0002</code>. The result is available in later templates as <code>{bound_code}</code>.</p>`
      );
    },
  });
})(window);
