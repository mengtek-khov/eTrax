from __future__ import annotations

from pathlib import Path

from etrax.adapters.local.json_cart_state_store import JsonCartStateStore


def test_json_cart_state_store_round_trips_quantity(tmp_path: Path) -> None:
    store = JsonCartStateStore(tmp_path / "cart_state.json")

    assert store.get_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee") is None

    store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee", quantity=3)

    assert store.get_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee") == 3


def test_json_cart_state_store_lists_and_removes_products(tmp_path: Path) -> None:
    store = JsonCartStateStore(tmp_path / "cart_state.json")

    store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee", quantity=3)
    store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="tea", quantity=1)

    assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {
        "coffee": 3,
        "tea": 1,
    }

    store.remove_product(bot_id="shop-bot", chat_id="1001", product_key="coffee")

    assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {"tea": 1}


def test_json_cart_state_store_clears_chat(tmp_path: Path) -> None:
    store = JsonCartStateStore(tmp_path / "cart_state.json")

    store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="coffee", quantity=3)
    store.set_quantity(bot_id="shop-bot", chat_id="1001", product_key="tea", quantity=1)

    store.clear_chat(bot_id="shop-bot", chat_id="1001")

    assert store.list_quantities(bot_id="shop-bot", chat_id="1001") == {}
