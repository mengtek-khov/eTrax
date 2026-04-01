from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from etrax.adapters.frappe.cart_state_store import FrappeCartStateStore
from etrax.adapters.frappe.profile_log_store import FrappeUserProfileLogStore
from etrax.core.flow import ModuleOutcome
from etrax.standalone.bot_runtime_manager import BotRuntimeManager
from etrax.standalone.runtime_update_router import handle_message_update
from etrax.standalone.start_welcome_runner import _log_profile


class FakeFrappeDB:
    def __init__(self, frappe_module: "FakeFrappeModule") -> None:
        self._frappe = frappe_module

    def get_value(self, doctype: str, filters: dict[str, object], fieldname: str) -> object | None:
        for doc in self._frappe._docs.get(doctype, {}).values():
            if _matches_filters(doc, filters):
                if fieldname == "name":
                    return doc.get("name")
                return doc.get(fieldname)
        return None

    def commit(self) -> None:
        self._frappe.commits += 1


class FakeFrappeDoc:
    def __init__(self, frappe_module: "FakeFrappeModule", payload: dict[str, object]) -> None:
        self._frappe = frappe_module
        self._payload = dict(payload)

    def update(self, payload: dict[str, object]) -> None:
        self._payload.update(payload)

    def save(self, ignore_permissions: bool = True) -> None:
        self._frappe.persist(self._payload)

    def insert(self, ignore_permissions: bool = True) -> None:
        self._frappe.persist(self._payload)


class FakeFrappeModule(ModuleType):
    def __init__(self) -> None:
        super().__init__("frappe")
        self._docs: dict[str, dict[str, dict[str, object]]] = {}
        self._name_counters: dict[str, int] = {}
        self.commits = 0
        self.db = FakeFrappeDB(self)

    def get_all(
        self,
        doctype: str,
        filters: dict[str, object] | None = None,
        fields: list[str] | None = None,
        order_by: str | None = None,
        limit_page_length: int | None = None,
        pluck: str | None = None,
    ) -> list[object]:
        docs = list(self._docs.get(doctype, {}).values())
        if filters:
            docs = [doc for doc in docs if _matches_filters(doc, filters)]
        if order_by:
            field_name, _, direction = order_by.partition(" ")
            docs.sort(key=lambda doc: str(doc.get(field_name, "")))
            if direction.strip().lower() == "desc":
                docs.reverse()
        if limit_page_length is not None:
            docs = docs[:limit_page_length]
        if pluck:
            return [doc.get(pluck) for doc in docs]
        if fields:
            return [{field: doc.get(field) for field in fields} for doc in docs]
        return [dict(doc) for doc in docs]

    def get_doc(self, *args):
        if len(args) == 1 and isinstance(args[0], dict):
            return FakeFrappeDoc(self, args[0])
        if len(args) == 2:
            doctype = str(args[0])
            name = str(args[1])
            payload = self._docs.get(doctype, {}).get(name)
            if payload is None:
                raise KeyError(name)
            return FakeFrappeDoc(self, payload)
        raise TypeError("unsupported fake frappe.get_doc usage")

    def delete_doc(self, doctype: str, name: str, force: bool = True, ignore_permissions: bool = True) -> None:
        self._docs.get(doctype, {}).pop(name, None)

    def persist(self, payload: dict[str, object]) -> None:
        doctype = str(payload.get("doctype", "")).strip()
        if not doctype:
            raise ValueError("doctype is required")
        doc_bucket = self._docs.setdefault(doctype, {})
        name = str(payload.get("name", "")).strip()
        if not name:
            next_counter = self._name_counters.get(doctype, 0) + 1
            self._name_counters[doctype] = next_counter
            name = f"{doctype}-{next_counter}"
        stored = dict(payload)
        stored["name"] = name
        doc_bucket[name] = stored


class FakeTokenService:
    def get_token(self, bot_id: str) -> str | None:
        return None


class FakeScaffoldStore:
    def ensure(self, bot_id: str) -> tuple[Path, bool]:
        return Path(f"{bot_id}.json"), False


class CaptureModule:
    def __init__(self) -> None:
        self.context: dict[str, object] | None = None

    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        self.context = dict(context)
        return ModuleOutcome(stop=True, reason="captured")


