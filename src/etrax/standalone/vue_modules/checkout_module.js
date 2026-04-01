(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "checkout",
    label: "checkout",
    defaultStep() {
      return {
        module_type: "checkout",
        text_template: "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
        empty_text_template: "Your cart is empty.",
        pay_button_text: "Pay Now",
        pay_callback_data: "checkout_paynow",
        parse_mode: "HTML",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
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
        module_type: "checkout",
        text_template: source.text_template ? String(source.text_template) : "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
        empty_text_template: source.checkout_empty_text ? String(source.checkout_empty_text) : (source.empty_text_template ? String(source.empty_text_template) : "Your cart is empty."),
        pay_button_text: source.checkout_pay_button_text ? String(source.checkout_pay_button_text) : (source.pay_button_text ? String(source.pay_button_text) : "Pay Now"),
        pay_callback_data: source.checkout_pay_callback_data ? String(source.checkout_pay_callback_data) : (source.pay_callback_data ? String(source.pay_callback_data) : "checkout_paynow"),
        parse_mode: source.parse_mode ? String(source.parse_mode) : "HTML",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
        product_name: "",
        product_key: "",
        price: "",
        quantity: "1",
        min_qty: "0",
        max_qty: "99",
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "checkout") {
        return null;
      }
      return {
        module_type: "checkout",
        text_template: parts[1] || "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
        empty_text_template: parts[2] || "Your cart is empty.",
        pay_button_text: parts[3] || "Pay Now",
        pay_callback_data: parts[4] || "checkout_paynow",
        parse_mode: parts[5] || "HTML",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
        button_text: "",
        success_text_template: "",
        invalid_text_template: "",
        product_name: "",
        product_key: "",
        price: "",
        quantity: "1",
        min_qty: "0",
        max_qty: "99",
      };
    },
    formatChain(step) {
      const text = String(step.text_template || "").trim();
      const emptyText = String(step.empty_text_template || "").trim();
      const payText = String(step.pay_button_text || "").trim() || "Pay Now";
      const payCallback = String(step.pay_callback_data || "").trim() || "checkout_paynow";
      const parseMode = String(step.parse_mode || "").trim() || "HTML";
      return `checkout | ${text} | ${emptyText} | ${payText} | ${payCallback} | ${parseMode}`;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const payText = String(step.pay_button_text || "").trim() || "Pay Now";
      const payCallback = String(step.pay_callback_data || "").trim() || "checkout_paynow";
      return `#${stepNo} checkout - ${payText} (${payCallback})`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'checkout')">` +
        `<div>` +
        `<label>Pay Button Text</label>` +
        `<input placeholder="Pay Now" :value="currentStepField(${ctx}, 'pay_button_text')" @input="updateCurrentStepField(${ctx}, 'pay_button_text', $event.target.value)">` +
        `</div>` +
	        `<div>` +
	        `<label>Pay Callback Data</label>` +
	        `<input list="callback-data-options" placeholder="checkout_paynow" :value="currentStepField(${ctx}, 'pay_callback_data')" @input="updateCurrentStepField(${ctx}, 'pay_callback_data', $event.target.value)">` +
	        `</div>` +
	        `<div>` +
	        `<label>Callback Suggestions</label>` +
	        `<select class="inline-button-input" :value="currentStepField(${ctx}, 'pay_callback_data')" @change="updateCurrentStepField(${ctx}, 'pay_callback_data', $event.target.value)">` +
	        `<option value="">Select callback_data</option>` +
	        `<option v-for="callbackKey in callbackOptions" :key="'checkout-pay-callback-' + callbackKey" :value="callbackKey">[[ callbackKey ]]</option>` +
	        `</select>` +
	        `</div>` +
	        `</div>` +
        `<label v-if="isStepType(${ctx}, 'checkout')">Checkout Template</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'checkout')">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_lines}', $event)">Lines</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_total_price}', $event)">Total</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_total_quantity}', $event)">Qty</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_item_count}', $event)">Items</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea ` +
        `placeholder="Use {cart_lines}, {cart_total_price}, {cart_total_quantity}, {cart_item_count}" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'checkout')">Empty Cart Template</label>` +
        `<textarea v-if="isStepType(${ctx}, 'checkout')" placeholder="Your cart is empty." :value="currentStepField(${ctx}, 'empty_text_template')" @input="updateCurrentStepField(${ctx}, 'empty_text_template', $event.target.value)"></textarea>`
      );
    },
  });
})(window);
