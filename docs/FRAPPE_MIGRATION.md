## eTrax to Frappe Migration Guide

This document explains how to move the current standalone-first eTrax project into Frappe without breaking the working local setup.

The short version:

1. Keep the current standalone runtime working.
2. Move storage to Frappe first.
3. Move Telegram update entrypoints into Frappe.
4. Move bot config storage into Frappe.
5. Only after that, decide whether to port the standalone Vue editor into a Frappe page.

This order matters because the current project is stable in standalone mode, and most framework coupling is in storage and HTTP/UI hosting rather than in the core Telegram modules.

## Current Architecture

The project is already split into three layers:

- `etrax.core`
  - framework-agnostic Telegram/business logic
- `etrax.adapters`
  - storage and framework-specific integrations
- `etrax.standalone`
  - local HTTP UI, local polling runtime, and JSON-file workflows

Important current entry points:

- standalone UI:
  - [src/etrax/standalone/token_ui.py](/d:/python/eTrax/src/etrax/standalone/token_ui.py)
- standalone runtime:
  - [src/etrax/standalone/bot_runtime_manager.py](/d:/python/eTrax/src/etrax/standalone/bot_runtime_manager.py)
- `/start` standalone runner:
  - [src/etrax/standalone/start_welcome_runner.py](/d:/python/eTrax/src/etrax/standalone/start_welcome_runner.py)
- CLI bootstrap:
  - [src/etrax/__main__.py](/d:/python/eTrax/src/etrax/__main__.py)

Current local persistence:

- encrypted tokens:
  - [src/etrax/adapters/local/json_token_store.py](/d:/python/eTrax/src/etrax/adapters/local/json_token_store.py)
- cart state:
  - [src/etrax/adapters/local/json_cart_state_store.py](/d:/python/eTrax/src/etrax/adapters/local/json_cart_state_store.py)
- user profile log:
  - [src/etrax/adapters/local/json_user_profile_log_store.py](/d:/python/eTrax/src/etrax/adapters/local/json_user_profile_log_store.py)
- bot workflow configs:
  - [src/etrax/adapters/local/bot_process_scaffold_store.py](/d:/python/eTrax/src/etrax/adapters/local/bot_process_scaffold_store.py)

Current Frappe adapters:

- token store:
  - [src/etrax/adapters/frappe/token_store.py](/d:/python/eTrax/src/etrax/adapters/frappe/token_store.py)
- profile log store:
  - [src/etrax/adapters/frappe/profile_log_store.py](/d:/python/eTrax/src/etrax/adapters/frappe/profile_log_store.py)
- cart state store:
  - [src/etrax/adapters/frappe/cart_state_store.py](/d:/python/eTrax/src/etrax/adapters/frappe/cart_state_store.py)

## What Is Already Portable

These parts are already suitable for Frappe with little or no redesign:

- Telegram modules in `etrax.core.telegram`
- token service in `etrax.core.token`
- profile extraction logic in [src/etrax/standalone/profile_logging.py](/d:/python/eTrax/src/etrax/standalone/profile_logging.py)
- cart-related module contracts via `CartStateStore`
- runtime storage injection in [src/etrax/standalone/bot_runtime_manager.py](/d:/python/eTrax/src/etrax/standalone/bot_runtime_manager.py)

This means the Frappe migration does not need to rewrite cart, checkout, share-contact, PayWay, or message/photo modules.

## What Is Still Standalone-Coupled

These parts still assume local files or standalone HTTP routes:

- the standalone config UI:
  - [src/etrax/standalone/token_ui.py](/d:/python/eTrax/src/etrax/standalone/token_ui.py)
- the standalone Vue editor:
  - [src/etrax/standalone/config_vue.js](/d:/python/eTrax/src/etrax/standalone/config_vue.js)
  - [src/etrax/standalone/vue_modules](/d:/python/eTrax/src/etrax/standalone/vue_modules)
- bot config storage in JSON files:
  - `data/bot_processes/*.json`
- standalone polling offset file:
  - `data/update_offsets.json`

This is why the recommended migration path is storage first, UI later.

## Recommended Migration Plan

### Phase 1: Keep Standalone as the Working Baseline

Do not remove or weaken the current standalone mode.

Reason:

- the current bot builder already works locally
- the current Vue editor depends on standalone routes and server-rendered state
- standalone mode is your fastest fallback if Frappe integration is incomplete

Success criteria:

- `python -m etrax token-ui` still works
- local JSON storage still works
- no Frappe dependency is required for local usage

### Phase 2: Move Storage to Frappe First

This is the safest first migration step.

