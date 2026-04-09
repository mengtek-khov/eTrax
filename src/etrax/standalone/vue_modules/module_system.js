(function (global) {
  "use strict";

  const moduleOrder = [];
  const modulesByType = {};

  function splitLines(raw) {
    return String(raw || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  }

  function parseMenuItems(raw) {
    return splitLines(raw);
  }

  function normalizeInlineButtons(rawButtons) {
    if (!Array.isArray(rawButtons)) {
      return [];
    }

    const normalized = [];
    for (let rawIndex = 0; rawIndex < rawButtons.length; rawIndex += 1) {
      const rawButton = rawButtons[rawIndex];
      const candidates = Array.isArray(rawButton) ? rawButton : [rawButton];
      const fallbackRow = Array.isArray(rawButton) ? rawIndex + 1 : normalized.length + 1;
      for (const candidate of candidates) {
        if (!candidate || typeof candidate !== "object") {
          continue;
        }
        const text = String(candidate.text || "").trim();
        const url = String(candidate.url || "").trim();
        const callbackData = String(candidate.callback_data || "").trim();
        const actualValue = String(candidate.actual_value || "").trim();
        const rowRaw = Number.parseInt(candidate.row, 10);
        const row = Number.isInteger(rowRaw) && rowRaw > 0 ? rowRaw : fallbackRow;
        if (!text) {
          continue;
        }
        if (Boolean(url) === Boolean(callbackData)) {
          continue;
        }
        if (url) {
          normalized.push({ text, url, row });
          continue;
        }
        const button = { text, callback_data: callbackData, row };
        if (actualValue) {
          button.actual_value = actualValue;
        }
        normalized.push(button);
      }
    }
    return normalized;
  }

  function parseOptionalInlineButtonMetadata(rawParts, fallbackRow) {
    let row = fallbackRow;
    let actualValue = "";
    const extras = Array.isArray(rawParts) ? rawParts.map((part) => String(part || "").trim()) : [];
    if (extras.length === 0) {
      return { row, actualValue };
    }

    const parseRow = (value) => {
      const match = /^row:(\d+)$/i.exec(value) || /^(\d+)$/.exec(value);
      return match ? Number.parseInt(match[1], 10) : null;
    };

    const firstRow = parseRow(extras[0]);
    if (firstRow && firstRow > 0) {
      row = firstRow;
      actualValue = extras.slice(1).join("|").trim();
      return { row, actualValue };
    }

    const lastRow = parseRow(extras[extras.length - 1]);
    if (extras.length > 1 && lastRow && lastRow > 0) {
      row = lastRow;
      actualValue = extras.slice(0, -1).join("|").trim();
      return { row, actualValue };
    }

    actualValue = extras.join("|").trim();
    return { row, actualValue };
  }

  function parseInlineButtons(raw) {
    const buttons = [];
    const lines = splitLines(raw);
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      const parts = line.split("|").map((part) => part.trim());
      if (parts.length < 3) {
        continue;
      }
      const text = parts[0];
      const action = parts[1].toLowerCase().replace(/\s+/g, "_");
      const value = String(parts[2] || "").trim();
      const { row, actualValue } = parseOptionalInlineButtonMetadata(parts.slice(3), index + 1);
      if (!text || !value) {
        continue;
      }
      if (action === "url") {
        buttons.push({ text, url: value, row: row > 0 ? row : index + 1 });
        continue;
      }
      if (action === "callback_data") {
        const button = { text, callback_data: value, row: row > 0 ? row : index + 1 };
        if (actualValue) {
          button.actual_value = actualValue;
        }
        buttons.push(button);
      }
    }
    return buttons;
  }

  function formatInlineButtons(buttons) {
    const normalized = normalizeInlineButtons(buttons);
    const lines = [];
    for (const button of normalized) {
      const text = String(button.text || "").trim();
      if (!text) {
        continue;
      }
      const rowRaw = Number.parseInt(button.row, 10);
      const row = Number.isInteger(rowRaw) && rowRaw > 0 ? rowRaw : lines.length + 1;
      if (button.url) {
        lines.push(`${text} | url | ${String(button.url)} | ${row}`);
        continue;
      }
      if (button.callback_data) {
        const actualValue = String(button.actual_value || "").trim();
        lines.push(
          actualValue
            ? `${text} | callback_data | ${String(button.callback_data)} | ${row} | ${actualValue}`
            : `${text} | callback_data | ${String(button.callback_data)} | ${row}`
        );
      }
    }
    return lines.join("\n");
  }

  const helpers = {
    splitLines,
    parseMenuItems,
    parseInlineButtons,
    formatInlineButtons,
    normalizeInlineButtons,
  };

  function registeredType(raw) {
    return String(raw || "").trim().toLowerCase();
  }

  function defaultType() {
    if (moduleOrder.length > 0) {
      return moduleOrder[0];
    }
    return "send_message";
  }

  function normalizeType(raw) {
    const normalized = registeredType(raw);
    if (normalized && modulesByType[normalized]) {
      return normalized;
    }
    return defaultType();
  }

  function normalizeCheckboxValue(raw) {
    const normalized = String(raw == null ? "" : raw).trim().toLowerCase();
    return raw === true || normalized === "true" || normalized === "1" || normalized === "yes" || normalized === "on";
  }

  function normalizeStep(rawStep, fallbackType) {
    const source = rawStep && typeof rawStep === "object" ? rawStep : {};
    const hideCaption = normalizeCheckboxValue(source.hide_caption);
    const requireLiveLocation = normalizeCheckboxValue(source.require_live_location);
    const findClosestSavedLocation = normalizeCheckboxValue(source.find_closest_saved_location);
    const matchClosestSavedLocation = normalizeCheckboxValue(source.match_closest_saved_location);
    const trackBreadcrumb = requireLiveLocation && normalizeCheckboxValue(source.track_breadcrumb);
    const storeHistoryByDay = normalizeCheckboxValue(source.store_history_by_day);
    return {
      module_type: normalizeType(source.module_type || fallbackType),
      text_template: source.text_template == null ? "" : String(source.text_template),
      parse_mode: source.parse_mode == null ? "" : String(source.parse_mode),
      hide_caption: hideCaption,
      require_live_location: requireLiveLocation,
      find_closest_saved_location: findClosestSavedLocation,
      match_closest_saved_location: matchClosestSavedLocation,
      track_breadcrumb: trackBreadcrumb,
      store_history_by_day: storeHistoryByDay,
      title: source.title == null ? "Main Menu" : String(source.title),
      items: parseMenuItems(Array.isArray(source.items) ? source.items.join("\n") : source.items || ""),
      buttons: normalizeInlineButtons(source.buttons || []),
      run_if_context_keys: Array.isArray(source.run_if_context_keys)
        ? source.run_if_context_keys.join("\n")
        : source.run_if_context_keys == null
          ? ""
          : String(source.run_if_context_keys),
      skip_if_context_keys: Array.isArray(source.skip_if_context_keys)
        ? source.skip_if_context_keys.join("\n")
        : source.skip_if_context_keys == null
          ? ""
          : String(source.skip_if_context_keys),
      save_callback_data_to_key: source.save_callback_data_to_key == null ? "" : String(source.save_callback_data_to_key),
      target_callback_key: source.target_callback_key == null ? "" : String(source.target_callback_key),
      target_command_key: source.target_command_key == null ? "" : String(source.target_command_key),
      photo_url: source.photo_url == null ? "" : String(source.photo_url),
      button_text: source.button_text == null ? "" : String(source.button_text),
      success_text_template: source.success_text_template == null ? "" : String(source.success_text_template),
      closest_location_group_text_template:
        source.closest_location_group_text_template == null ? "" : String(source.closest_location_group_text_template),
      closest_location_group_send_timing:
        source.closest_location_group_send_timing == null ? "end" : String(source.closest_location_group_send_timing),
      closest_location_group_send_after_step:
        source.closest_location_group_send_after_step == null ? "" : String(source.closest_location_group_send_after_step),
      invalid_text_template: source.invalid_text_template == null ? "" : String(source.invalid_text_template),
      closest_location_tolerance_meters:
        source.closest_location_tolerance_meters == null ? "" : String(source.closest_location_tolerance_meters),
      breadcrumb_interval_minutes: source.breadcrumb_interval_minutes == null ? "" : String(source.breadcrumb_interval_minutes),
      breadcrumb_min_distance_meters:
        source.breadcrumb_min_distance_meters == null ? "" : String(source.breadcrumb_min_distance_meters),
      breadcrumb_started_text_template:
        source.breadcrumb_started_text_template == null ? "" : String(source.breadcrumb_started_text_template),
      breadcrumb_interrupted_text_template:
        source.breadcrumb_interrupted_text_template == null ? "" : String(source.breadcrumb_interrupted_text_template),
      breadcrumb_resumed_text_template:
        source.breadcrumb_resumed_text_template == null ? "" : String(source.breadcrumb_resumed_text_template),
      breadcrumb_ended_text_template:
        source.breadcrumb_ended_text_template == null ? "" : String(source.breadcrumb_ended_text_template),
      max_link_points: source.max_link_points == null ? "" : String(source.max_link_points),
      empty_text_template: source.empty_text_template == null ? "" : String(source.empty_text_template),
      pay_button_text: source.pay_button_text == null ? "" : String(source.pay_button_text),
      pay_callback_data: source.pay_callback_data == null ? "" : String(source.pay_callback_data),
      return_url: source.return_url == null ? "" : String(source.return_url),
      title_template: source.title_template == null ? "" : String(source.title_template),
      description_template: source.description_template == null ? "" : String(source.description_template),
      open_button_text: source.open_button_text == null ? "" : String(source.open_button_text),
      web_button_text: source.web_button_text == null ? "" : String(source.web_button_text),
      currency: source.currency == null ? "" : String(source.currency),
      payment_limit: source.payment_limit == null ? "5" : String(source.payment_limit),
      deep_link_prefix: source.deep_link_prefix == null ? "" : String(source.deep_link_prefix),
      merchant_ref_prefix: source.merchant_ref_prefix == null ? "" : String(source.merchant_ref_prefix),
      product_name: source.product_name == null ? "" : String(source.product_name),
      product_key: source.product_key == null ? "" : String(source.product_key),
      price: source.price == null ? "" : String(source.price),
      quantity: source.quantity == null ? "1" : String(source.quantity),
      min_qty: source.min_qty == null ? "0" : String(source.min_qty),
      max_qty: source.max_qty == null ? "99" : String(source.max_qty),
    };
  }

  function register(definition) {
    if (!definition || typeof definition !== "object") {
      return;
    }

    const type = registeredType(definition.type);
    if (!type) {
      return;
    }

    const normalized = {
      type,
      label: definition.label ? String(definition.label) : type,
      defaultStep:
        typeof definition.defaultStep === "function"
          ? definition.defaultStep
          : function defaultStep() {
              return normalizeStep({ module_type: type }, type);
            },
      parsePrimary:
        typeof definition.parsePrimary === "function"
          ? definition.parsePrimary
          : function parsePrimary(source) {
              return normalizeStep(source, type);
            },
      parseChain:
        typeof definition.parseChain === "function"
          ? definition.parseChain
          : function parseChain(parts) {
              const first = registeredType(parts && parts.length ? parts[0] : "");
              if (first !== type) {
                return null;
              }
              return normalizeStep({ module_type: type }, type);
            },
      formatChain:
        typeof definition.formatChain === "function"
          ? definition.formatChain
          : function formatChain(step) {
              return `${type} | ${String(step.text_template || "")}`;
            },
      rowLabel:
        typeof definition.rowLabel === "function"
          ? definition.rowLabel
          : function rowLabel(step, index) {
              const stepNo = index + 1;
              return `#${stepNo} ${type} - ${String(step.text_template || "").trim() || "(empty)"}`;
            },
      editorTemplate:
        typeof definition.editorTemplate === "function"
          ? definition.editorTemplate
          : function editorTemplate() {
              return "";
            },
    };

    if (!modulesByType[type]) {
      moduleOrder.push(type);
    }
    modulesByType[type] = normalized;
  }

  function resolve(type) {
    const normalized = normalizeType(type);
    return modulesByType[normalized] || null;
  }

  function defaultStep(type) {
    const definition = resolve(type);
    if (!definition) {
      return normalizeStep({ module_type: defaultType() }, defaultType());
    }
    return normalizeStep(definition.defaultStep(helpers), definition.type);
  }

  function parsePrimary(type, source) {
    const definition = resolve(type);
    if (!definition) {
      return defaultStep(type);
    }
    return normalizeStep(definition.parsePrimary(source || {}, helpers), definition.type);
  }

  function parseChain(parts) {
    const targetType = normalizeType(parts && parts.length ? parts[0] : "");
    const definition = resolve(targetType);
    if (!definition) {
      return null;
    }
    const parsed = definition.parseChain(parts || [], helpers);
    if (!parsed) {
      return null;
    }
    return normalizeStep(parsed, definition.type);
  }

  function formatChainStep(step) {
    const normalizedStep = normalizeStep(step, step && step.module_type ? step.module_type : defaultType());
    const definition = resolve(normalizedStep.module_type);
    if (!definition) {
      return "";
    }
    return String(definition.formatChain(normalizedStep, helpers) || "").trim();
  }

  function rowLabel(step, index) {
    const normalizedStep = normalizeStep(step, step && step.module_type ? step.module_type : defaultType());
    const definition = resolve(normalizedStep.module_type);
    if (!definition) {
      const stepNo = index + 1;
      return `#${stepNo} ${normalizedStep.module_type}`;
    }
    return String(definition.rowLabel(normalizedStep, index, helpers) || `#${index + 1} ${definition.type}`);
  }

  function editorTemplate(type, contextExpression, idPrefix) {
    const definition = resolve(type);
    if (!definition) {
      return "";
    }
    return String(
      definition.editorTemplate(
        {
          ctx: String(contextExpression || "editor"),
          idPrefix: String(idPrefix || ""),
        },
        helpers
      ) || ""
    );
  }

  function optionList() {
    return moduleOrder.map((type) => ({ type, label: modulesByType[type].label }));
  }

  global.EtraxModuleSystem = {
    helpers,
    register,
    resolve,
    normalizeType,
    defaultType,
    defaultStep,
    parsePrimary,
    parseChain,
    formatChainStep,
    rowLabel,
    editorTemplate,
    optionList,
  };
})(window);

