from __future__ import annotations

"""Shared runtime typing contracts used by the standalone Telegram runtime."""

from pathlib import Path
from typing import Any, Protocol

from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartConfig,
    ForgetUserDataConfig,
    LoadCallbackConfig,
    LoadCommandConfig,
    LoadInlineButtonConfig,
    OpenMiniAppConfig,
    PaywayPaymentConfig,
    RouteConfig,
    SendInlineButtonConfig,
    SendMessageConfig,
    SendPhotoConfig,
    ShareContactConfig,
    ShareLocationConfig,
    UserProfileStore,
)

RuntimeStepConfig = (
    SendMessageConfig
    | SendPhotoConfig
    | CartButtonConfig
    | CheckoutCartConfig
    | PaywayPaymentConfig
    | RouteConfig
    | LoadCallbackConfig
    | LoadCommandConfig
    | LoadInlineButtonConfig
    | ShareContactConfig
    | ShareLocationConfig
    | SendInlineButtonConfig
    | OpenMiniAppConfig
    | ForgetUserDataConfig
)


class UserProfileLogStore(UserProfileStore, Protocol):
    """Port for reading and writing per-bot user profile snapshots."""


class TemporaryCommandMenuStateStore(Protocol):
    """Port for persisting active callback-based temporary menus by bot and chat."""

    def set_active_menu(self, *, bot_id: str, chat_id: str, source_callback_key: str) -> None:
        """Persist the active temporary command menu source for one bot/chat pair."""

    def get_active_menu(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Read the active temporary command menu source for one bot/chat pair."""

    def list_active_menus(self, *, bot_id: str) -> list[dict[str, Any]]:
        """List persisted active temporary command menus for one bot."""

    def delete_active_menu(self, *, bot_id: str, chat_id: str) -> None:
        """Remove any persisted active temporary command menu for one bot/chat pair."""


class BotProcessScaffoldStore(Protocol):
    """Port for ensuring a bot config scaffold exists before runtime polling begins."""

    def ensure(self, bot_id: str) -> tuple[Path, bool]:
        """Ensure a config scaffold exists and return its path plus a created flag."""
