(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  // The editor shows one unified list of variables. On disk the first one is
  // still the step's own variable_name/text_template pair, and the rest ride
  // in the step's `items` array - the same generic list field the menu module
  // uses - so everything survives both the regular save path and the Save As
  // Template path without any new per-row form plumbing (the config_vue.js
  // helpers `currentStepVariableItems`/`setVariableLineAt`/etc. handle the
  // index 0 <-> variable_name/text_template mapping transparently). Each line
  // reads "name = value template".
  function payload(variableName, valueTemplate, items) {
    return {
      module_type: "set_variable",
      text_template: valueTemplate || "",
      variable_name: variableName || "",
      button_text: variableName || "",
      parse_mode: "",
      title: "Main Menu",
      items: Array.isArray(items) ? items : [],
      buttons: [],
    };
  }

  moduleSystem.register({
    type: "set_variable",
    label: "set_variable",
    defaultStep() {
      return payload("", "", []);
    },
    parsePrimary(source) {
      const variableName = String(source.variable_name || source.button_text || "").trim();
      const items = Array.isArray(source.items) ? source.items : [];
      return payload(variableName, source.text_template ? String(source.text_template) : "", items);
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "set_variable") {
        return null;
      }
      // Legacy pipe format only ever carried one variable; additional
      // variables require the JSON-per-line chain format.
      return payload(parts[1] || "", parts[2] || "", []);
    },
    formatChain(step) {
      const variableName = String(step.variable_name || step.button_text || "").trim();
      const items = Array.isArray(step.items) ? step.items.filter((line) => String(line || "").trim()) : [];
      const chainPayload = {
        module_type: "set_variable",
        variable_name: variableName,
        text_template: String(step.text_template || ""),
      };
      if (items.length) {
        chainPayload.items = items;
      }
      return JSON.stringify(chainPayload);
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const variableName = String(step.variable_name || step.button_text || "").trim();
      const extraCount = Array.isArray(step.items) ? step.items.filter((line) => String(line || "").trim()).length : 0;
      const suffix = extraCount ? ` (+${extraCount} more)` : "";
      return `#${stepNo} set_variable - ${variableName || "(unnamed variable)"}${suffix}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const nameId = `${prefix}variable_name`;
      const templateId = `${prefix}variable_text_template`;
      return (
        `<label for="${nameId}" v-if="isStepType(${ctx}, 'set_variable')">Variable Name</label>` +
        `<input id="${nameId}" v-if="isStepType(${ctx}, 'set_variable')" placeholder="location_prompt" ` +
        `:value="variableDraft(${ctx}).variable_name" ` +
        `@input="updateVariableDraftField(${ctx}, 'variable_name', $event.target.value)">` +
        `<label for="${templateId}" v-if="isStepType(${ctx}, 'set_variable')">Value Template</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'set_variable')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<i>', '</i>', $event)">Italic</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<code>', '</code>', $event)">Code</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<blockquote>', '</blockquote>', $event)">Quote</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<tg-spoiler>', '</tg-spoiler>', $event)">Spoiler</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;https://example.com&quot;>', '</a>', $event)">Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyVariableDraftTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;tg://user?id=123456789&quot;>', '</a>', $event)">Mention</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertVariableDraftTemplateToken(${ctx}, 'text_template', '{bot_name}', $event)">Bot</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertVariableDraftTemplateToken(${ctx}, 'text_template', '{user_first_name}', $event)">Name</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertVariableDraftTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea id="${templateId}" ` +
        `placeholder="Text shown later as {variable_name}. Can reference other context, e.g. {keyboard_reply_text} or an earlier variable like {location_prompt}." ` +
        `:value="variableDraft(${ctx}).text_template" ` +
        `@input="updateVariableDraftField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>` +
        `<div class="actions" v-if="isStepType(${ctx}, 'set_variable')">` +
        `<button type="button" class="secondary" @click="saveVariable(${ctx})">[[ variableDraft(${ctx}).edit_index === null ? 'Add Variable' : 'Update Variable' ]]</button>` +
        `<button type="button" class="secondary" v-if="variableDraft(${ctx}).edit_index !== null" @click="cancelVariableEdit(${ctx})">Cancel</button>` +
        `</div>` +
        `<div class="module-list" v-if="isStepType(${ctx}, 'set_variable')">` +
        `<div class="module-list-row" v-for="(line, variableIndex) in currentStepVariableItems(${ctx})" :key="'var-' + variableIndex">` +
        `<div class="module-list-meta">[[ variableEntryLabel(line, variableIndex) ]]</div>` +
        `<div class="module-list-actions">` +
        `<button type="button" class="primary" @click="editVariable(${ctx}, variableIndex)">Edit</button>` +
        `<button type="button" :disabled="variableIndex === 0" @click="moveVariableUp(${ctx}, variableIndex)">Up</button>` +
        `<button type="button" :disabled="variableIndex >= currentStepVariableItems(${ctx}).length - 1" @click="moveVariableDown(${ctx}, variableIndex)">Down</button>` +
        `<button type="button" class="danger" @click="removeVariable(${ctx}, variableIndex)">Remove</button>` +
        `</div>` +
        `</div>` +
        `</div>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'set_variable')">Fill in Variable Name and Value Template, then Add Variable. Each renders against the current context and is stored under its name; later steps in this pipeline - including any callback it jumps to - can use it as <code>{variable_name}</code>. Variables run in order, so a later one can reference an earlier one.</p>`
      );
    },
  });
})(window);
