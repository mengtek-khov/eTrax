(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "route",
    label: "route",
    defaultStep() {
      return {
        module_type: "route",
        text_template: "",
        empty_text_template: "",
        max_link_points: "60",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source) {
      return {
        module_type: "route",
        text_template: source.text_template ? String(source.text_template) : "",
        empty_text_template: source.empty_text_template ? String(source.empty_text_template) : "",
        max_link_points: source.max_link_points == null ? "60" : String(source.max_link_points),
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "route") {
        return null;
      }
      return {
        module_type: "route",
        text_template: parts[1] || "",
        empty_text_template: parts[2] || "",
        max_link_points: parts[3] || "60",
        parse_mode: parts[4] || "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      return JSON.stringify({
        module_type: "route",
        text_template: String(step.text_template || ""),
        empty_text_template: String(step.empty_text_template || ""),
        max_link_points: String(step.max_link_points || "60"),
        parse_mode: String(step.parse_mode || ""),
      });
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const text = String(step.text_template || "").trim();
      const preview = text.length > 50 ? `${text.slice(0, 50)}...` : text;
      return `#${stepNo} route - ${preview || "(route summary)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'route')">Route Message</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'route')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{route_total_distance_text}', $event)">Distance</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{route_point_count}', $event)">Points</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{route_segment_count}', $event)">Segments</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{route_link}', $event)">Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea :value="currentStepField(${ctx}, 'text_template')" @input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)" placeholder="Breadcrumb Route&#10;Distance: {route_total_distance_text}&#10;Map: {route_link}"></textarea>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'route')">Empty Route Message</label>` +
        `<textarea v-if="isStepType(${ctx}, 'route')" :value="currentStepField(${ctx}, 'empty_text_template')" @input="updateCurrentStepField(${ctx}, 'empty_text_template', $event.target.value)" placeholder="No breadcrumb route available yet."></textarea>` +
        `<label v-if="isStepType(${ctx}, 'route')">Max Map Link Points</label>` +
        `<input v-if="isStepType(${ctx}, 'route')" type="number" min="2" step="1" :value="currentStepField(${ctx}, 'max_link_points')" @input="updateCurrentStepField(${ctx}, 'max_link_points', $event.target.value)">`
      );
    },
  });
})(window);