Already implemented:

- `FrappeBotTokenStore`
- `FrappeUserProfileLogStore`
- `FrappeCartStateStore`

The runtime now supports injected stores in:

- [src/etrax/standalone/bot_runtime_manager.py](/d:/python/eTrax/src/etrax/standalone/bot_runtime_manager.py)

Current switch pattern:

- local:
  - pass nothing and JSON stores are used by default
- frappe:
  - inject Frappe stores into `BotRuntimeManager`

Example:

```python
from pathlib import Path

from etrax.adapters.frappe import FrappeCartStateStore, FrappeUserProfileLogStore
from etrax.standalone.bot_runtime_manager import BotRuntimeManager

runtime = BotRuntimeManager(
    token_service=token_service,
    bot_config_dir=Path("data/bot_processes"),
    state_file=Path("data/update_offsets.json"),
    cart_state_store=FrappeCartStateStore(),
    profile_log_store=FrappeUserProfileLogStore(),
)
```

Required Frappe DocTypes for this phase:

1. `eTrax Bot Token`
   - `bot_id`
   - `encrypted_token`
   - `created_at`
   - `updated_at`

2. `eTrax User Profile`
   - `bot_id`
   - `telegram_user_id`
   - `profile_json`

3. `eTrax Cart Item`
   - `bot_id`
   - `chat_id`
   - `product_key`
   - `quantity`

Recommended indexes:

- unique on `eTrax Bot Token.bot_id`
- unique on `eTrax User Profile.(bot_id, telegram_user_id)`
- unique on `eTrax Cart Item.(bot_id, chat_id, product_key)`

### Phase 3: Add Frappe Bot Config Storage

This is the next missing backend piece.

Right now bot config still comes from local JSON:

- [src/etrax/adapters/local/bot_process_scaffold_store.py](/d:/python/eTrax/src/etrax/adapters/local/bot_process_scaffold_store.py)

You should add:

- `FrappeBotProcessScaffoldStore`

Suggested DocType:

1. `eTrax Bot Config`
   - `bot_id`
   - `config_json`
   - `created_at`
   - `updated_at`

Suggested store methods:

- `ensure(bot_id) -> tuple[Path | str, bool]`
- `get(bot_id) -> dict[str, object]`
- `save(bot_id, payload) -> None`
- `clone(source_bot_id, target_bot_id, overwrite=False) -> None`

Important design recommendation:

- do not force Frappe storage semantics into the runtime
- instead, introduce a bot-config store interface similar to cart/profile stores
- then update the runtime and UI loaders to depend on that interface

That keeps local JSON and Frappe DB interchangeable.

### Phase 4: Move Telegram Update Handling into Frappe

After storage is ready, move update ingestion.

There are two practical models:

1. Telegram webhook into Frappe
2. Scheduled poller from Frappe

Recommended:

- use webhook if your Frappe site has a public HTTPS endpoint
- use poller only if webhook deployment is blocked

Recommended Frappe integration points:

- whitelisted API method for webhook
- background job or scheduler task for polling

The core handler logic can still reuse:

- [src/etrax/standalone/bot_runtime_manager.py](/d:/python/eTrax/src/etrax/standalone/bot_runtime_manager.py)

But you will likely want to extract a smaller reusable update-dispatch service from it later so Frappe can process one update at a time without owning the whole standalone polling loop.

Recommended refactor:

- keep `BotRuntimeManager` for standalone polling/thread control
- extract a reusable `process_update(...)` service from `_handle_update(...)`
- let Frappe webhook/poller call that service directly

### Phase 5: Move Polling Offset Storage into Frappe

If you use Frappe polling instead of webhook, do not keep offsets in `data/update_offsets.json`.

Suggested DocType:

1. `eTrax Bot Runtime State`
   - `bot_id`
   - `last_update_offset`
   - `updated_at`

If you use webhook only, this phase is not needed.

### Phase 6: Decide What to Do With the Vue Editor

This is the highest-effort part.

Important fact:

- Frappe can host Vue
- but the current Vue editor is not a drop-in Frappe app

Why:

- the current editor depends on [src/etrax/standalone/token_ui.py](/d:/python/eTrax/src/etrax/standalone/token_ui.py) routes
- it expects server-rendered HTML and embedded JSON state
- it loads module scripts from standalone paths such as `/module-*.js`

You have two options:

#### Option A: Keep the Standalone UI, Use Frappe Only as Backend Storage

This is the fastest and lowest-risk path.

Pros:

- least rewrite
- your team keeps the current workflow
- Frappe gets the data

Cons:

