(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "delete_message",
    label: "delete_message",
    defaultStep() {
      return {
        module_type: "delete_message",
        source_result_key: "send_message_result",
        message_id_context_key: "message_id",
        message_id: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "delete_message",
        source_result_key: source.source_result_key ? String(source.source_result_key) : "send_message_result",
        message_id_context_key: source.message_id_context_key ? String(source.message_id_context_key) : "message_id",
        message_id: source.message_id ? String(source.message_id) : "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "delete_message") {
        return null;
      }
      return {
        module_type: "delete_message",
        source_result_key: parts[1] || "send_message_result",
        message_id_context_key: parts[2] || "message_id",
        message_id: parts[3] || "",
      };
    },
    formatChain(step) {
      const sourceResultKey = String(step.source_result_key || "send_message_result").trim();
      const messageIdContextKey = String(step.message_id_context_key || "message_id").trim();
      const messageId = String(step.message_id || "").trim();
      return `delete_message | ${sourceResultKey} | ${messageIdContextKey} | ${messageId}`;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const messageId = String(step.message_id || "").trim();
      const sourceResultKey = String(step.source_result_key || "send_message_result").trim();
      return `#${stepNo} delete_message - ${messageId || sourceResultKey || "message_id"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      const prefix = args.idPrefix || "";
      const sourceId = `${prefix}delete_source_result_key`;
      const contextId = `${prefix}delete_message_id_context_key`;
      const fixedId = `${prefix}delete_message_id`;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'delete_message')">` +
        `<div>` +
        `<label for="${sourceId}">Source Result Key</label>` +
        `<input id="${sourceId}" placeholder="send_message_result" :value="currentStepField(${ctx}, 'source_result_key')" ` +
        `@input="updateCurrentStepField(${ctx}, 'source_result_key', $event.target.value)">` +
        `<p class="hint">Use the result key from the module that sent the message, for example <code>send_message_result</code> or <code>send_photo_result</code>.</p>` +
        `</div>` +
        `<div>` +
        `<label for="${contextId}">Message ID Context Key</label>` +
        `<input id="${contextId}" placeholder="message_id" :value="currentStepField(${ctx}, 'message_id_context_key')" ` +
        `@input="updateCurrentStepField(${ctx}, 'message_id_context_key', $event.target.value)">` +
        `<p class="hint">Used when a previous custom module saved the Telegram message id directly in context.</p>` +
        `</div>` +
        `<div>` +
        `<label for="${fixedId}">Fixed Message ID</label>` +
        `<input id="${fixedId}" placeholder="Optional explicit message id" :value="currentStepField(${ctx}, 'message_id')" ` +
        `@input="updateCurrentStepField(${ctx}, 'message_id', $event.target.value)">` +
        `</div>` +
        `</div>`
      );
    },
  });
})(window);
