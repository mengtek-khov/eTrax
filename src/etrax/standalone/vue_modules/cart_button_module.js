(function (global) {
  "use strict";

  const moduleSystem = global.EtraxModuleSystem;
  if (!moduleSystem) {
    return;
  }

  moduleSystem.register({
    type: "cart_button",
    label: "cart_button",
    defaultStep() {
      return {
        module_type: "cart_button",
        text_template: "",
        parse_mode: "",
        hide_caption: false,
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: "",
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
        module_type: "cart_button",
        text_template: source.text_template ? String(source.text_template) : "",
        parse_mode: source.parse_mode ? String(source.parse_mode) : "",
        hide_caption: Boolean(source.hide_caption),
        title: "Main Menu",
        items: [],
        buttons: [],
        photo_url: source.photo_url ? String(source.photo_url) : "",
        product_name: source.cart_product_name ? String(source.cart_product_name) : (source.product_name ? String(source.product_name) : ""),
        product_key: source.cart_product_key ? String(source.cart_product_key) : (source.product_key ? String(source.product_key) : ""),
        price: source.cart_price ? String(source.cart_price) : (source.price ? String(source.price) : ""),
        quantity: source.cart_qty ? String(source.cart_qty) : (source.quantity ? String(source.quantity) : "1"),
        min_qty: source.cart_min_qty ? String(source.cart_min_qty) : (source.min_qty ? String(source.min_qty) : "0"),
        max_qty: source.cart_max_qty ? String(source.cart_max_qty) : (source.max_qty ? String(source.max_qty) : "99"),
      };
    },
    parseChain(parts) {
      if (String(parts[0] || "").trim().toLowerCase() !== "cart_button") {
        return null;
      }
      const extras = parts.slice(9);
      let photoUrl = "";
      let hideCaption = false;
      for (const extra of extras) {
        const trimmed = String(extra || "").trim();
        if (!trimmed) {
          continue;
        }
        if (trimmed.startsWith("photo:")) {
          photoUrl = trimmed.slice(6);
          continue;
        }
        if (trimmed.toLowerCase() === "hide_caption") {
          hideCaption = true;
        }
      }
      return {
        module_type: "cart_button",
        product_name: parts[1] || "",
        price: parts[2] || "",
        quantity: parts[3] || "1",
        min_qty: parts[4] || "0",
        max_qty: parts[5] || "99",
        text_template: parts[6] || "",
        product_key: parts[7] || "",
        parse_mode: parts[8] || "",
        photo_url: photoUrl,
        hide_caption: hideCaption,
        title: "Main Menu",
        items: [],
        buttons: [],
      };
    },
    formatChain(step) {
      const productName = String(step.product_name || "").trim();
      const price = String(step.price || "").trim();
      const qty = String(step.quantity || "").trim() || "1";
      const minQty = String(step.min_qty || "").trim() || "0";
      const maxQty = String(step.max_qty || "").trim() || "99";
      const text = String(step.text_template || "").trim();
      const productKey = String(step.product_key || "").trim();
      const parseMode = String(step.parse_mode || "").trim();
      const photoUrl = String(step.photo_url || "").trim();
      const hideCaption = Boolean(step.hide_caption);
      let payload = `cart_button | ${productName} | ${price} | ${qty} | ${minQty} | ${maxQty} | ${text} | ${productKey}`;
      if (parseMode) {
        payload += ` | ${parseMode}`;
      }
      if (photoUrl) {
        if (!parseMode) {
          payload += ` | `;
        }
        payload += ` | photo:${photoUrl}`;
      }
      if (hideCaption) {
        if (!parseMode && !photoUrl) {
          payload += ` | `;
        }
        payload += ` | hide_caption`;
      }
      return payload;
    },
    rowLabel(step, index) {
      const stepNo = index + 1;
      const productName = String(step.product_name || "").trim();
      const qty = String(step.quantity || "").trim() || "1";
      const price = String(step.price || "").trim();
      return `#${stepNo} cart_button - ${productName || "(empty product)"} (${qty}${price ? ` x ${price}` : ""})`;
    },
    editorTemplate(args) {
      const ctx = args.ctx;
      return (
        `<div class="module-grid" v-if="isStepType(${ctx}, 'cart_button')">` +
        `<div>` +
        `<label>Photo URL or File ID (optional)</label>` +
        `<input placeholder="https://example.com/product.jpg" :value="currentStepField(${ctx}, 'photo_url')" @input="updateCurrentStepField(${ctx}, 'photo_url', $event.target.value)">` +
        `</div>` +
        `<div v-if="currentStepField(${ctx}, 'photo_url')">` +
        `<label class="checkbox compact"><input type="checkbox" :checked="currentStepChecked(${ctx}, 'hide_caption')" @change="updateCurrentStepToggle(${ctx}, 'hide_caption', $event.target.checked)"><span>Hide Caption When Sending Photo</span></label>` +
        `</div>` +
        `<div>` +
        `<label>Product Name</label>` +
        `<input placeholder="Coffee" :value="currentStepField(${ctx}, 'product_name')" @input="updateCurrentStepField(${ctx}, 'product_name', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Product Key (optional)</label>` +
        `<input placeholder="coffee" :value="currentStepField(${ctx}, 'product_key')" @input="updateCurrentStepField(${ctx}, 'product_key', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Price</label>` +
        `<input placeholder="2.50" :value="currentStepField(${ctx}, 'price')" @input="updateCurrentStepField(${ctx}, 'price', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Default Qty</label>` +
        `<input placeholder="1" :value="currentStepField(${ctx}, 'quantity')" @input="updateCurrentStepField(${ctx}, 'quantity', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Min Qty</label>` +
        `<input placeholder="0" :value="currentStepField(${ctx}, 'min_qty')" @input="updateCurrentStepField(${ctx}, 'min_qty', $event.target.value)">` +
        `</div>` +
        `<div>` +
        `<label>Max Qty</label>` +
        `<input placeholder="99" :value="currentStepField(${ctx}, 'max_qty')" @input="updateCurrentStepField(${ctx}, 'max_qty', $event.target.value)">` +
        `</div>` +
        `</div>` +
        `<label v-if="isStepType(${ctx}, 'cart_button') && !(currentStepField(${ctx}, 'photo_url') && currentStepChecked(${ctx}, 'hide_caption'))">Message Template</label>` +
        `<div class="template-editor" v-if="isStepType(${ctx}, 'cart_button') && !(currentStepField(${ctx}, 'photo_url') && currentStepChecked(${ctx}, 'hide_caption'))">` +
        `<div class="template-toolbar">` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<b>', '</b>', $event)">Bold</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<i>', '</i>', $event)">Italic</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<blockquote>', '</blockquote>', $event)">Quote</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<tg-spoiler>', '</tg-spoiler>', $event)">Spoiler</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="applyTemplateSnippet(${ctx}, 'text_template', '<a href=&quot;https://example.com&quot;>', '</a>', $event)">Link</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{bot_name}', $event)">Bot</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{product_name}', $event)">Product</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{price}', $event)">Price</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_quantity}', $event)">Qty</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '{cart_total_price}', $event)">Total</button>` +
        `<button type="button" class="secondary" @mousedown.prevent="insertTemplateToken(${ctx}, 'text_template', '\\n', $event)">Line</button>` +
        `</div>` +
        `<textarea ` +
        `placeholder="Use {product_name}, {price}, {cart_quantity}, {cart_total_price}" ` +
        `:value="currentStepField(${ctx}, 'text_template')" ` +
        `@input="updateCurrentStepField(${ctx}, 'text_template', $event.target.value)"></textarea>` +
        `</div>`
      );
    },
  });
})(window);