- two deployment surfaces
- config UI is not inside Frappe Desk

This is the recommended first production path.

#### Option B: Port the Config UI Into a Frappe Page

This is the full integration path.

Work needed:

1. Create a Frappe Desk page or app page.
2. Serve Vue assets through Frappe’s asset pipeline.
3. Replace `token_ui.py` HTTP endpoints with Frappe API methods.
4. Replace server-rendered HTML state injection with API-driven fetch/save.
5. Move token/config/run/stop actions to Frappe endpoints.

Required endpoint equivalents:

- list configured bots
- get bot config
- save bot config
- save token
- revoke token
- duplicate/mirror config
- run bot
- stop bot
- get runtime status

Recommended rule:

- do not port the UI until storage and update handling are stable in Frappe

## Suggested Frappe App Layout

Inside your Frappe app, use a structure like this:

```text
my_frappe_app/
  my_frappe_app/
    api/
      etrax_bot.py
    doctype/
      etrax_bot_token/
      etrax_user_profile/
      etrax_cart_item/
      etrax_bot_config/
      etrax_bot_runtime_state/
    services/
      etrax_runtime.py
      etrax_storage.py
    www/ or desk page assets/
      etrax_bot_builder/
```

Recommended responsibility split:

- DocTypes own persistence schema
- `services/etrax_storage.py` builds the correct eTrax stores
- `services/etrax_runtime.py` builds runtime/update-processing objects
- `api/etrax_bot.py` exposes webhook/UI endpoints

## Minimal First Frappe Runtime

If you want the smallest real migration, implement this first:

1. keep standalone UI
2. use Frappe token/profile/cart stores
3. keep bot config in local JSON
4. keep standalone polling runtime

That already gives you:

- DB-backed token storage
- DB-backed profile log
- DB-backed cart state

and avoids the hardest parts until later.

## Full Migration Checklist

### Backend

1. Install Frappe and your app.
2. Add required DocTypes.
3. Add unique indexes for bot/profile/cart records.
4. Wire `FrappeBotTokenStore`.
5. Wire `FrappeUserProfileLogStore`.
6. Wire `FrappeCartStateStore`.
7. Add `FrappeBotProcessScaffoldStore`.
8. Add runtime-state/offset store if using polling.
9. Extract reusable single-update processing from standalone runtime.
10. Add webhook or poll scheduler in Frappe.

### UI

1. Decide whether standalone UI stays or is replaced.
2. If porting, recreate `token_ui.py` endpoints in Frappe.
3. Move Vue assets into Frappe asset build.
4. Replace embedded JSON bootstrap with Frappe API fetches.
5. Add auth/permissions for admin users.

### Operations

1. Keep local mode as fallback while rolling out.
2. Test one bot in Frappe before moving all bots.
3. Migrate existing local JSON data into Frappe tables.
4. Add backup/export for bot configs and profiles.
5. Monitor Telegram webhook or poll failures.

## Data Migration Mapping

Use this mapping when moving local files into Frappe:

- `data/tokens.json`
  - migrate to `eTrax Bot Token`
- `data/profile_log.json`
  - migrate to `eTrax User Profile`
- `data/cart_state.json`
  - migrate to `eTrax Cart Item`
- `data/bot_processes/*.json`
  - migrate to `eTrax Bot Config`
- `data/update_offsets.json`
  - migrate to `eTrax Bot Runtime State` if polling remains

## Risks and Constraints

### Telegram Data Availability

Telegram usually does not provide these fields to bots automatically:

- `date_of_birth`
- `gender`
- `bio`

Those fields are placeholders in the current profile log flow. If you want them in Frappe, you must collect them through your own bot conversation modules.

### Current Vue Editor

Do not assume the current Vue editor can simply be dropped into Frappe.

It needs route, asset, and state-loading changes because it is currently coupled to:

- [src/etrax/standalone/token_ui.py](/d:/python/eTrax/src/etrax/standalone/token_ui.py)

### Local/Remote Dual Mode

Do not remove local mode during migration.

The project is safer if you preserve:

- local JSON mode for development and fallback
- Frappe-backed mode for deployment/integration

The current `BotRuntimeManager` already supports this direction because injected stores are optional.

## Recommended Next Steps

If you want the migration to proceed with low risk, implement in this order:

1. add `FrappeBotProcessScaffoldStore`
2. add a storage backend selector for runtime/bootstrap
3. extract one-update processing from `BotRuntimeManager`
4. create Frappe webhook endpoint
5. only then decide whether to port the Vue UI

If you want the fastest visible result, stop after step 4 and keep the current standalone Vue editor.
