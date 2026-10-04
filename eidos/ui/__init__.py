"""PyQt6 UI subsystem for EidosBot."""

from eidos.ui.allowlist_dialog import AllowlistManagerDialog
from eidos.ui.app import UIController
from eidos.ui.chat import ChatHistoryWidget, MessageBubble, markdown_to_chat_html, open_chat_link
from eidos.ui.conversations_dialog import ConversationsDialog
from eidos.ui.dialogs import ConfirmationDialog, prompt_confirmation_gui
from eidos.ui.hud import HUDOverlay
from eidos.ui.pill import StatusPill
from eidos.ui.settings_dialog import SettingsDialog
from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE, apply_global_styles

__all__ = [
    "HUDOverlay",
    "StatusPill",
    "ChatHistoryWidget",
    "MessageBubble",
    "markdown_to_chat_html",
    "open_chat_link",
    "ConfirmationDialog",
    "prompt_confirmation_gui",
    "AllowlistManagerDialog",
    "ConversationsDialog",
    "SettingsDialog",
    "UIController",
    "GLOBAL_TOOLTIP_STYLE",
    "apply_global_styles",
]
