# eTrax

Standalone Telegram bot builder scaffold with modular, composable workflows. The architecture is framework-agnostic so integration with other frameworks (for example, Frappe) can be added later.

## Architecture

- `etrax.core`: Framework-agnostic business logic, flow engine, and interfaces.
- `etrax.adapters`: Framework/storage implementations (`inmemory`, `frappe`).
- `etrax.app`: Dependency wiring for runtime composition.

This keeps `core` reusable while adapters isolate framework dependencies.

## Standalone Usage

Install editable package:

```bash
pip install -e .
```

Run Telegram token configuration web UI:

```bash
python -m etrax token-ui --host 127.0.0.1 --port 8765
```

Enable UI code hot reload (development):

```bash
python -m etrax token-ui --dev-hot-reload
```

By default, this stores:

- encrypted tokens in `data/tokens.json`
- local Fernet key in `data/token.key` (or use `ETRAX_TOKEN_SECRET` env var)
- per-bot process scaffold in `data/bot_processes/<bot_id>.json`

UI actions:
- `Config` button opens dedicated bot setup page for module/module-config editing.
- Runtime control (`Run` / `Stop`) is available in both `Configured Bots` list and dedicated `Config` page.
- Runtime hot-reloads bot process config on every polling cycle, so config/module-registry edits are applied without restart.

Run standalone demo:

```bash
python -m etrax tracking ETX-001
```

Run `/start -> welcome` polling loop:

```bash
python -m etrax start-welcome --bot-id support-bot --welcome-template "Welcome {user_first_name}!"
```

## Flow Engine (Client-Custom Sequences)

Use `FlowGraph` to define allowed transitions and `FlowEngine` to execute modules with loop safety.

```python
from etrax.core.flow import FlowEngine, FlowGraph, ModuleOutcome


class ModuleA:
    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        return ModuleOutcome(next_module="B")


class ModuleB:
    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        count = int(context.get("count", 0)) + 1
        if count < 3:
            return ModuleOutcome(context_updates={"count": count}, next_module="A")
        return ModuleOutcome(context_updates={"count": count}, next_module="C")


class ModuleC:
    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        return ModuleOutcome(next_module="D")


class ModuleD:
    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        return ModuleOutcome(stop=True, reason="done")


graph = FlowGraph(
    {
        "A": ["B", "D"],
        "B": ["A", "C", "D"],
        "C": ["D"],
        "D": [],
    }
)

engine = FlowEngine(
    graph,
    modules={"A": ModuleA(), "B": ModuleB(), "C": ModuleC(), "D": ModuleD()},
    max_steps=100,
    max_visits_per_module=20,
)

result = engine.run_auto("A", initial_context={"client_id": "acme"})
print(result.history, result.stop_reason)
```

For fixed client paths, you can validate and execute explicit routes (repeats allowed):

```python
graph.validate_path(["A", "B", "A", "D"])
result = engine.run_path(["A", "B", "A", "D"])
```

## Telegram Builder Direction

- Each module represents a bot capability block (for example: intake, intent parse, route, reply, handoff).
- Client-specific bot behavior is composed by configuring module transitions.
- Repeated modules are supported for retry/clarification loops.

## Send Message Module

Core module available: `SendTelegramMessageModule` in `etrax.core.telegram`.

It resolves:
- `bot_id` -> token from `BotTokenService` (or any compatible token resolver)
- `chat_id` and message text from context or fixed config
- optional template rendering via `text_template`

Example usage in a flow:

```python
from etrax.adapters.telegram.bot_api_gateway import TelegramBotApiGateway
from etrax.core.telegram import SendMessageConfig, SendTelegramMessageModule

send_module = SendTelegramMessageModule(
    token_resolver=token_service,  # token_service.get_token(bot_id)
    gateway=TelegramBotApiGateway(),
    config=SendMessageConfig(
        bot_id="support-bot",
        text_template="Hello {customer_name}, ticket #{ticket_id} is in progress.",
        next_module="end",
    ),
)
```

`StartWelcomeHandler` is also available to process updates and trigger welcome on `/start`.

## Token Security Model

- Tokens are encrypted before persistence using `cryptography.fernet`.
- Web UI only shows masked token values.
- Service layer supports set/list/revoke without exposing plaintext in logs.
- Frappe integration can reuse the same core service via `FrappeBotTokenStore`.

## Per-Bot Process File

When you save a token in the UI, a dedicated process file is auto-created for that `bot_id`.
This file is the place to attach module registry and flow transitions for custom bot behavior.
Use the `Config` page to update:
- default bot command menu settings
- enable/disable command menu sync
- include/exclude `/start` and `/menu`
- add/remove custom commands
- optional command descriptions
- per-command process module setup (`send_message` or `menu`) for:
  - default `/start`
  - default `/menu`
  - every custom command
- module chaining per command:
  - each command can define multiple chained steps
  - chain-step format in UI:
    - `send_message | your text template | optional_parse_mode`
    - `menu | Menu Title | /a - A; /b - B | optional_parse_mode`
  - Module Setup page includes visual list controls to add/remove/reorder module steps.

Legacy `Start Module (/start)` and `Menu Module (/menu)` sections are hidden; use the command-menu command panels for module setup.
When you press `Save Config`, command menu is synced immediately to Telegram, so removed commands are cleared from bot menu right away.
Default command-menu inclusion is `/start` on and `/menu` off (enable `/menu` explicitly when needed).

For `menu` module type, runtime renders a command list message. Telegram slash-command menu (`setMyCommands`) is synced strictly from `command_menu` config (defaults + custom commands in dedicated config page).

## Frappe Integration

Use adapter API from your Frappe app:

```python
from etrax.adapters.frappe.api import get_tracking_snapshot

snapshot = get_tracking_snapshot("ETX-001")
```

Optional dependency when needed:

```bash
pip install -e .[frappe]
```

Migration guide:

- [docs/FRAPPE_MIGRATION.md](/d:/python/eTrax/docs/FRAPPE_MIGRATION.md)

## Tests

```bash
pytest
```
