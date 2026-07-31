(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  const barcodeQuickTokens = [
    "{selfie_barcode_value}",
    "{selfie_barcode_type}",
    "{selfie_barcode_qr_count}",
  ];

  const patternQuickTokens = [
    "{selfie_pattern_value}",
    "{selfie_pattern_type}",
    "{selfie_pattern_count}",
  ];

  const mrzQuickTokens = [
    "{selfie_mrz_document_number}",
    "{selfie_mrz_surname}",
    "{selfie_mrz_given_names}",
    "{selfie_mrz_nationality}",
    "{selfie_mrz_birth_date}",
    "{selfie_mrz_expiry_date}",
  ];

  const SCAN_MODES = ["none", "barcode_qr", "pattern", "mrz"];

  function renderQuickTokenToolbar(ctx, field, tokens) {
    return (
      `<div class="template-toolbar">` +
      tokens
        .map(
          (token) =>
            `<button type="button" class="secondary" @click="applyTemplateSnippet(${ctx}, '${field}', '${token}', '', $event)">${token}</button>`
        )
        .join("") +
      `</div>`
    );
  }

  function normalizeScanMode(value) {
    const candidate = String(value || "none");
    return SCAN_MODES.indexOf(candidate) === -1 ? "none" : candidate;
  }

  function scanModeRadio(ctx, mode, title, note) {
    return (
      `<label :class="['checkbox', 'compact', 'share-location-mode', { 'is-selected': (currentStepField(${ctx}, 'scan_mode') || 'none') === '${mode}' }]">` +
      `<input type="radio" :checked="(currentStepField(${ctx}, 'scan_mode') || 'none') === '${mode}'" @change="if ($event.target.checked) { updateCurrentStepField(${ctx}, 'scan_mode', '${mode}'); }">` +
      `<span class="share-location-mode-copy"><span class="share-location-mode-title">${title}</span><span class="share-location-mode-note">${note}</span></span>` +
      `</label>`
    );
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
        scan_mode: "none",
        scan_pattern_type: "phone_number",
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
        scan_mode: normalizeScanMode(source.scan_mode),
        scan_pattern_type: source.scan_pattern_type ? String(source.scan_pattern_type) : "phone_number",
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
        scan_mode: "none",
        scan_pattern_type: "phone_number",
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
      const scanMode = normalizeScanMode(step.scan_mode);
      const scanPatternType = String(step.scan_pattern_type || "phone_number");
      if (step.require_finish_current_command || finishText || requireOriginalDate || originalDateInvalidText || originalMaxAge !== 60 || !requireSameDay || scanMode !== "none") {
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
          scan_mode: scanMode,
          scan_pattern_type: scanPatternType,
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
        `<div v-if="isStepType(${ctx}, 'ask_selfie')">` +
        `<label>Image Scanning</label>` +
        `<p class="hint">Choose whether to run best-effort recognition on the selfie image after it is received.</p>` +
        `<div class="share-location-mode-grid">` +
        scanModeRadio(ctx, "none", "No Scan", "Just capture the selfie photo as usual.") +
        scanModeRadio(ctx, "barcode_qr", "Scan Barcode / QR", "Decodes any barcode or QR code found in the photo.") +
        scanModeRadio(ctx, "pattern", "Scan For Pattern", "Runs OCR and extracts a phone number, email, or ID/reference number.") +
        scanModeRadio(ctx, "mrz", "Scan MRZ (Passport / ID)", "Runs OCR and reads the machine-readable zone on a passport or ID card.") +
        `</div>` +
        `<p class="hint">Best-effort: none of these block the flow if nothing is found.</p>` +
        `</div>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'ask_selfie') && currentStepField(${ctx}, 'scan_mode') === 'barcode_qr'">` +
        `<p class="hint">Available keys include <code>{selfie_barcode_value}</code>, <code>{selfie_barcode_type}</code>, and <code>{selfie_barcode_qr_count}</code>. Insert into Success Text:</p>` +
        renderQuickTokenToolbar(ctx, "success_text_template", barcodeQuickTokens) +
        `</div>` +
        `<div v-if="isStepType(${ctx}, 'ask_selfie') && currentStepField(${ctx}, 'scan_mode') === 'pattern'">` +
        `<label>Pattern Type</label>` +
        `<select :value="currentStepField(${ctx}, 'scan_pattern_type') || 'phone_number'" @change="updateCurrentStepField(${ctx}, 'scan_pattern_type', $event.target.value)">` +
        `<option value="phone_number">Phone Number</option>` +
        `<option value="email">Email Address</option>` +
        `<option value="id_number">ID / Reference Number</option>` +
        `</select>` +
        `</div>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'ask_selfie') && currentStepField(${ctx}, 'scan_mode') === 'pattern'">` +
        `<p class="hint">Available keys include <code>{selfie_pattern_value}</code>, <code>{selfie_pattern_type}</code>, and <code>{selfie_pattern_count}</code>. Insert into Success Text:</p>` +
        renderQuickTokenToolbar(ctx, "success_text_template", patternQuickTokens) +
        `</div>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'ask_selfie') && currentStepField(${ctx}, 'scan_mode') === 'mrz'">` +
        `<p class="hint">Available keys include <code>{selfie_mrz_document_number}</code>, <code>{selfie_mrz_surname}</code>, <code>{selfie_mrz_given_names}</code>, <code>{selfie_mrz_nationality}</code>, <code>{selfie_mrz_birth_date}</code>, and <code>{selfie_mrz_expiry_date}</code>. Insert into Success Text:</p>` +
        renderQuickTokenToolbar(ctx, "success_text_template", mrzQuickTokens) +
        `</div>` +
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
