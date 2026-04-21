from __future__ import annotations

from pathlib import Path

from etrax.adapters.local.json_bound_code_store import JsonBoundCodeStore
from etrax.core.telegram.bind_code import BindCodeConfig, BindCodeModule


def test_bind_code_module_generates_incrementing_codes_and_updates_context() -> None:
    file_path = Path("data/_test_bound_codes.json")
    try:
        if file_path.exists():
            file_path.unlink()
        store = JsonBoundCodeStore(file_path)
        module = BindCodeModule(
            bound_code_store=store,
            config=BindCodeConfig(
                bot_id="Testing 2",
                route_key="etrex",
                prefix="ETX-",
                number_width=4,
                start_number=1,
            ),
        )

        first = module.execute(
            {
                "chat_id": "1001",
                "user_id": "2001",
                "profile": {"first_name": "Alice"},
            }
        )
        second = module.execute(
            {
                "chat_id": "1002",
                "user_id": "2002",
            }
        )

        assert first.reason == "bind_code_assigned"
        assert first.context_updates["bound_code"] == "ETX-0001"
        assert first.context_updates["bound_code_number"] == 1
        assert first.context_updates["bound_code_number_text"] == "0001"
        assert first.context_updates["profile"]["bound_code"] == "ETX-0001"

        assert second.context_updates["bound_code"] == "ETX-0002"
        stored = store.get_binding_by_code(bot_id="Testing 2", code="ETX-0002")
        assert stored is not None
        assert stored["user_id"] == "2002"
        assert stored["route_key"] == "etrex"
    finally:
        if file_path.exists():
            file_path.unlink()
