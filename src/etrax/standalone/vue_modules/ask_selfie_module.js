(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "ask_selfie",
    label: "ask_selfie",
    defaultStep() {
      return {
        module_type: "ask_selfie",
        text_template: "Please send a selfie photo.",
        parse_mode: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "Thanks, your selfie was received.",
        invalid_text_template: "Please send a selfie photo.",
        require_original_capture_date: false,
        original_capture_max_age_minutes: 60,
        require_original_capture_same_day: true,
        original_capture_invalid_text_template: "Please send a selfie taken today within the last hour. If Telegram removes the original date, send the image as a file.",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "ask_selfie",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: source.success_text_template ? String(source.success_text_template) : "",
        invalid_text_template: source.invalid_text_template ? String(source.invalid_text_template) : "",
        require_original_capture_date: Boolean(source.require_original_capture_date),
        original_capture_max_age_minutes: source.original_capture_max_age_minutes
          ? Number(source.original_capture_max_age_minutes)
          : 60,
        require_original_capture_same_day: source.require_original_capture_same_day === undefined
          ? true
          : Boolean(source.require_original_capture_same_day),
        original_capture_invalid_text_template: source.original_capture_invalid_text_template
          ? String(source.original_capture_invalid_text_template)
          : "Please send a selfie taken today within the last hour. If Telegram removes the original date, send the image as a file.",
        require_finish_current_command: Boolean(source.require_finish_current_command),
        finish_current_command_text_template: source.finish_current_command_text_template
          ? String(source.finish_current_command_text_template)
          : "",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "ask_selfie") {
        return null;
      }
      return {
        module_type: "ask_selfie",
        text_template: parts[1] || "",
        success_text_template: parts[2] || "",
        invalid_text_template: parts[3] || "",
        parse_mode: parts[4] || "",
        require_original_capture_date: false,
        original_capture_max_age_minutes: 60,
        require_original_capture_same_day: true,
        original_capture_invalid_text_template: "Please send a selfie taken today within the last hour. If Telegram removes the original date, send the image as a file.",
        require_finish_current_command: false,
        finish_current_command_text_template: "",
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
      };
    },
    formatChain(step) {
      const prompt = String(step.text_template || "").trim();
      const successText = String(step.success_text_template || "").trim();
      const invalidText = String(step.invalid_text_template || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      let payload = `ask_selfie | ${prompt} | ${successText} | ${invalidText}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      const finishText = String(step.finish_current_command_text_template || "").trim();
      const requireOriginalDate = Boolean(step.require_original_capture_date);
      const originalDateInvalidText = String(step.original_capture_invalid_text_template || "").trim();
      const originalMaxAge = Number(step.original_capture_max_age_minutes || 60) || 60;
      const requireSameDay = step.require_original_capture_same_day === undefined
        ? true
        : Boolean(step.require_original_capture_same_day);
      if (step.require_finish_current_command || finishText || requireOriginalDate || originalDateInvalidText || originalMaxAge !== 60 || !requireSameDay) {
        return JSON.stringify({
          module_type: "ask_selfie",
          text_template: prompt,
          success_text_template: successText,
          invalid_text_template: invalidText,
          parse_mode: parseMode,
          require_original_capture_date: requireOriginalDate,
          original_capture_max_age_minutes: originalMaxAge,
          require_original_capture_same_day: requireSameDay,
          original_capture_invalid_text_template: originalDateInvalidText,
          require_finish_current_command: Boolean(step.require_finish_current_command),
          finish_current_command_text_template: finishText,
        });
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const prompt = String(step.text_template || "").trim();
      const preview = prompt.length > 32 ? `${prompt.slice(0, 32)}...` : prompt;
      return `#${stepNo} ask_selfie - ${preview || "(empty prompt)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Prompt Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Ask the user to send a selfie photo" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Success Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Shown after the user sends a selfie photo" ` +
        `:value="currentStepField(${ctx}, 'success_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'success_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Invalid Selfie Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" ` +
        `placeholder="Shown when the user sends something other than a photo" ` +
        `:value="currentStepField(${ctx}, 'invalid_text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'invalid_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')" class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_original_capture_date')" @change="updateCurrentStepToggle(${ctx}, 'require_original_capture_date', $event.target.checked)"><span>Require original photo date</span></label>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Original Date Max Age Minutes</label>` +
        `<input v-if="isStepType(${ctx}, 'ask_selfie')" type="number" min="1" step="1" :value="currentStepField(${ctx}, 'original_capture_max_age_minutes')" @input="updateCurrentStepField(${ctx}, 'original_capture_max_age_minutes', $event.target.value)">` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')" class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_original_capture_same_day')" @change="updateCurrentStepToggle(${ctx}, 'require_original_capture_same_day', $event.target.checked)"><span>Require same-day original date</span></label>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Original Date Invalid Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" placeholder="Shown when the photo original date is missing or too old" :value="currentStepField(${ctx}, 'original_capture_invalid_text_template')" @input="updateCurrentStepField(${ctx}, 'original_capture_invalid_text_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')" class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'require_finish_current_command')" @change="updateCurrentStepToggle(${ctx}, 'require_finish_current_command', $event.target.checked)"><span>Require this selfie before new actions</span></label>` +
        `<label v-if="isStepType(${ctx}, 'ask_selfie')">Blocked Action Text</label>` +
        `<textarea v-if="isStepType(${ctx}, 'ask_selfie')" placeholder="Please finish the current command before starting a new one." :value="currentStepField(${ctx}, 'finish_current_command_text_template')" @input="updateCurrentStepField(${ctx}, 'finish_current_command_text_template', $event.target.value)"></textarea>` +
        `<p class="hint" v-if="isStepType(${ctx}, 'ask_selfie')">If enabled, other commands and callbacks wait until the user sends a selfie. /restart is still allowed.</p>`
      );
    },
  });
})(window);