def test_frappe_user_profile_log_store_round_trips_profiles() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeUserProfileLogStore()
        store.upsert_profile(
            bot_id="support-bot",
            user_id="77",
            profile_updates={
                "username": "alice_user",
                "chat_ids": ["1001"],
                "interaction_count": 1,
            },
        )
        store.upsert_profile(
            bot_id="support-bot",
            user_id="77",
            profile_updates={
                "phone_number": "+85522222222",
                "chat_ids": ["1002"],
            },
        )

        profile = store.get_profile(bot_id="support-bot", user_id="77")

        assert profile is not None
        assert profile["bot_id"] == "support-bot"
        assert profile["telegram_user_id"] == "77"
        assert profile["username"] == "alice_user"
        assert profile["phone_number"] == "+85522222222"
        assert profile["chat_ids"] == ["1001", "1002"]
        assert fake_frappe.commits == 2
    finally:
        _restore_frappe_module(previous)


def test_frappe_user_profile_log_store_feeds_runtime_profile_fallback() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeUserProfileLogStore()
        store.upsert_profile(
            bot_id="support-bot",
            user_id="77",
            profile_updates={
                "first_name": "Alice",
                "last_name": "Example",
                "full_name": "Alice Example",
                "username": "alice_user",
                "phone_number": "+85522222222",
                "contact_is_current_user": True,
            },
        )
        module = CaptureModule()

        sent = handle_message_update(
            {
                "message": {
                    "text": "/start",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"start": [module]},
            start_returning_user=False,
            profile_log_store=store,
        )

        assert sent == 1
        assert module.context is not None
        assert module.context["contact_phone_number"] == "+85522222222"
        assert module.context["contact_is_current_user"] is True
        assert module.context["contact_user_id"] == "77"
    finally:
        _restore_frappe_module(previous)


def test_frappe_user_profile_log_store_supports_start_welcome_profile_logging() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeUserProfileLogStore()
        _log_profile(
            store,
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "last_name": "Example",
                        "username": "alice_user",
                    },
                    "contact": {
                        "user_id": 77,
                        "first_name": "Alice",
                        "phone_number": "+85522222222",
                    },
                }
            },
            bot_id="support-bot",
        )

        profile = store.get_profile(bot_id="support-bot", user_id="77")

        assert profile is not None
        assert profile["phone_number"] == "+85522222222"
        assert profile["contact_is_current_user"] is True
    finally:
        _restore_frappe_module(previous)


def test_frappe_user_profile_log_store_deletes_profiles() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeUserProfileLogStore()
        store.upsert_profile(
            bot_id="support-bot",
            user_id="77",
            profile_updates={"first_name": "Alice"},
        )

        store.delete_profile(bot_id="support-bot", user_id="77")

        assert store.get_profile(bot_id="support-bot", user_id="77") is None
    finally:
        _restore_frappe_module(previous)


def test_frappe_cart_state_store_round_trips_quantities() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeCartStateStore()
        assert store.get_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee") is None

        store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee", quantity=3)
        store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="tea", quantity=1)

        assert store.get_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee") == 3
        assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {
            "coffee": 3,
            "tea": 1,
        }

        store.remove_product(bot_id="shop-bot", chat_id="1001", product_key="coffee")

        assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {"tea": 1}
        assert fake_frappe.commits == 3
    finally:
        _restore_frappe_module(previous)


def test_frappe_cart_state_store_clears_chat() -> None:
    fake_frappe = FakeFrappeModule()
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = fake_frappe
    try:
        store = FrappeCartStateStore()
        store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee", quantity=3)
        store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="tea", quantity=1)

        store.clear_chat(bot_id="shop-bot", chat_id="1001")

        assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {}
    finally:
        _restore_frappe_module(previous)


def test_bot_runtime_manager_accepts_injected_stores(tmp_path: Path) -> None:
    cart_state_store = object()
    profile_log_store = object()
    scaffold_store = FakeScaffoldStore()

    manager = BotRuntimeManager(
        token_service=FakeTokenService(),
        bot_config_dir=tmp_path / "bot_processes",
        state_file=tmp_path / "update_offsets.json",
        cart_state_store=cart_state_store,  # type: ignore[arg-type]
        profile_log_store=profile_log_store,  # type: ignore[arg-type]
        scaffold_store=scaffold_store,
    )

    assert manager._cart_state_store is cart_state_store
    assert manager._profile_log_store is profile_log_store
    assert manager._scaffold_store is scaffold_store


def _matches_filters(doc: dict[str, object], filters: dict[str, object]) -> bool:
    return all(doc.get(key) == value for key, value in filters.items())


def _restore_frappe_module(previous: object) -> None:
    if previous is None:
        sys.modules.pop("frappe", None)
    else:
        sys.modules["frappe"] = previous  # type: ignore[assignment]
