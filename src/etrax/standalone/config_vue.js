(function (global) {
  "use strict";

  // Bootstraps the standalone workflow editor once the shared module system is available.

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    global.EtraxConfigVue = {
      mount() {},
    };
    return;
  }

  const helpers = moduleSystem.helpers;

  function parseSerializedChainStep(rawLine) {
    // Supports the newer JSON-per-line chain-step format saved by the backend.
    const line = String(rawLine || "").trim();
    if (!line) {
      return null;
    }
    try {
      const payload = JSON.parse(line);
      if (!payload || typeof payload !== "object" || Array.isArray(payload) || !payload.module_type) {
        return null;
      }
      return moduleSystem.parsePrimary(payload.module_type, payload);
    } catch (_error) {
      return null;
    }
  }

  function parseChainSteps(raw) {
    // Accept both JSON-per-line and legacy pipe-delimited chain-step text.
    const steps = [];
    for (const line of helpers.splitLines(raw)) {
      const serialized = parseSerializedChainStep(line);
      if (serialized) {
        steps.push(serialized);
        continue;
      }
      const parts = line.split("|").map((part) => part.trim());
      const step = moduleSystem.parseChain(parts);
      if (step) {
        steps.push(step);
      }
    }
    return steps;
  }

  function normalizeCommandKey(rawValue) {
    let command = String(rawValue || "").trim();
    if (!command) {
      return "";
    }
    if (command.startsWith("/")) {
      command = command.slice(1);
    }
    const botSplitIndex = command.indexOf("@");
    if (botSplitIndex >= 0) {
      command = command.slice(0, botSplitIndex);
    }
    command = command.replace(/[-\s]+/g, "_");
    command = command
      .split("")
      .map((ch) => (/^[a-z0-9_]$/i.test(ch) ? ch.toLowerCase() : "_"))
      .join("");
    command = command.split("_").filter((part) => part.length > 0).join("_");
    if (!command) {
      return "";
    }
    if (/^\d/.test(command)) {
      command = `cmd_${command}`;
    }
    return command.slice(0, 32);
  }

  function createEditor(values) {
    // Wrap one module/pipeline payload in the editor state tracked by Vue.
    const source = normalizeEditorSeed(values);
    const moduleType = moduleSystem.normalizeType(source.module_type || moduleSystem.defaultType());
    const primary = moduleSystem.parsePrimary(moduleType, source);
    const chain = parseChainSteps(source.chain_steps || "");
    return {
      add_type: moduleType,
      visible: false,
      editing_index: null,
      steps: [primary, ...chain],
    };
  }

  function normalizeEditorSeed(values) {
    const source = values && typeof values === "object" ? values : {};
    const normalized = { ...source };
    if (!("title" in normalized) && "menu_title" in source) {
      normalized.title = source.menu_title;
    }
    if (!("items" in normalized) && "menu_items" in source) {
      normalized.items = source.menu_items;
    }
    if (!("run_if_context_keys" in normalized) && "inline_run_if_context_keys" in source) {
      normalized.run_if_context_keys = source.inline_run_if_context_keys;
    }
    if (!("skip_if_context_keys" in normalized) && "inline_skip_if_context_keys" in source) {
      normalized.skip_if_context_keys = source.inline_skip_if_context_keys;
    }
    if (!("save_callback_data_to_key" in normalized) && "inline_save_callback_data_to_key" in source) {
      normalized.save_callback_data_to_key = source.inline_save_callback_data_to_key;
    }
    if (!("target_callback_key" in normalized) && "callback_target_key" in source) {
      normalized.target_callback_key = source.callback_target_key;
    }
    if (!("target_command_key" in normalized) && "command_target_key" in source) {
      normalized.target_command_key = source.command_target_key;
    }
    if (!("button_text" in normalized)) {
      if ("contact_button_text" in source && String(source.contact_button_text || "").trim()) {
        normalized.button_text = source.contact_button_text;
      } else if ("mini_app_button_text" in source) {
        normalized.button_text = source.mini_app_button_text;
      }
    }
    if (!("success_text_template" in normalized) && "contact_success_text" in source) {
      normalized.success_text_template = source.contact_success_text;
    }
    if (!("invalid_text_template" in normalized) && "contact_invalid_text" in source) {
      normalized.invalid_text_template = source.contact_invalid_text;
    }
    if (!("empty_text_template" in normalized)) {
      if ("checkout_empty_text" in source && String(source.checkout_empty_text || "").trim()) {
        normalized.empty_text_template = source.checkout_empty_text;
      } else if ("payment_empty_text" in source) {
        normalized.empty_text_template = source.payment_empty_text;
      }
    }
    if (!("pay_button_text" in normalized) && "checkout_pay_button_text" in source) {
      normalized.pay_button_text = source.checkout_pay_button_text;
    }
    if (!("pay_callback_data" in normalized) && "checkout_pay_callback_data" in source) {
      normalized.pay_callback_data = source.checkout_pay_callback_data;
    }
    if (!("return_url" in normalized) && "payment_return_url" in source) {
      normalized.return_url = source.payment_return_url;
    }
    if (!("url" in normalized) && "mini_app_url" in source) {
      normalized.url = source.mini_app_url;
    }
    if (!("title_template" in normalized) && "payment_title_template" in source) {
      normalized.title_template = source.payment_title_template;
    }
    if (!("description_template" in normalized) && "payment_description_template" in source) {
      normalized.description_template = source.payment_description_template;
    }
    if (!("open_button_text" in normalized) && "payment_open_button_text" in source) {
      normalized.open_button_text = source.payment_open_button_text;
    }
    if (!("web_button_text" in normalized) && "payment_web_button_text" in source) {
      normalized.web_button_text = source.payment_web_button_text;
    }
    if (!("currency" in normalized) && "payment_currency" in source) {
      normalized.currency = source.payment_currency;
    }
    if (!("payment_limit" in normalized) && "payment_limit" in source) {
      normalized.payment_limit = source.payment_limit;
    }
    if (!("deep_link_prefix" in normalized) && "payment_deep_link_prefix" in source) {
      normalized.deep_link_prefix = source.payment_deep_link_prefix;
    }
    if (!("merchant_ref_prefix" in normalized) && "payment_merchant_ref_prefix" in source) {
      normalized.merchant_ref_prefix = source.payment_merchant_ref_prefix;
    }
    if (!("product_name" in normalized) && "cart_product_name" in source) {
      normalized.product_name = source.cart_product_name;
    }
    if (!("product_key" in normalized) && "cart_product_key" in source) {
      normalized.product_key = source.cart_product_key;
    }
    if (!("price" in normalized) && "cart_price" in source) {
      normalized.price = source.cart_price;
    }
    if (!("quantity" in normalized) && "cart_qty" in source) {
      normalized.quantity = source.cart_qty;
    }
    if (!("min_qty" in normalized) && "cart_min_qty" in source) {
      normalized.min_qty = source.cart_min_qty;
    }
    if (!("max_qty" in normalized) && "cart_max_qty" in source) {
      normalized.max_qty = source.cart_max_qty;
    }
    return normalized;
  }

  function defaultStartValues() {
    // Default values for the primary `/start` module editor.
    return {
      module_type: "send_message",
      text_template: "Welcome to our bot, {user_first_name}.",
      parse_mode: "",
      hide_caption: false,
      title: "Start Menu",
      items: [],
        buttons: [],
        save_callback_data_to_key: "",
        target_callback_key: "",
        target_command_key: "",
        photo_url: "",
      button_text: "",
      success_text_template: "",
      invalid_text_template: "",
      empty_text_template: "",
      pay_button_text: "",
      pay_callback_data: "",
      return_url: "",
      title_template: "",
      description_template: "",
      open_button_text: "",
      web_button_text: "",
      currency: "",
      payment_limit: "5",
      deep_link_prefix: "",
      merchant_ref_prefix: "",
      product_name: "",
      product_key: "",
      price: "",
      quantity: "1",
      min_qty: "0",
      max_qty: "99",
    };
  }

  function createCommandEntry(row) {
    // Normalize one command row coming from the preloaded config state.
    const source = row && typeof row === "object" ? row : {};
    const rawRestoreOriginalMenu = Object.prototype.hasOwnProperty.call(source, "restore_original_menu")
      ? source.restore_original_menu
      : true;
    return {
      command: source.command ? String(source.command) : "",
      description: source.description ? String(source.description) : "",
      restore_original_menu: !(
        rawRestoreOriginalMenu === false
        || rawRestoreOriginalMenu === 0
        || rawRestoreOriginalMenu === "0"
        || rawRestoreOriginalMenu === "false"
        || rawRestoreOriginalMenu === "False"
        || rawRestoreOriginalMenu === ""
      ),
      editor: createEditor(source),
    };
  }

  function createCallbackEntry(row) {
    // Normalize one callback row coming from the preloaded config state.
    const source = row && typeof row === "object" ? row : {};
    const temporaryCommands = Array.isArray(source.temporary_commands)
      ? source.temporary_commands.map((entry) => createCommandEntry(entry))
      : [];
    const hasSavedTemporaryCommands = temporaryCommands.some(
      (entry) => normalizeCommandKey(entry && entry.command ? entry.command : "").length > 0
    );
    return {
      callback_key: source.callback_key ? String(source.callback_key) : "",
      editor: createEditor(source),
      temporaryCommandEntries: temporaryCommands,
      tempCommandsExpanded: hasSavedTemporaryCommands,
    };
  }

  function createTemporaryMenuExampleCommand(command, description) {
    const normalizedCommand = normalizeCommandKey(command);
    return createCommandEntry({
      command: normalizedCommand,
      description: String(description || ""),
      module_type: "send_message",
      text_template: `Example /${normalizedCommand} temporary command. Replace this module with your real ${normalizedCommand} flow.`,
      parse_mode: "",
    });
  }

  function createModuleWithTempCommandExample() {
    const temporaryCommands = [
      createTemporaryMenuExampleCommand("command1", "Command 1"),
      createTemporaryMenuExampleCommand("command2", "Command 2"),
    ];
    return {
      commandEntry: createCommandEntry({
        command: "temp_menu",
        description: "Module with temp command",
        module_type: "callback_module",
        callback_target_key: "temp_menu",
      }),
      callbackEntry: createCallbackEntry({
        callback_key: "temp_menu",
        module_type: "send_message",
        text_template:
          "Temporary command menu is active for this chat. Use /command1 or /command2.",
        temporary_commands: temporaryCommands.map((entry) => {
          const serialized = {
            command: entry.command,
            description: entry.description,
            module_type: "send_message",
            text_template: "",
          };
          const editor = entry && entry.editor && typeof entry.editor === "object" ? entry.editor : null;
          if (editor && Array.isArray(editor.steps) && editor.steps.length > 0) {
            const step = editor.steps[0];
            serialized.module_type = step && step.module_type ? String(step.module_type) : "send_message";
            serialized.text_template = step && step.text_template ? String(step.text_template) : "";
          }
          return serialized;
        }),
      }),
    };
  }

  function parseState(rawState) {
    // Convert the server-provided JSON blob into the reactive Vue state shape.
    const parsed = rawState && typeof rawState === "object" ? rawState : {};
    const start = parsed.start && typeof parsed.start === "object" ? parsed.start : {};
    const commandRows = Array.isArray(parsed.commands) ? parsed.commands : [];
    const callbackRows = Array.isArray(parsed.callbacks) ? parsed.callbacks : [];
    const profileLogContextKeys = Array.isArray(parsed.context_key_options)
      ? parsed.context_key_options
        .map((value) => String(value || "").trim())
        .filter((value) => Boolean(value))
      : [];
    const startReturningTextTemplate = typeof start.start_returning_text_template === "string"
      ? start.start_returning_text_template
      : "Welcome back, {user_first_name}.";
    return {
      moduleOptions: moduleSystem.optionList(),
      profileLogContextKeys,
      startDescription: start.description ? String(start.description) : "",
      startReturningTextTemplate: String(startReturningTextTemplate || "Welcome back, {user_first_name}."),
      startEditor: createEditor(start.module_values || defaultStartValues()),
      commandEntries: commandRows.map((row) => createCommandEntry(row)),
      callbackEntries: callbackRows.map((row) => createCallbackEntry(row)),
    };
  }

  function renderModuleEditorSections(contextExpression, idPrefix) {
    // Compose the per-module editor panes registered in the shared module system.
    let html = "";
    for (const option of moduleSystem.optionList()) {
      html += moduleSystem.editorTemplate(option.type, contextExpression, idPrefix);
    }
    return html;
  }

  function appTemplate() {
    // Main HTML template for the standalone config editor.
    return `
<div class="module-block" id="start-module-setup">
  <p class="module-title">/start Command Setup</p>
  <div class="command-row no-action">
    <input value="/start" readonly>
    <input id="start_command_description" name="start_command_description" placeholder="Start bot" v-model="startDescription">
  </div>
  <label for="start_returning_text_template">Welcome Back Message</label>
  <textarea id="start_returning_text_template" rows="3" v-model="startReturningTextTemplate"></textarea>
  <div class="module-list-tools">
    <select v-model="startEditor.add_type">
      <option v-for="option in availableModuleOptions" :key="'start-opt-' + option.type" :value="option.type">[[ option.label ]]</option>
    </select>
    <button type="button" class="secondary" @click="addModule(startEditor)">Add Module</button>
  </div>
  <div class="module-list">
    <div v-for="(step, moduleIndex) in startEditor.steps" :key="'start-' + moduleIndex" :class="moduleRowClass(startEditor, moduleIndex)">
      <div class="module-list-meta">[[ moduleRowLabel(step, moduleIndex, isEditing(startEditor, moduleIndex)) ]]</div>
      <div class="module-list-actions">
        <button type="button" @click="editModule(startEditor, moduleIndex)">Edit</button>
        <button type="button" :disabled="moduleIndex === 0" @click="moveModuleUp(startEditor, moduleIndex)">Up</button>
        <button type="button" :disabled="moduleIndex >= startEditor.steps.length - 1" @click="moveModuleDown(startEditor, moduleIndex)">Down</button>
        <button type="button" @click="removeModule(startEditor, moduleIndex)">Remove</button>
      </div>
    </div>
  </div>
  <p class="module-editor-placeholder" v-if="!startEditor.visible">Click Edit on a module row to load Module Setup.</p>
  <div class="module-editor" v-if="startEditor.visible">
    <div class="module-grid">
      <div>
        <label for="start_module_type_display">Module Type (locked)</label>
        <input id="start_module_type_display" :value="currentStepType(startEditor)" readonly>
      </div>
      <div>
        <label for="start_parse_mode">Parse Mode (optional)</label>
        <input id="start_parse_mode" placeholder="HTML or MarkdownV2" :value="currentStepField(startEditor, 'parse_mode')" @input="updateCurrentStepField(startEditor, 'parse_mode', $event.target.value)">
      </div>
      <div>
        <label>Reset Current Module</label>
        <button type="button" class="secondary" @click="resetCurrentModule(startEditor)">Reset To Default</button>
      </div>
    </div>
    ${renderModuleEditorSections("startEditor", "start_")}
  </div>
  <input type="hidden" name="start_module_type" :value="startPrimary.module_type">
  <input type="hidden" name="start_text_template" :value="startPrimary.text_template">
  <input type="hidden" name="start_parse_mode" :value="startPrimary.parse_mode">
  <input type="hidden" name="start_hide_caption" :value="startPrimary.hide_caption ? '1' : ''">
  <input type="hidden" name="start_menu_title" :value="startPrimary.title">
  <input type="hidden" name="start_menu_items" :value="formatMenuItems(startPrimary.items)">
  <input type="hidden" name="start_inline_buttons" :value="formatInlineButtons(startPrimary.buttons)">
  <input type="hidden" name="start_inline_run_if_context_keys" :value="startPrimary.run_if_context_keys">
  <input type="hidden" name="start_inline_skip_if_context_keys" :value="startPrimary.skip_if_context_keys">
  <input type="hidden" name="start_inline_save_callback_data_to_key" :value="startPrimary.save_callback_data_to_key">
  <input type="hidden" name="start_callback_target_key" :value="startPrimary.target_callback_key">
  <input type="hidden" name="start_command_target_key" :value="startPrimary.target_command_key">
  <input type="hidden" name="start_photo_url" :value="startPrimary.photo_url">
  <input type="hidden" name="start_contact_button_text" :value="startPrimary.button_text">
  <input type="hidden" name="start_mini_app_button_text" :value="startPrimary.button_text">
  <input type="hidden" name="start_contact_success_text" :value="startPrimary.success_text_template">
  <input type="hidden" name="start_contact_invalid_text" :value="startPrimary.invalid_text_template">
  <input type="hidden" name="start_require_live_location" :value="startPrimary.require_live_location ? '1' : ''">
  <input type="hidden" name="start_track_breadcrumb" :value="startPrimary.track_breadcrumb ? '1' : ''">
  <input type="hidden" name="start_store_history_by_day" :value="startPrimary.store_history_by_day ? '1' : ''">
  <input type="hidden" name="start_breadcrumb_interval_minutes" :value="startPrimary.breadcrumb_interval_minutes">
  <input type="hidden" name="start_breadcrumb_min_distance_meters" :value="startPrimary.breadcrumb_min_distance_meters">
  <input type="hidden" name="start_checkout_empty_text" :value="startPrimary.empty_text_template">
  <input type="hidden" name="start_checkout_pay_button_text" :value="startPrimary.pay_button_text">
  <input type="hidden" name="start_checkout_pay_callback_data" :value="startPrimary.pay_callback_data">
  <input type="hidden" name="start_payment_empty_text" :value="startPrimary.empty_text_template">
  <input type="hidden" name="start_payment_return_url" :value="startPrimary.return_url">
  <input type="hidden" name="start_mini_app_url" :value="startPrimary.return_url">
  <input type="hidden" name="start_payment_title_template" :value="startPrimary.title_template">
  <input type="hidden" name="start_payment_description_template" :value="startPrimary.description_template">
  <input type="hidden" name="start_payment_open_button_text" :value="startPrimary.open_button_text">
  <input type="hidden" name="start_payment_web_button_text" :value="startPrimary.web_button_text">
  <input type="hidden" name="start_payment_currency" :value="startPrimary.currency">
  <input type="hidden" name="start_payment_limit" :value="startPrimary.payment_limit">
  <input type="hidden" name="start_payment_deep_link_prefix" :value="startPrimary.deep_link_prefix">
  <input type="hidden" name="start_payment_merchant_ref_prefix" :value="startPrimary.merchant_ref_prefix">
  <input type="hidden" name="start_cart_product_name" :value="startPrimary.product_name">
  <input type="hidden" name="start_cart_product_key" :value="startPrimary.product_key">
  <input type="hidden" name="start_cart_price" :value="startPrimary.price">
  <input type="hidden" name="start_cart_qty" :value="startPrimary.quantity">
  <input type="hidden" name="start_cart_min_qty" :value="startPrimary.min_qty">
  <input type="hidden" name="start_cart_max_qty" :value="startPrimary.max_qty">
  <input type="hidden" name="start_chain_steps" :value="formatChainSteps(startEditor.steps.slice(1))">
  <input type="hidden" name="start_returning_text_template" :value="startReturningTextTemplate">

	  <label>Custom Commands</label>
	  <p class="hint">Each command has its own process module setup panel.</p>
	  <div id="command-list" class="command-list">
	    <div class="command-entry" v-for="(entry, commandIndex) in commandEntries" :key="'cmd-' + commandIndex">
      <p class="command-panel-title">[[ commandPanelTitle(entry.command) ]]</p>
      <div class="command-row">
        <input placeholder="/help" v-model="entry.command">
        <input placeholder="Get help" v-model="entry.description">
        <button type="button" @click="removeCommand(commandIndex)">Remove</button>
      </div>
      <div class="module-list-tools">
        <select v-model="entry.editor.add_type">
          <option v-for="option in availableModuleOptions" :key="'cmd-opt-' + commandIndex + '-' + option.type" :value="option.type">[[ option.label ]]</option>
        </select>
        <button type="button" class="secondary" @click="addModule(entry.editor)">Add Module</button>
      </div>
      <div class="module-list">
        <div v-for="(step, moduleIndex) in entry.editor.steps" :key="'cmd-' + commandIndex + '-' + moduleIndex" :class="moduleRowClass(entry.editor, moduleIndex)">
          <div class="module-list-meta">[[ moduleRowLabel(step, moduleIndex, isEditing(entry.editor, moduleIndex)) ]]</div>
          <div class="module-list-actions">
            <button type="button" @click="editModule(entry.editor, moduleIndex)">Edit</button>
            <button type="button" :disabled="moduleIndex === 0" @click="moveModuleUp(entry.editor, moduleIndex)">Up</button>
            <button type="button" :disabled="moduleIndex >= entry.editor.steps.length - 1" @click="moveModuleDown(entry.editor, moduleIndex)">Down</button>
            <button type="button" @click="removeModule(entry.editor, moduleIndex)">Remove</button>
          </div>
        </div>
      </div>
      <p class="module-editor-placeholder" v-if="!entry.editor.visible">Click Edit on a module row to load Module Setup.</p>
      <div class="module-editor" v-if="entry.editor.visible">
        <div class="module-grid">
          <div>
            <label>Module Type (locked)</label>
            <input :value="currentStepType(entry.editor)" readonly>
          </div>
          <div>
            <label>Parse Mode (optional)</label>
            <input placeholder="HTML or MarkdownV2" :value="currentStepField(entry.editor, 'parse_mode')" @input="updateCurrentStepField(entry.editor, 'parse_mode', $event.target.value)">
          </div>
          <div>
            <label>Reset Current Module</label>
            <button type="button" class="secondary" @click="resetCurrentModule(entry.editor)">Reset To Default</button>
          </div>
        </div>
        ${renderModuleEditorSections("entry.editor", "")}
      </div>
      <input type="hidden" name="command_name" :value="entry.command">
      <input type="hidden" name="command_description" :value="entry.description">
      <input type="hidden" name="command_module_type" :value="primaryStep(entry.editor).module_type">
      <input type="hidden" name="command_text_template" :value="primaryStep(entry.editor).text_template">
	      <input type="hidden" name="command_hide_caption" :value="primaryStep(entry.editor).hide_caption ? '1' : ''">
	      <input type="hidden" name="command_parse_mode" :value="primaryStep(entry.editor).parse_mode">
	      <input type="hidden" name="command_menu_title" :value="primaryStep(entry.editor).title">
	      <input type="hidden" name="command_menu_items" :value="formatMenuItems(primaryStep(entry.editor).items)">
	      <input type="hidden" name="command_inline_buttons" :value="formatInlineButtons(primaryStep(entry.editor).buttons)">
	      <input type="hidden" name="command_inline_run_if_context_keys" :value="primaryStep(entry.editor).run_if_context_keys">
	      <input type="hidden" name="command_inline_skip_if_context_keys" :value="primaryStep(entry.editor).skip_if_context_keys">
	      <input type="hidden" name="command_inline_save_callback_data_to_key" :value="primaryStep(entry.editor).save_callback_data_to_key">
	      <input type="hidden" name="command_callback_target_key" :value="primaryStep(entry.editor).target_callback_key">
	      <input type="hidden" name="command_command_target_key" :value="primaryStep(entry.editor).target_command_key">
	      <input type="hidden" name="command_photo_url" :value="primaryStep(entry.editor).photo_url">
	      <input type="hidden" name="command_contact_button_text" :value="primaryStep(entry.editor).button_text">
	      <input type="hidden" name="command_mini_app_button_text" :value="primaryStep(entry.editor).button_text">
      <input type="hidden" name="command_contact_success_text" :value="primaryStep(entry.editor).success_text_template">
      <input type="hidden" name="command_contact_invalid_text" :value="primaryStep(entry.editor).invalid_text_template">
      <input type="hidden" name="command_require_live_location" :value="primaryStep(entry.editor).require_live_location ? '1' : ''">
      <input type="hidden" name="command_track_breadcrumb" :value="primaryStep(entry.editor).track_breadcrumb ? '1' : ''">
      <input type="hidden" name="command_store_history_by_day" :value="primaryStep(entry.editor).store_history_by_day ? '1' : ''">
      <input type="hidden" name="command_breadcrumb_interval_minutes" :value="primaryStep(entry.editor).breadcrumb_interval_minutes">
      <input type="hidden" name="command_breadcrumb_min_distance_meters" :value="primaryStep(entry.editor).breadcrumb_min_distance_meters">
      <input type="hidden" name="command_checkout_empty_text" :value="primaryStep(entry.editor).empty_text_template">
      <input type="hidden" name="command_payment_empty_text" :value="primaryStep(entry.editor).empty_text_template">
      <input type="hidden" name="command_checkout_pay_button_text" :value="primaryStep(entry.editor).pay_button_text">
      <input type="hidden" name="command_checkout_pay_callback_data" :value="primaryStep(entry.editor).pay_callback_data">
      <input type="hidden" name="command_payment_return_url" :value="primaryStep(entry.editor).return_url">
      <input type="hidden" name="command_mini_app_url" :value="primaryStep(entry.editor).return_url">
	      <input type="hidden" name="command_payment_title_template" :value="primaryStep(entry.editor).title_template">
	      <input type="hidden" name="command_payment_description_template" :value="primaryStep(entry.editor).description_template">
	      <input type="hidden" name="command_payment_open_button_text" :value="primaryStep(entry.editor).open_button_text">
	      <input type="hidden" name="command_payment_web_button_text" :value="primaryStep(entry.editor).web_button_text">
	      <input type="hidden" name="command_payment_currency" :value="primaryStep(entry.editor).currency">
	      <input type="hidden" name="command_payment_limit" :value="primaryStep(entry.editor).payment_limit">
	      <input type="hidden" name="command_payment_deep_link_prefix" :value="primaryStep(entry.editor).deep_link_prefix">
	      <input type="hidden" name="command_payment_merchant_ref_prefix" :value="primaryStep(entry.editor).merchant_ref_prefix">
	      <input type="hidden" name="command_cart_product_name" :value="primaryStep(entry.editor).product_name">
	      <input type="hidden" name="command_cart_product_key" :value="primaryStep(entry.editor).product_key">
	      <input type="hidden" name="command_cart_price" :value="primaryStep(entry.editor).price">
	      <input type="hidden" name="command_cart_qty" :value="primaryStep(entry.editor).quantity">
	      <input type="hidden" name="command_cart_min_qty" :value="primaryStep(entry.editor).min_qty">
	      <input type="hidden" name="command_cart_max_qty" :value="primaryStep(entry.editor).max_qty">
	      <input type="hidden" name="command_chain_steps" :value="formatChainSteps(entry.editor.steps.slice(1))">
	    </div>
	  </div>
	  <div class="actions">
	    <button type="button" class="secondary" @click="addCommand">Add Command</button>
	    <button type="button" class="secondary" @click="addModuleWithTempCommandExample">Add command with temp command</button>
	  </div>

		  <label>Callback Modules</label>
		  <p class="hint">Match callback module keys to inline-button <code>callback_data</code> values.</p>
		  <datalist id="command-key-options">
		    <option v-for="commandKey in commandOptions" :key="'command-opt-' + commandKey" :value="commandKey">[[ commandKey ]]</option>
		  </datalist>
		  <datalist id="callback-key-options">
		    <option v-for="callbackKey in callbackOptions" :key="'callback-opt-' + callbackKey" :value="callbackKey">[[ callbackKey ]]</option>
		  </datalist>
		  <datalist id="callback-data-options">
		    <option v-for="callbackKey in callbackOptions" :key="'callback-data-opt-' + callbackKey" :value="callbackKey">[[ callbackKey ]]</option>
		  </datalist>
		  <div id="callback-list" class="command-list">
	    <div class="command-entry" v-for="(entry, callbackIndex) in callbackEntries" :key="'callback-' + (entry.callback_key || callbackIndex)">
	      <p class="command-panel-title">[[ callbackPanelTitle(entry.callback_key) ]]</p>
	      <div class="command-row">
	        <input placeholder="Driver" list="callback-key-options" v-model="entry.callback_key">
	        <select class="inline-button-input" :value="entry.callback_key" @change="applyCallbackSuggestion(entry, $event.target.value)">
	          <option value="">Select callback_data from current module setup</option>
	          <option v-for="callbackKey in callbackOptions" :key="'callback-select-' + callbackIndex + '-' + callbackKey" :value="callbackKey">[[ callbackKey ]]</option>
	        </select>
	        <button type="button" @click="removeCallback(callbackIndex)">Remove</button>
	      </div>
	      <div class="module-list-tools">
	        <select v-model="entry.editor.add_type">
	          <option v-for="option in availableModuleOptions" :key="'callback-module-opt-' + callbackIndex + '-' + option.type" :value="option.type">[[ option.label ]]</option>
	        </select>
	        <button type="button" class="secondary" @click="addModule(entry.editor)">Add Module</button>
	      </div>
	      <div class="module-list">
	        <div v-for="(step, moduleIndex) in entry.editor.steps" :key="'callback-' + callbackIndex + '-' + moduleIndex" :class="moduleRowClass(entry.editor, moduleIndex)">
	          <div class="module-list-meta">[[ moduleRowLabel(step, moduleIndex, isEditing(entry.editor, moduleIndex)) ]]</div>
	          <div class="module-list-actions">
	            <button type="button" @click="editModule(entry.editor, moduleIndex)">Edit</button>
	            <button type="button" :disabled="moduleIndex === 0" @click="moveModuleUp(entry.editor, moduleIndex)">Up</button>
	            <button type="button" :disabled="moduleIndex >= entry.editor.steps.length - 1" @click="moveModuleDown(entry.editor, moduleIndex)">Down</button>
	            <button type="button" @click="removeModule(entry.editor, moduleIndex)">Remove</button>
	          </div>
	        </div>
	      </div>
	      <p class="module-editor-placeholder" v-if="!entry.editor.visible">Click Edit on a module row to load Callback Module Setup.</p>
	      <div class="module-editor" v-if="entry.editor.visible">
	        <div class="module-grid">
	          <div>
	            <label>Module Type (locked)</label>
	            <input :value="currentStepType(entry.editor)" readonly>
	          </div>
	          <div>
	            <label>Parse Mode (optional)</label>
	            <input placeholder="HTML or MarkdownV2" :value="currentStepField(entry.editor, 'parse_mode')" @input="updateCurrentStepField(entry.editor, 'parse_mode', $event.target.value)">
	          </div>
	          <div>
	            <label>Reset Current Module</label>
	            <button type="button" class="secondary" @click="resetCurrentModule(entry.editor)">Reset To Default</button>
	          </div>
	        </div>
	        ${renderModuleEditorSections("entry.editor", "")}
	      </div>
	      <div class="callback-submenu-block" v-if="showTemporaryCommands(entry)">
        <label>Temporary Commands After This Callback</label>
        <p class="hint">When this callback runs, Telegram command menu switches to these commands for this chat only. After one of them finishes, the main command menu returns.</p>
        <div class="actions">
          <button type="button" class="secondary" @click="clearTemporaryCommands(entry)">Clear Temporary Commands</button>
        </div>
        <div class="command-list">
          <div class="command-entry" v-for="(tempEntry, tempCommandIndex) in entry.temporaryCommandEntries" :key="'callback-temp-' + (entry.callback_key || callbackIndex) + '-' + (tempEntry.command || tempCommandIndex)">
            <p class="command-panel-title">[[ commandPanelTitle(tempEntry.command) ]]</p>
            <div class="command-row">
              <input placeholder="/next" v-model="tempEntry.command">
              <input placeholder="Next step" v-model="tempEntry.description">
              <button type="button" @click="removeTemporaryCommand(entry, tempCommandIndex)">Remove</button>
            </div>
            <label class="checkbox">
              <input type="checkbox" v-model="tempEntry.restore_original_menu">
              Reset to original command menu after this temp command runs
            </label>
            <div class="module-list-tools">
              <select v-model="tempEntry.editor.add_type">
                <option v-for="option in availableModuleOptions" :key="'callback-temp-opt-' + callbackIndex + '-' + tempCommandIndex + '-' + option.type" :value="option.type">[[ option.label ]]</option>
              </select>
              <button type="button" class="secondary" @click="addModule(tempEntry.editor)">Add Module</button>
            </div>
            <div class="module-list">
              <div v-for="(step, moduleIndex) in tempEntry.editor.steps" :key="'callback-temp-step-' + callbackIndex + '-' + tempCommandIndex + '-' + moduleIndex" :class="moduleRowClass(tempEntry.editor, moduleIndex)">
                <div class="module-list-meta">[[ moduleRowLabel(step, moduleIndex, isEditing(tempEntry.editor, moduleIndex)) ]]</div>
                <div class="module-list-actions">
                  <button type="button" @click="editModule(tempEntry.editor, moduleIndex)">Edit</button>
                  <button type="button" :disabled="moduleIndex === 0" @click="moveModuleUp(tempEntry.editor, moduleIndex)">Up</button>
                  <button type="button" :disabled="moduleIndex >= tempEntry.editor.steps.length - 1" @click="moveModuleDown(tempEntry.editor, moduleIndex)">Down</button>
                  <button type="button" @click="removeModule(tempEntry.editor, moduleIndex)">Remove</button>
                </div>
              </div>
            </div>
            <p class="module-editor-placeholder" v-if="!tempEntry.editor.visible">Click Edit on a module row to load Temporary Command Module Setup.</p>
            <div class="module-editor" v-if="tempEntry.editor.visible">
              <div class="module-grid">
                <div>
                  <label>Module Type (locked)</label>
                  <input :value="currentStepType(tempEntry.editor)" readonly>
                </div>
                <div>
                  <label>Parse Mode (optional)</label>
                  <input placeholder="HTML or MarkdownV2" :value="currentStepField(tempEntry.editor, 'parse_mode')" @input="updateCurrentStepField(tempEntry.editor, 'parse_mode', $event.target.value)">
                </div>
                <div>
                  <label>Reset Current Module</label>
                  <button type="button" class="secondary" @click="resetCurrentModule(tempEntry.editor)">Reset To Default</button>
                </div>
              </div>
              ${renderModuleEditorSections("tempEntry.editor", "")}
            </div>
          </div>
        </div>
      </div>
	      <div class="actions">
	        <button type="button" class="secondary" @click="addTemporaryCommand(entry)">
            [[ temporaryCommandsButtonLabel(entry) ]]
          </button>
	      </div>

      <input type="hidden" name="callback_key" :value="entry.callback_key">
      <input type="hidden" name="callback_module_type" :value="primaryStep(entry.editor).module_type">
      <input type="hidden" name="callback_text_template" :value="primaryStep(entry.editor).text_template">
      <input type="hidden" name="callback_hide_caption" :value="primaryStep(entry.editor).hide_caption ? '1' : ''">
      <input type="hidden" name="callback_parse_mode" :value="primaryStep(entry.editor).parse_mode">
      <input type="hidden" name="callback_menu_title" :value="primaryStep(entry.editor).title">
      <input type="hidden" name="callback_menu_items" :value="formatMenuItems(primaryStep(entry.editor).items)">
      <input type="hidden" name="callback_inline_buttons" :value="formatInlineButtons(primaryStep(entry.editor).buttons)">
      <input type="hidden" name="callback_inline_run_if_context_keys" :value="primaryStep(entry.editor).run_if_context_keys">
      <input type="hidden" name="callback_inline_skip_if_context_keys" :value="primaryStep(entry.editor).skip_if_context_keys">
      <input type="hidden" name="callback_inline_save_callback_data_to_key" :value="primaryStep(entry.editor).save_callback_data_to_key">
      <input type="hidden" name="callback_callback_target_key" :value="primaryStep(entry.editor).target_callback_key">
      <input type="hidden" name="callback_command_target_key" :value="primaryStep(entry.editor).target_command_key">
      <input type="hidden" name="callback_photo_url" :value="primaryStep(entry.editor).photo_url">
      <input type="hidden" name="callback_contact_button_text" :value="primaryStep(entry.editor).button_text">
      <input type="hidden" name="callback_mini_app_button_text" :value="primaryStep(entry.editor).button_text">
      <input type="hidden" name="callback_contact_success_text" :value="primaryStep(entry.editor).success_text_template">
      <input type="hidden" name="callback_contact_invalid_text" :value="primaryStep(entry.editor).invalid_text_template">
      <input type="hidden" name="callback_require_live_location" :value="primaryStep(entry.editor).require_live_location ? '1' : ''">
      <input type="hidden" name="callback_track_breadcrumb" :value="primaryStep(entry.editor).track_breadcrumb ? '1' : ''">
      <input type="hidden" name="callback_store_history_by_day" :value="primaryStep(entry.editor).store_history_by_day ? '1' : ''">
      <input type="hidden" name="callback_breadcrumb_interval_minutes" :value="primaryStep(entry.editor).breadcrumb_interval_minutes">
      <input type="hidden" name="callback_breadcrumb_min_distance_meters" :value="primaryStep(entry.editor).breadcrumb_min_distance_meters">
      <input type="hidden" name="callback_checkout_empty_text" :value="primaryStep(entry.editor).empty_text_template">
      <input type="hidden" name="callback_payment_empty_text" :value="primaryStep(entry.editor).empty_text_template">
      <input type="hidden" name="callback_checkout_pay_button_text" :value="primaryStep(entry.editor).pay_button_text">
      <input type="hidden" name="callback_checkout_pay_callback_data" :value="primaryStep(entry.editor).pay_callback_data">
      <input type="hidden" name="callback_payment_return_url" :value="primaryStep(entry.editor).return_url">
      <input type="hidden" name="callback_mini_app_url" :value="primaryStep(entry.editor).return_url">
      <input type="hidden" name="callback_payment_title_template" :value="primaryStep(entry.editor).title_template">
      <input type="hidden" name="callback_payment_description_template" :value="primaryStep(entry.editor).description_template">
      <input type="hidden" name="callback_payment_open_button_text" :value="primaryStep(entry.editor).open_button_text">
      <input type="hidden" name="callback_payment_web_button_text" :value="primaryStep(entry.editor).web_button_text">
      <input type="hidden" name="callback_payment_currency" :value="primaryStep(entry.editor).currency">
      <input type="hidden" name="callback_payment_limit" :value="primaryStep(entry.editor).payment_limit">
      <input type="hidden" name="callback_payment_deep_link_prefix" :value="primaryStep(entry.editor).deep_link_prefix">
      <input type="hidden" name="callback_payment_merchant_ref_prefix" :value="primaryStep(entry.editor).merchant_ref_prefix">
      <input type="hidden" name="callback_cart_product_name" :value="primaryStep(entry.editor).product_name">
      <input type="hidden" name="callback_cart_product_key" :value="primaryStep(entry.editor).product_key">
      <input type="hidden" name="callback_cart_price" :value="primaryStep(entry.editor).price">
      <input type="hidden" name="callback_cart_qty" :value="primaryStep(entry.editor).quantity">
      <input type="hidden" name="callback_cart_min_qty" :value="primaryStep(entry.editor).min_qty">
      <input type="hidden" name="callback_cart_max_qty" :value="primaryStep(entry.editor).max_qty">
      <input type="hidden" name="callback_chain_steps" :value="formatChainSteps(entry.editor.steps.slice(1))">
      <input type="hidden" name="callback_temporary_commands" :value="serializeTemporaryCommands(entry.temporaryCommandEntries)">
    </div>
  </div>
  <div class="actions">
    <button type="button" class="secondary" @click="addCallback">Add Callback Module</button>
  </div>
  <div class="actions">
    <button type="button" class="secondary" @click="resetAllToStartDefault">Reset Everything To /start Default</button>
  </div>
</div>
		`;
  }

  function buildVueOptions(initialState) {
    // Build the Vue app that manages module editing, ordering, and serialization.
    return {
      delimiters: ["[[", "]]"],
      data() {
        return parseState(initialState);
      },
      computed: {
        startPrimary() {
          return this.primaryStep(this.startEditor);
        },
        hasCartModuleConfigured() {
          const editors = [this.startEditor];
          for (const entry of this.commandEntries) {
            editors.push(entry.editor);
          }
          for (const entry of this.callbackEntries) {
            editors.push(entry.editor);
          }
          for (const editor of editors) {
            if (!editor || !Array.isArray(editor.steps)) {
              continue;
            }
            for (const step of editor.steps) {
              if (moduleSystem.normalizeType(step && step.module_type) === "cart_button") {
                return true;
              }
            }
          }
          return false;
        },
        availableModuleOptions() {
          if (this.hasCartModuleConfigured) {
            return this.moduleOptions;
          }
          return this.moduleOptions.filter((option) => option.type !== "checkout" && option.type !== "payway_payment");
        },
        callbackOptions() {
          // Keep one shared suggestion list for callback keys referenced across modules.
          const options = [];
          const seen = new Set();
          const addOption = (rawValue) => {
            const callbackKey = String(rawValue || "").trim();
            if (!callbackKey || seen.has(callbackKey)) {
              return;
            }
            seen.add(callbackKey);
            options.push(callbackKey);
          };
          const collect = (editor) => {
            if (!editor || !Array.isArray(editor.steps)) {
              return;
            }
            for (const step of editor.steps) {
              addOption(step && step.pay_callback_data ? step.pay_callback_data : "");
              const buttons = helpers.normalizeInlineButtons(step && step.buttons ? step.buttons : []);
              for (const button of buttons) {
                addOption(button && button.callback_data ? button.callback_data : "");
              }
            }
          };
          for (const entry of this.callbackEntries) {
            addOption(entry && entry.callback_key ? entry.callback_key : "");
          }
          collect(this.startEditor);
          for (const entry of this.commandEntries) {
            collect(entry.editor);
          }
          for (const entry of this.callbackEntries) {
            collect(entry.editor);
          }
          return options;
        },
        contextKeyOptions() {
          const options = [];
          const seen = new Set();
          const addOption = (rawValue) => {
            const contextKey = String(rawValue || "").trim();
            if (!contextKey || seen.has(contextKey)) {
              return;
            }
            seen.add(contextKey);
            options.push(contextKey);
          };
          for (const value of this.profileLogContextKeys) {
            addOption(value);
          }
          return options;
        },
      },
      methods: {
        primaryStep(editor) {
          if (!editor || !Array.isArray(editor.steps) || !editor.steps.length) {
            return moduleSystem.defaultStep(moduleSystem.defaultType());
          }
          return editor.steps[0];
        },
        ensureEditor(editor) {
          if (!editor.steps.length) {
            editor.steps.push(moduleSystem.defaultStep(editor.add_type || moduleSystem.defaultType()));
          }
          if (editor.editing_index == null || editor.editing_index < 0 || editor.editing_index >= editor.steps.length) {
            editor.editing_index = 0;
          }
        },
        currentStep(editor) {
          this.ensureEditor(editor);
          return editor.steps[editor.editing_index];
        },
        currentStepType(editor) {
          return moduleSystem.normalizeType(this.currentStep(editor).module_type);
        },
        isStepType(editor, type) {
          return this.currentStepType(editor) === moduleSystem.normalizeType(type);
        },
        currentStepField(editor, field) {
          const step = this.currentStep(editor);
          const value = step && field in step ? step[field] : "";
          return value == null ? "" : String(value);
        },
        hasMeaningfulTemporaryCommands(entry) {
          if (!entry || !Array.isArray(entry.temporaryCommandEntries)) {
            return false;
          }
          return entry.temporaryCommandEntries.some(
            (tempEntry) => normalizeCommandKey(tempEntry && tempEntry.command ? tempEntry.command : "").length > 0
          );
        },
        showTemporaryCommands(entry) {
          if (!entry) {
            return false;
          }
          return Boolean(entry.tempCommandsExpanded) || this.hasMeaningfulTemporaryCommands(entry);
        },
        temporaryCommandsButtonLabel(entry) {
          return this.showTemporaryCommands(entry)
            ? "Add Another Temporary Command"
            : "Add Temporary Command";
        },
        currentStepChecked(editor, field) {
          const step = this.currentStep(editor);
          return Boolean(step && step[field]);
        },
        updateCurrentStepToggle(editor, field, checked) {
          const step = this.currentStep(editor);
          step[field] = Boolean(checked);
        },
        resetCurrentModule(editor) {
          this.ensureEditor(editor);
          const currentType = this.currentStepType(editor);
          const nextStep = moduleSystem.defaultStep(currentType);
          editor.steps.splice(editor.editing_index, 1, nextStep);
        },
        currentStepMenuItems(editor) {
          const step = this.currentStep(editor);
          return this.formatMenuItems(step.items || []);
        },
        currentStepInlineButtons(editor) {
          const step = this.currentStep(editor);
          return this.formatInlineButtons(step.buttons || []);
        },
        currentStepButtons(editor) {
          const step = this.currentStep(editor);
          return helpers.normalizeInlineButtons(step.buttons || []);
        },
        ensureStepButtons(editor) {
          const step = this.currentStep(editor);
          step.buttons = helpers.normalizeInlineButtons(step.buttons || []);
          return step.buttons;
        },
        normalizeInlineButtonDraft(rawDraft) {
          const draft = rawDraft && typeof rawDraft === "object" ? rawDraft : {};
          const action = String(draft.action || "").trim().toLowerCase();
          const rowRaw = Number.parseInt(draft.row, 10);
          return {
            text: String(draft.text || ""),
            action: action === "url" ? "url" : "callback_data",
            value: String(draft.value || ""),
            actual_value: String(draft.actual_value || ""),
            row: Number.isInteger(rowRaw) && rowRaw > 0 ? rowRaw : 1,
            edit_index: Number.isInteger(draft.edit_index) ? draft.edit_index : null,
          };
        },
        inlineButtonDraft(editor) {
          const step = this.currentStep(editor);
          return this.normalizeInlineButtonDraft(step._inline_button_draft);
        },
        ensureInlineButtonDraft(editor) {
          const step = this.currentStep(editor);
          step._inline_button_draft = this.normalizeInlineButtonDraft(step._inline_button_draft);
          return step._inline_button_draft;
        },
        normalizeContextKeyDraft(rawDraft) {
          const draft = rawDraft && typeof rawDraft === "object" ? rawDraft : {};
          return {
            run_if_context_keys: String(draft.run_if_context_keys || ""),
            skip_if_context_keys: String(draft.skip_if_context_keys || ""),
          };
        },
        ensureContextKeyDraft(editor) {
          const step = this.currentStep(editor);
          step._context_key_draft = this.normalizeContextKeyDraft(step._context_key_draft);
          return step._context_key_draft;
        },
        contextKeyDraft(editor, field) {
          const step = this.currentStep(editor);
          const draft = this.normalizeContextKeyDraft(step._context_key_draft);
          if (field !== "run_if_context_keys" && field !== "skip_if_context_keys") {
            return "";
          }
          return String(draft[field] || "");
        },
        updateContextKeyDraftField(editor, field, value) {
          if (field !== "run_if_context_keys" && field !== "skip_if_context_keys") {
            return;
          }
          const draft = this.ensureContextKeyDraft(editor);
          draft[field] = String(value || "");
        },
        appendContextKey(editor, field) {
          if (field !== "run_if_context_keys" && field !== "skip_if_context_keys") {
            return;
          }
          const draft = this.ensureContextKeyDraft(editor);
          const selectedKey = String(draft[field] || "").trim();
          if (!selectedKey) {
            return;
          }
          const step = this.currentStep(editor);
          const existing = helpers.splitLines(step[field] || "");
          if (existing.includes(selectedKey)) {
            draft[field] = "";
            return;
          }
          existing.push(selectedKey);
          step[field] = existing.join("\n");
          draft[field] = "";
        },
	        updateInlineButtonDraftField(editor, field, value) {
	          const draft = this.ensureInlineButtonDraft(editor);
          if (field === "action") {
            const normalized = String(value || "").trim().toLowerCase();
            draft.action = normalized === "url" ? "url" : "callback_data";
            return;
          }
          if (field === "edit_index") {
            draft.edit_index = Number.isInteger(value) ? value : null;
            return;
          }
          if (field === "row") {
            const parsed = Number.parseInt(value, 10);
            draft.row = Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
            return;
          }
	          if (field === "text" || field === "value" || field === "actual_value") {
	            draft[field] = String(value || "");
	          }
	        },
	        applyInlineButtonDraftCallbackSuggestion(editor, value) {
	          const draft = this.ensureInlineButtonDraft(editor);
	          draft.action = "callback_data";
	          draft.value = String(value || "");
	        },
        inlineButtonLabel(button, index) {
          const text = String(button && button.text ? button.text : "").trim();
          const hasUrl = Boolean(button && button.url);
          const action = hasUrl ? "url" : "callback_data";
          const value = String(
            hasUrl ? button.url : button && button.callback_data ? button.callback_data : ""
          ).trim();
	          const rowRaw = Number.parseInt(button && button.row, 10);
	          const row = Number.isInteger(rowRaw) && rowRaw > 0 ? rowRaw : index + 1;
	          const trimmedValue = value.length > 40 ? `${value.slice(0, 40)}...` : value;
	          const actualValue = String(button && button.actual_value ? button.actual_value : "").trim();
	          const actualSuffix = actualValue ? ` | actual: ${actualValue.length > 30 ? `${actualValue.slice(0, 30)}...` : actualValue}` : "";
	          return `#${index + 1} Row ${row} ${text || "(empty text)"} | ${action}: ${trimmedValue || "(empty value)"}${actualSuffix}`;
	        },
	        saveInlineButton(editor) {
	          const buttons = this.ensureStepButtons(editor);
	          const draft = this.ensureInlineButtonDraft(editor);
	          const text = String(draft.text || "").trim();
	          const value = String(draft.value || "").trim();
	          const actualValue = String(draft.actual_value || "").trim();
	          const action = draft.action === "url" ? "url" : "callback_data";
	          const row = Number.isInteger(draft.row) && draft.row > 0 ? draft.row : 1;
	          if (!text || !value) {
	            return;
	          }
	          const nextButton = action === "url"
	            ? { text, url: value, row }
	            : actualValue
	              ? { text, callback_data: value, actual_value: actualValue, row }
	              : { text, callback_data: value, row };
          if (Number.isInteger(draft.edit_index) && draft.edit_index >= 0 && draft.edit_index < buttons.length) {
            buttons.splice(draft.edit_index, 1, nextButton);
          } else {
            buttons.push(nextButton);
          }
          this.cancelInlineButtonEdit(editor);
        },
        cancelInlineButtonEdit(editor) {
          const draft = this.ensureInlineButtonDraft(editor);
	          draft.text = "";
	          draft.action = "callback_data";
	          draft.value = "";
	          draft.actual_value = "";
	          draft.row = 1;
	          draft.edit_index = null;
	        },
        editInlineButton(editor, index) {
          const buttons = this.ensureStepButtons(editor);
          if (index < 0 || index >= buttons.length) {
            return;
          }
          const button = buttons[index] || {};
          const draft = this.ensureInlineButtonDraft(editor);
          draft.text = String(button.text || "");
	          if (button.url) {
	            draft.action = "url";
	            draft.value = String(button.url || "");
	            draft.actual_value = "";
	          } else {
	            draft.action = "callback_data";
	            draft.value = String(button.callback_data || "");
	            draft.actual_value = String(button.actual_value || "");
	          }
          const rowRaw = Number.parseInt(button.row, 10);
          draft.row = Number.isInteger(rowRaw) && rowRaw > 0 ? rowRaw : index + 1;
          draft.edit_index = index;
        },
        moveInlineButtonUp(editor, index) {
          const buttons = this.ensureStepButtons(editor);
          if (index <= 0 || index >= buttons.length) {
            return;
          }
          [buttons[index - 1], buttons[index]] = [buttons[index], buttons[index - 1]];
          const draft = this.ensureInlineButtonDraft(editor);
          if (draft.edit_index === index) {
            draft.edit_index = index - 1;
          } else if (draft.edit_index === index - 1) {
            draft.edit_index = index;
          }
        },
        moveInlineButtonDown(editor, index) {
          const buttons = this.ensureStepButtons(editor);
          if (index < 0 || index >= buttons.length - 1) {
            return;
          }
          [buttons[index + 1], buttons[index]] = [buttons[index], buttons[index + 1]];
          const draft = this.ensureInlineButtonDraft(editor);
          if (draft.edit_index === index) {
            draft.edit_index = index + 1;
          } else if (draft.edit_index === index + 1) {
            draft.edit_index = index;
          }
        },
        removeInlineButton(editor, index) {
          const buttons = this.ensureStepButtons(editor);
          if (index < 0 || index >= buttons.length) {
            return;
          }
          buttons.splice(index, 1);
          const draft = this.ensureInlineButtonDraft(editor);
          if (draft.edit_index == null) {
            return;
          }
          if (draft.edit_index === index) {
            this.cancelInlineButtonEdit(editor);
            return;
          }
          if (draft.edit_index > index) {
            draft.edit_index -= 1;
          }
        },
        updateCurrentStepField(editor, field, value) {
          const step = this.currentStep(editor);
          if (field === "module_type") {
            step.module_type = moduleSystem.normalizeType(value);
            return;
          }
          step[field] = String(value || "");
        },
        applyTemplateSnippet(editor, field, before, after, event) {
          const step = this.currentStep(editor);
          const current = step && field in step && step[field] != null ? String(step[field]) : "";
          const toolbarButton = event && event.currentTarget ? event.currentTarget : null;
          const container = toolbarButton ? toolbarButton.closest(".template-editor") : null;
          const textarea = container ? container.querySelector("textarea") : null;
          let nextValue = `${current}${before}${after}`;
          let selectionStart = nextValue.length;
          let selectionEnd = nextValue.length;
          if (textarea && typeof textarea.selectionStart === "number" && typeof textarea.selectionEnd === "number") {
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const selectedText = current.slice(start, end);
            nextValue = `${current.slice(0, start)}${before}${selectedText}${after}${current.slice(end)}`;
            selectionStart = start + before.length;
            selectionEnd = selectionStart + selectedText.length;
          }
          step[field] = nextValue;
          if (textarea && typeof textarea.focus === "function" && typeof this.$nextTick === "function") {
            this.$nextTick(() => {
              textarea.focus();
              if (typeof textarea.setSelectionRange === "function") {
                textarea.setSelectionRange(selectionStart, selectionEnd);
              }
            });
          }
        },
        insertTemplateToken(editor, field, token, event) {
          this.applyTemplateSnippet(editor, field, String(token || ""), "", event);
        },
        updateCurrentStepMenuItems(editor, raw) {
          const step = this.currentStep(editor);
          step.items = helpers.parseMenuItems(raw);
        },
        updateCurrentStepInlineButtons(editor, raw) {
          const step = this.currentStep(editor);
          step.buttons = helpers.parseInlineButtons(raw);
        },
        moduleRowClass(editor, index) {
          return this.isEditing(editor, index) ? "module-list-row is-editing" : "module-list-row";
        },
        isEditing(editor, index) {
          return Boolean(editor.visible) && editor.editing_index === index;
        },
        moduleRowLabel(step, index, editing) {
          const baseLabel = moduleSystem.rowLabel(step, index);
          return editing ? `Editing - ${baseLabel}` : baseLabel;
        },
        editModule(editor, index) {
          if (index < 0 || index >= editor.steps.length) {
            return;
          }
          editor.visible = true;
          editor.editing_index = index;
        },
        addModule(editor) {
          editor.steps.push(moduleSystem.defaultStep(editor.add_type));
          editor.visible = true;
          editor.editing_index = editor.steps.length - 1;
        },
        moveModuleUp(editor, index) {
          if (index <= 0 || index >= editor.steps.length) {
            return;
          }
          [editor.steps[index - 1], editor.steps[index]] = [editor.steps[index], editor.steps[index - 1]];
          if (editor.editing_index === index) {
            editor.editing_index = index - 1;
          } else if (editor.editing_index === index - 1) {
            editor.editing_index = index;
          }
        },
        moveModuleDown(editor, index) {
          if (index < 0 || index >= editor.steps.length - 1) {
            return;
          }
          [editor.steps[index + 1], editor.steps[index]] = [editor.steps[index], editor.steps[index + 1]];
          if (editor.editing_index === index) {
            editor.editing_index = index + 1;
          } else if (editor.editing_index === index + 1) {
            editor.editing_index = index;
          }
        },
        removeModule(editor, index) {
          if (index < 0 || index >= editor.steps.length) {
            return;
          }
          if (editor.steps.length <= 1) {
            editor.steps = [moduleSystem.defaultStep(editor.add_type)];
            editor.editing_index = editor.visible ? 0 : null;
            return;
          }
          editor.steps.splice(index, 1);
          if (editor.editing_index == null) {
            return;
          }
          if (editor.editing_index > index) {
            editor.editing_index -= 1;
            return;
          }
          if (editor.editing_index >= editor.steps.length) {
            editor.editing_index = editor.steps.length - 1;
          }
        },
        addCommand() {
          this.commandEntries.push(createCommandEntry({}));
        },
        addModuleWithTempCommandExample() {
          const example = createModuleWithTempCommandExample();
          const commandKey = normalizeCommandKey(example.commandEntry.command);
          const callbackKey = String(example.callbackEntry.callback_key || "").trim();
          const existingCommandIndex = this.commandEntries.findIndex(
            (entry) => normalizeCommandKey(entry && entry.command ? entry.command : "") === commandKey
          );
          const existingCallbackIndex = this.callbackEntries.findIndex(
            (entry) => String(entry && entry.callback_key ? entry.callback_key : "").trim() === callbackKey
          );
          const hasConflict = existingCommandIndex >= 0 || existingCallbackIndex >= 0;
          if (hasConflict && typeof window !== "undefined" && typeof window.confirm === "function") {
            const confirmed = window.confirm(
              "Replace the existing /temp_menu command or temp_menu callback with the temporary command example scaffold?"
            );
            if (!confirmed) {
              return;
            }
          }
          example.commandEntry.editor.visible = true;
          example.commandEntry.editor.editing_index = 0;
          example.callbackEntry.editor.visible = true;
          example.callbackEntry.editor.editing_index = 0;
          if (existingCommandIndex >= 0) {
            this.commandEntries.splice(existingCommandIndex, 1, example.commandEntry);
          } else {
            this.commandEntries.push(example.commandEntry);
          }
          if (existingCallbackIndex >= 0) {
            this.callbackEntries.splice(existingCallbackIndex, 1, example.callbackEntry);
          } else {
            this.callbackEntries.push(example.callbackEntry);
          }
        },
        removeCommand(index) {
          if (index < 0 || index >= this.commandEntries.length) {
            return;
          }
          this.commandEntries.splice(index, 1);
        },
        addCallback() {
          const suggestedKey = this.callbackOptions.length > 0 ? this.callbackOptions[0] : "";
          this.callbackEntries.push(createCallbackEntry({ callback_key: suggestedKey }));
        },
        removeCallback(index) {
          if (index < 0 || index >= this.callbackEntries.length) {
            return;
          }
          this.callbackEntries.splice(index, 1);
        },
        addTemporaryCommand(entry) {
          if (!entry) {
            return;
          }
          if (!Array.isArray(entry.temporaryCommandEntries)) {
            entry.temporaryCommandEntries = [];
          }
          entry.tempCommandsExpanded = true;
          const hasMeaningfulEntry = this.hasMeaningfulTemporaryCommands(entry);
          const hasBlankDraft = entry.temporaryCommandEntries.some(
            (tempEntry) => normalizeCommandKey(tempEntry && tempEntry.command ? tempEntry.command : "").length === 0
          );
          if (!hasMeaningfulEntry && hasBlankDraft) {
            return;
          }
          entry.temporaryCommandEntries.push(createCommandEntry({}));
        },
        removeTemporaryCommand(entry, index) {
          if (!entry || !Array.isArray(entry.temporaryCommandEntries)) {
            return;
          }
          if (index < 0 || index >= entry.temporaryCommandEntries.length) {
            return;
          }
          entry.temporaryCommandEntries.splice(index, 1);
          if (!entry.temporaryCommandEntries.length) {
            entry.tempCommandsExpanded = false;
          }
        },
        clearTemporaryCommands(entry) {
          if (!entry || !Array.isArray(entry.temporaryCommandEntries)) {
            return;
          }
          entry.temporaryCommandEntries = [];
          entry.tempCommandsExpanded = false;
        },
        applyCallbackSuggestion(entry, value) {
          if (!entry) {
            return;
          }
          entry.callback_key = String(value || "");
        },
        resetAllToStartDefault() {
          if (typeof window !== "undefined" && typeof window.confirm === "function") {
            const confirmed = window.confirm(
              "Reset everything and keep only /start with default setup?"
            );
            if (!confirmed) {
              return;
            }
          }
          this.startDescription = "";
          this.startEditor = createEditor(defaultStartValues());
          this.commandEntries = [];
          this.callbackEntries = [];
          const includeStartCheckbox = typeof document !== "undefined"
            ? document.querySelector('input[name="include_start_command"]')
            : null;
          if (includeStartCheckbox) {
            includeStartCheckbox.checked = true;
          }
          const commandMenuCheckbox = typeof document !== "undefined"
            ? document.querySelector('input[name="command_menu_enabled"]')
            : null;
          if (commandMenuCheckbox) {
            commandMenuCheckbox.checked = true;
          }
        },
        commandPanelTitle(command) {
          const value = String(command || "").trim();
          return value ? `${value} Module Setup` : "New Command Module Setup";
        },
        callbackPanelTitle(callbackKey) {
          const value = String(callbackKey || "").trim();
          return value ? `${value} Callback Module Setup` : "New Callback Module Setup";
        },
        formatMenuItems(items) {
          if (!Array.isArray(items)) {
            return "";
          }
          return items.map((item) => String(item || "").trim()).filter((item) => item.length > 0).join("\n");
        },
        formatInlineButtons(buttons) {
          return helpers.formatInlineButtons(buttons || []);
        },
        formatChainSteps(steps) {
          if (!Array.isArray(steps)) {
            return "";
          }
          const lines = [];
          for (const step of steps) {
            const normalized = moduleSystem.parsePrimary(
              step && step.module_type ? step.module_type : moduleSystem.defaultType(),
              step || {}
            );
            const payload = JSON.stringify(normalized);
            if (payload) {
              lines.push(payload);
            }
          }
          return lines.join("\n");
        },
        serializeCommandEntry(entry) {
          const source = entry && typeof entry === "object" ? entry : {};
          const editor = source.editor && typeof source.editor === "object" ? source.editor : createEditor({});
          const primary = this.primaryStep(editor);
          return {
            command: normalizeCommandKey(source.command || ""),
            description: String(source.description || ""),
            module_type: primary.module_type,
            text_template: primary.text_template,
            hide_caption: primary.hide_caption ? "1" : "",
            parse_mode: primary.parse_mode,
            menu_title: primary.title,
            menu_items: this.formatMenuItems(primary.items),
            inline_buttons: this.formatInlineButtons(primary.buttons),
            inline_run_if_context_keys: primary.run_if_context_keys,
            inline_skip_if_context_keys: primary.skip_if_context_keys,
            inline_save_callback_data_to_key: primary.save_callback_data_to_key,
            callback_target_key: primary.target_callback_key,
            command_target_key: primary.target_command_key,
            photo_url: primary.photo_url,
            contact_button_text: primary.button_text,
            mini_app_button_text: primary.button_text,
            contact_success_text: primary.success_text_template,
            contact_invalid_text: primary.invalid_text_template,
            require_live_location: primary.require_live_location ? "1" : "",
            track_breadcrumb: primary.track_breadcrumb ? "1" : "",
            store_history_by_day: primary.store_history_by_day ? "1" : "",
            breadcrumb_interval_minutes: primary.breadcrumb_interval_minutes,
            breadcrumb_min_distance_meters: primary.breadcrumb_min_distance_meters,
            checkout_empty_text: primary.empty_text_template,
            payment_empty_text: primary.empty_text_template,
            checkout_pay_button_text: primary.pay_button_text,
            checkout_pay_callback_data: primary.pay_callback_data,
            payment_return_url: primary.return_url,
            mini_app_url: primary.return_url,
            payment_title_template: primary.title_template,
            payment_description_template: primary.description_template,
            payment_open_button_text: primary.open_button_text,
            payment_web_button_text: primary.web_button_text,
            payment_currency: primary.currency,
            payment_limit: primary.payment_limit,
            payment_deep_link_prefix: primary.deep_link_prefix,
            payment_merchant_ref_prefix: primary.merchant_ref_prefix,
            cart_product_name: primary.product_name,
            cart_product_key: primary.product_key,
            cart_price: primary.price,
            cart_qty: primary.quantity,
            cart_min_qty: primary.min_qty,
            cart_max_qty: primary.max_qty,
            chain_steps: this.formatChainSteps(editor.steps.slice(1)),
            restore_original_menu: source.restore_original_menu ? "1" : "",
          };
        },
        serializeTemporaryCommands(entries) {
          if (!Array.isArray(entries) || !entries.length) {
            return "";
          }
          const payload = entries
            .map((entry) => this.serializeCommandEntry(entry))
            .filter((entry) => Boolean(entry.command));
          return payload.length ? JSON.stringify(payload) : "";
        },
      },
      template: appTemplate(),
    };
  }

  function mount(rootSelector, stateSelector) {
    // Mount the config editor against server-rendered HTML and embedded JSON state.
    const root = document.querySelector(rootSelector);
    const stateNode = document.querySelector(stateSelector);
    if (!root || !stateNode || !global.Vue) {
      return;
    }

    let state = {};
    try {
      state = JSON.parse(stateNode.textContent || "{}");
    } catch (_error) {
      state = {};
    }

    global.Vue.createApp(buildVueOptions(state)).mount(root);
  }

  global.EtraxConfigVue = {
    mount,
  };
})(window);

