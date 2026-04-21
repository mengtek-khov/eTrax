(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "send_location",
    label: "send_location",
    defaultStep() {
      return {
        module_type: "send_location",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        location_latitude: "{location_latitude}",
        location_longitude: "{location_longitude}",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "send_location",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
        location_latitude: source.location_latitude != null ? String(source.location_latitude) : String(source.latitude || ""),
        location_longitude: source.location_longitude != null ? String(source.location_longitude) : String(source.longitude || ""),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "send_location") {
        return null;
      }
      return {
        module_type: "send_location",
        location_latitude: parts[1] || "",
        location_longitude: parts[2] || "",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      const latitude = String(step.location_latitude || "").trim();
      const longitude = String(step.location_longitude || "").trim();
      return `send_location | ${latitude} | ${longitude}`;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const latitude = String(step.location_latitude || "").trim();
      const longitude = String(step.location_longitude || "").trim();
      return `#${stepNo} send_location - ${latitude && longitude ? `${latitude}, ${longitude}` : "(context location)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const locationId = `${prefix}location_template`;
      const locationIdAttr = locationId ? ` id="${locationId}"` : "";
      const locationFor = locationId ? ` for="${locationId}"` : "";
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'send_location')">` +
        `<div>` +
        `<label${locationFor}>Location Template</label>` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="updateCurrentStepField(${ctx}, 'location_latitude', '{location_latitude}'); updateCurrentStepField(${ctx}, 'location_longitude', '{location_longitude}')">Shared Location</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="updateCurrentStepField(${ctx}, 'location_latitude', '{closest_location_latitude}'); updateCurrentStepField(${ctx}, 'location_longitude', '{closest_location_longitude}')">Closest Location</button>` +
        `</div>` +
        `<input${locationIdAttr} placeholder="{location_latitude}, {location_longitude}" :value="(currentStepField(${ctx}, 'location_latitude') || '') + ((currentStepField(${ctx}, 'location_longitude') || '') ? ', ' + currentStepField(${ctx}, 'location_longitude') : '')" @input="const raw = String($event.target.value || ''); const parts = raw.split(','); updateCurrentStepField(${ctx}, 'location_latitude', (parts.shift() || '').trim()); updateCurrentStepField(${ctx}, 'location_longitude', parts.join(',').trim())">` +
        `<p class="hint">Use one value like <code>{location_latitude}, {location_longitude}</code>. This sends a native Telegram map pin using the resolved coordinates.</p>` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
