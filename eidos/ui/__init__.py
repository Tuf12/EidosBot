"""PyQt6 UI subsystem for EidosBot."""

from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.app import UIController
from eidos.ui.dialogs import ConfirmationDialog, prompt_confirmation_gui
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill

__all__ = [
    "HUDOverlay",
    "StatusPill",
    "ConfirmationDialog",
    "prompt_confirmation_gui",
    "AllowlistManagerDialog",
    "UIController",
]
