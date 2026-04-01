(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "menu",
    label: "menu",
    defaultStep() {
      return {
        module_type: "menu",
        text_template: "",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    parsePrimary(source, helpers) {
      return {
        module_type: "menu",
        text_template: "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: source.menu_title ? String(source.menu_title) : "Main Menu",
        items: helpers.parseMenuItems(source.menu_items || ""),
        buttons: [],
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "menu") {
        return null;
      }
      const items = String(parts[2] || "")
        .split(";")
        .map((item) => item.trim())
        .filter((item) => item.length > 0);
      return {
        module_type: "menu",
        text_template: "",
        parse_mode: parts[3] || "",
        title: parts[1] || "Main Menu",
        items,
        buttons: [],
      };
    },
    formatChain(step, helpers) {
      const title = String(step.title || "Main Menu").trim() || "Main Menu";
      const items = helpers.parseMenuItems(Array.isArray(step.items) ? step.items.join("\n") : "");
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `menu | ${title} | ${items.join("; ")}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const itemCount = Array.isArray(step.items) ? step.items.length : 0;
      return `#${stepNo} menu - ${step.title || "Main Menu"} (${itemCount} items)`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const titleId = `${prefix}menu_title`;
      const itemsId = `${prefix}menu_items`;
      const titleIdAttr = titleId ? ` id=\"${titleId}\"` : "";
      const itemsIdAttr = itemsId ? ` id=\"${itemsId}\"` : "";
      const titleFor = titleId ? ` for=\"${titleId}\"` : "";
      const itemsFor = itemsId ? ` for=\"${itemsId}\"` : "";
      return (
        `<div class=\"module-grid\" v-if=\"isStepType(${ctx}, 'menu')\">` +
        `<div>` +
        `<label${titleFor}>Menu Title (for menu type)</label>` +
        `<input${titleIdAttr} placeholder=\"Main Menu\" :value=\"currentStepField(${ctx}, 'title')\" ` +
        `@input=\"updateCurrentStepField(${ctx}, 'title', $event.target.value)\">` +
        `</div>` +
        `<div>` +
        `<label${itemsFor}>Menu Items (for menu type, one per line)</label>` +
        `<textarea${itemsIdAttr} placeholder=\"/help - Get help&#10;/contact - Contact support\" ` +
        `:value=\"currentStepMenuItems(${ctx})\" ` +
        `@input=\"updateCurrentStepMenuItems(${ctx}, $event.target.value)\"></textarea>` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
