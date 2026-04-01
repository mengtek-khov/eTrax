(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "payway_payment",
    label: "payway_payment",
    defaultStep() {
      return {
        module_type: "payway_payment",
        text_template: "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
        empty_text_template: "Your cart is empty.",
        parse_mode: "HTML",
        return_url: "",
        title_template: "Cart payment for {bot_name}",
        description_template: "{cart_lines}",
        open_button_text: "Open ABA Mobile",
        web_button_text: "Open Web Checkout",
        currency: "USD",
        payment_limit: "5",
        deep_link_prefix: "abamobilebank://",
        merchant_ref_prefix: "cart",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
        pay_button_text: "",
        pay_callback_data: "",
        product_name: "",
        product_key: "",
        price: "",
        quantity: "1",
        min_qty: "0",
        max_qty: "99",
      };
    },
    parsePrimary(source) {
      return {
        module_type: "payway_payment",
        text_template: source.text_template ? String(source.text_template) : "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
        empty_text_template: source.checkout_empty_text ? String(source.checkout_empty_text) : (source.empty_text_template ? String(source.empty_text_template) : "Your cart is empty."),
        parse_mode: source.parse_mode ? String(source.parse_mode) : "HTML",
        return_url: source.payment_return_url ? String(source.payment_return_url) : (source.return_url ? String(source.return_url) : ""),
        title_template: source.payment_title_template ? String(source.payment_title_template) : (source.title_template ? String(source.title_template) : "Cart payment for {bot_name}"),
        description_template: source.payment_description_template ? String(source.payment_description_template) : (source.description_template ? String(source.description_template) : "{cart_lines}"),
        open_button_text: source.payment_open_button_text ? String(source.payment_open_button_text) : (source.open_button_text ? String(source.open_button_text) : "Open ABA Mobile"),
        web_button_text: source.payment_web_button_text ? String(source.payment_web_button_text) : (source.web_button_text ? String(source.web_button_text) : "Open Web Checkout"),
        currency: source.payment_currency ? String(source.payment_currency) : (source.currency ? String(source.currency) : "USD"),
        payment_limit: source.payment_limit ? String(source.payment_limit) : "5",
        deep_link_prefix: source.deep_link_prefix ? String(source.deep_link_prefix) : "abamobilebank://",
        merchant_ref_prefix: source.merchant_ref_prefix ? String(source.merchant_ref_prefix) : "cart",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
        pay_button_text: "",
        pay_callback_data: "",
        product_name: "",
        product_key: "",
        price: "",
        quantity: "1",
        min_qty: "0",
        max_qty: "99",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "payway_payment") {
        return null;
      }
      return {
        module_type: "payway_payment",
        text_template: parts[1] || "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
        empty_text_template: parts[2] || "Your cart is empty.",
        return_url: parts[3] || "",
        title_template: parts[4] || "Cart payment for {bot_name}",
        description_template: parts[5] || "{cart_lines}",
        open_button_text: parts[6] || "Open ABA Mobile",
        web_button_text: parts[7] || "Open Web Checkout",
        currency: parts[8] || "USD",
        payment_limit: parts[9] || "5",
        parse_mode: parts[10] || "HTML",
        deep_link_prefix: parts[11] || "abamobilebank://",
        merchant_ref_prefix: parts[12] || "cart",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
        pay_button_text: "",
        pay_callback_data: "",
        product_name: "",
        product_key: "",
        price: "",
        quantity: "1",
        min_qty: "0",
        max_qty: "99",
      };
    },
    formatChain(step) {
      return [
        "payway_payment",
        String(step.text_template || "").trim(),
        String(step.empty_text_template || "").trim(),
        String(step.return_url || "").trim(),
        String(step.title_template || "").trim(),
        String(step.description_template || "").trim(),
        String(step.open_button_text || "").trim() || "Open ABA Mobile",
        String(step.web_button_text || "").trim() || "Open Web Checkout",
        String(step.currency || "").trim() || "USD",
        String(step.payment_limit || "").trim() || "5",
        String(step.parse_mode || "").trim() || "HTML",
        String(step.deep_link_prefix || "").trim() || "abamobilebank://",
        String(step.merchant_ref_prefix || "").trim() || "cart",
      ].join(" | ");
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const returnUrl = String(step.return_url || "").trim();
      return `#${stepNo} payway_payment - ${returnUrl || "(missing return_url)"}`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'payway_payment')">` +
        `<div>` +
        `<label>Return URL</label>` +
        `<input placeholder="https://example.com/paymentRespond" :value="currentStepField(${ctx}, 'return_url')" @input="updateCurrentStepField(${ctx}, 'return_url', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Currency</label>` +
        `<input placeholder="USD" :value="currentStepField(${ctx}, 'currency')" @input="updateCurrentStepField(${ctx}, 'currency', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Payment Limit</label>` +
        `<input placeholder="5" :value="currentStepField(${ctx}, 'payment_limit')" @input="updateCurrentStepField(${ctx}, 'payment_limit', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Deep Link Prefix</label>` +
        `<input placeholder="abamobilebank://" :value="currentStepField(${ctx}, 'deep_link_prefix')" @input="updateCurrentStepField(${ctx}, 'deep_link_prefix', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Open Button Text</label>` +
        `<input placeholder="Open ABA Mobile" :value="currentStepField(${ctx}, 'open_button_text')" @input="updateCurrentStepField(${ctx}, 'open_button_text', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Web Button Text</label>` +
        `<input placeholder="Open Web Checkout" :value="currentStepField(${ctx}, 'web_button_text')" @input="updateCurrentStepField(${ctx}, 'web_button_text', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Merchant Ref Prefix</label>` +
        `<input placeholder="cart" :value="currentStepField(${ctx}, 'merchant_ref_prefix')" @input="updateCurrentStepField(${ctx}, 'merchant_ref_prefix', $event.target.value)">` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'payway_payment')">Payment Message Template</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'payway_payment')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_total_price}', $event)">Total</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_lines}', $event)">Lines</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{payment_deep_link}', $event)">Deep Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{payment_link}', $event)">Web Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea placeholder="Use {cart_total_price}, {cart_lines}, {payment_deep_link}, {payment_link}" :value="currentStepField(${ctx}, 'text_template')" @input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'payway_payment')">Title Template</label>` +
        `<textarea v-if="isStepType(${ctx}, 'payway_payment')" placeholder="Cart payment for {bot_name}" :value="currentStepField(${ctx}, 'title_template')" @input="updateCurrentStepField(${ctx}, 'title_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'payway_payment')">Description Template</label>` +
        `<textarea v-if="isStepType(${ctx}, 'payway_payment')" placeholder="{cart_lines}" :value="currentStepField(${ctx}, 'description_template')" @input="updateCurrentStepField(${ctx}, 'description_template', $event.target.value)"></textarea>` +
        `<label v-if="isStepType(${ctx}, 'payway_payment')">Empty Cart Template</label>` +
        `<textarea v-if="isStepType(${ctx}, 'payway_payment')" placeholder="Your cart is empty." :value="currentStepField(${ctx}, 'empty_text_template')" @input="updateCurrentStepField(${ctx}, 'empty_text_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
