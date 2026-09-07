"""PyQt6 UI subsystem for EidosBot."""

from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.app import UIController
from eidos.ui.dialogs import ConfirmationDialog, prompt_confirmation_gui
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill
from eidos.ui.settings_dialog import SettingsDialog

__all__ = [
    "HUDOverlay",
    "StatusPill",
    "ConfirmationDialog",
    "prompt_confirmation_gui",
    "AllowlistManagerDialog",
    "SettingsDialog",
    "UIController",
]
