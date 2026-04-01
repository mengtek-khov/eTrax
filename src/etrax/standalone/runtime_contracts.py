from __future__ import annotations

"""Shared runtime typing contracts used by the standalone Telegram runtime."""

from pathlib import Path
from typing import Any, Protocol

from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartConfig,
    ForgetUserDataConfig,
    LoadCallbackConfig,
    LoadInlineButtonConfig,
    OpenMiniAppConfig,
    PaywayPaymentConfig,
    SendInlineButtonConfig,
    SendMessageConfig,
    SendPhotoConfig,
    ShareContactConfig,
    UserProfileStore,
)

RuntimeStepConfig = (
    SendMessageConfig
    | SendPhotoConfig
    | CartButtonConfig
    | CheckoutCartConfig
    | PaywayPaymentConfig
    | LoadCallbackConfig
    | LoadInlineButtonConfig
    | ShareContactConfig
    | SendInlineButtonConfig
    | OpenMiniAppConfig
    | ForgetUserDataConfig
)


class UserProfileLogStore(UserProfileStore, Protocol):
    """Port for reading and writing per-bot user profile snapshots."""


class BotProcessScaffoldStore(Protocol):
    """Port for ensuring a bot config scaffold exists before runtime polling begins."""

    def ensure(self, bot_id: str) -> tuple[Path, bool]:
        """Ensure a config scaffold exists and return its path plus a created flag."""
