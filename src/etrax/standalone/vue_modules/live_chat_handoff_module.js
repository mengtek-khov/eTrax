(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "live_chat_handoff",
    label: "live_chat_handoff",
    defaultStep() {
      return {
        module_type: "live_chat_handoff",
        text_template: "You're being connected with a support agent. Please wait here for their reply.",
        parse_mode: "",
        admin_chat_id: "",
        admin_notify_template: "",
        timeout_minutes: 30,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "live_chat_handoff",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        admin_chat_id: source.admin_chat_id ? String(source.admin_chat_id) : "",
        admin_notify_template: source.admin_notify_template ? String(source.admin_notify_template) : "",
        timeout_minutes: source.timeout_minutes ? Number(source.timeout_minutes) : 30,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "live_chat_handoff") {
        return null;
      }
      return {
        module_type: "live_chat_handoff",
        text_template: parts[1] || "",
        admin_chat_id: parts[2] || "",
        timeout_minutes: parts[3] ? Number(parts[3]) : 30,
        parse_mode: parts[4] || "",
        admin_notify_template: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
      };
    },
    formatChain(step) {
      const prompt = String(step.text_template || "").trim();
      const adminChatId = String(step.admin_chat_id || "").trim();
      const timeoutMinutes = Number(step.timeout_minutes || 30) || 30;
      const parseMode = String(step.parse_mode || "").trim();
      const adminNotifyTemplate = String(step.admin_notify_template || "").trim();
      if (adminNotifyTemplate) {
        return JSON.stringify({
          module_type: "live_chat_handoff",
          text_template: prompt,
          admin_chat_id: adminChatId,
          timeout_minutes: timeoutMinutes,
          parse_mode: parseMode,
          admin_notify_template: adminNotifyTemplate,
        });
      }
      let payload = `live_chat_handoff | ${prompt} | ${adminChatId} | ${timeoutMinutes}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const adminChatId = String(step.admin_chat_id || "").trim();
      return `#${stepNo} live_chat_handoff - agent chat ${adminChatId || "(not set)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'live_chat_handoff')">Handoff Text (shown to the user)</label>` +
        `<textarea v-if="isStepType(${ctx}, 'live_chat_handoff')" ` +
        `placeholder="You're being connected with a support agent. Please wait here for their reply." ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'live_chat_handoff')">Admin Telegram Chat ID</label>` +
        `<input v-if="isStepType(${ctx}, 'live_chat_handoff')" type="text" placeholder="e.g. 123456789" ` +
        `:value="currentStepField(${ctx}, 'admin_chat_id')" ` +
        `@input="updateCurrentStepField(${ctx}, 'admin_chat_id', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'live_chat_handoff')">The support agent replies from this chat with <code>/reply &lt;chat_id&gt; &lt;message&gt;</code> and ends the session with <code>/release &lt;chat_id&gt;</code>.</p>` +
        `<label v-if="isStepType(${ctx}, 'live_chat_handoff')">Timeout Minutes</label>` +
        `<input v-if="isStepType(${ctx}, 'live_chat_handoff')" type="number" min="1" step="1" ` +
        `:value="currentStepField(${ctx}, 'timeout_minutes')" ` +
        `@input="updateCurrentStepField(${ctx}, 'timeout_minutes', $event.target.value)">` +
        `<p class="hint" v-if="isStepType(${ctx}, 'live_chat_handoff')">The chat automatically returns to bot automation after this many minutes of inactivity.</p>` +
        `<label v-if="isStepType(${ctx}, 'live_chat_handoff')">Admin Notification Text (optional)</label>` +
        `<textarea v-if="isStepType(${ctx}, 'live_chat_handoff')" ` +
        `placeholder="Live chat requested by {user_first_name} (chat_id={chat_id})." ` +
        `:value="currentStepField(${ctx}, 'admin_notify_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'admin_notify_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
