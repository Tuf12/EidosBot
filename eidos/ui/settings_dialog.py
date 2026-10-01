"""PyQt6 Settings Dialog for model configuration, API keys, and safety policies."""

import copy
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from eidos.memory.types import INTERACTION_STYLE_CHOICES
from eidos.memory.user_store import UserStore
from eidos.models.chutes_client import ChutesClient
from eidos.models.ollama_client import OllamaClient
from eidos.ui.profile_onboarding_dialog import ProfileOnboardingDialog
from eidos.voice.tts import KokoroTTS

logger = logging.getLogger(__name__)

CHUTES_VISION_PRESETS = [
    "Qwen/Qwen3.5-397B-A17B-TEE",
    "google/gemma-4-31B-turbo-TEE",
    "moonshotai/Kimi-K2.6-TEE",
    "Qwen/Qwen3.6-27B-TEE",
]

CHUTES_REASONING_PRESETS = [
    "moonshotai/Kimi-K2.6-TEE",
    "google/gemma-4-31B-turbo-TEE",
    "deepseek-ai/DeepSeek-V3.2-TEE",
    "Qwen/Qwen3-32B-TEE",
    "zai-org/GLM-5.1-TEE",
    "moonshotai/Kimi-K3-TEE",
]


class SettingsDialog(QDialog):
    """
    Comprehensive settings configuration window for EidosBot:
    - Provider preference (Local, Auto, Cloud)
    - Ollama models and connection testing
    - Chutes AI API Key, models, and connection testing
    - Safety policies and confirmation toggles
    - Display and HUD preferences
    """

    signal_settings_applied = pyqtSignal(dict)  # emitted when settings are saved
    signal_open_allowlist = pyqtSignal()

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        config_path: str = "config.yaml",
        user_store: Optional[UserStore] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = copy.deepcopy(config or {})
        self.config_path = config_path
        mem_path = self.config.get("memory", {}).get("user_profile_file")
        self.user_store = user_store or UserStore(storage_path=mem_path)
        self._init_ui()
        self.load_from_config()
        # Query installed local models asynchronously or on init
        self._refresh_ollama_models(silent=True)
        self._refresh_chutes_models(silent=True)

    def _init_ui(self):
        self.setWindowTitle("EidosBot Settings")
        self.setMinimumSize(700, 580)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Title Banner
        title_box = QHBoxLayout()
        title_label = QLabel("⚙️ <b>EidosBot System Settings</b>")
        title_label.setStyleSheet("font-size: 16px; color: #50fa7b;")
        title_box.addWidget(title_label)
        title_box.addStretch()
        main_layout.addLayout(title_box)

        # Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane { border: 1px solid #44475a; background-color: #21222c; border-radius: 6px; } "
            "QTabBar::tab { background-color: #282a36; color: #f8f8f2; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; } "
            "QTabBar::tab:selected { background-color: #44475a; color: #50fa7b; font-weight: bold; } "
        )

        self.tab_models = QWidget()
        self.tab_safety = QWidget()
        self.tab_voice = QWidget()
        self.tab_display = QWidget()
        self.tab_memory = QWidget()
        self.tab_telegram = QWidget()

        self._build_models_tab()
        self._build_safety_tab()
        self._build_voice_tab()
        self._build_display_tab()
        self._build_memory_tab()
        self._build_telegram_tab()

        self.tabs.addTab(self.tab_models, "🤖 Models & Cloud")
        self.tabs.addTab(self.tab_safety, "🛡️ Safety & Confirmations")
        self.tabs.addTab(self.tab_voice, "🎙️ Voice & Speech")
        self.tabs.addTab(self.tab_display, "🖥️ Display & HUD")
        self.tabs.addTab(self.tab_memory, "🧠 User Memory")
        self.tabs.addTab(self.tab_telegram, "📱 Telegram")

        main_layout.addWidget(self.tabs, stretch=1)

        # Bottom Action Bar
        bottom_box = QHBoxLayout()
        self.status_msg = QLabel("")
        self.status_msg.setStyleSheet("color: #8be9fd; font-size: 12px;")
        bottom_box.addWidget(self.status_msg, stretch=1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(
            "background-color: #44475a; color: #f8f8f2; padding: 8px 16px; border-radius: 4px;"
        )
        self.btn_cancel.clicked.connect(self.reject)
        bottom_box.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save & Apply")
        self.btn_save.setStyleSheet(
            "background-color: #50fa7b; color: #282a36; font-weight: bold; padding: 8px 20px; border-radius: 4px;"
        )
        self.btn_save.clicked.connect(self._on_save_clicked)
        bottom_box.addWidget(self.btn_save)

        main_layout.addLayout(bottom_box)

        from eidos.ui.styles import GLOBAL_TOOLTIP_STYLE

        self.setStyleSheet(
            "QDialog { background-color: #1e1f29; color: #f8f8f2; } "
            "QGroupBox { font-weight: bold; border: 1px solid #44475a; border-radius: 6px; margin-top: 10px; padding-top: 12px; } "
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; color: #8be9fd; } "
            "QLineEdit, QComboBox, QSpinBox { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; border-radius: 4px; padding: 6px; } "
            "QLineEdit:focus, QComboBox:focus { border: 1px solid #bd93f9; } "
            "QPushButton:hover { opacity: 0.9; }\n"
            f"{GLOBAL_TOOLTIP_STYLE}"
        )

    # -------------------------------------------------------------
    # TAB 1: MODELS & CLOUD
    # -------------------------------------------------------------
    def _build_models_tab(self):
        layout = QVBoxLayout(self.tab_models)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Provider Preference
        pref_group = QGroupBox("Execution Mode (Provider Preference)")
        pref_layout = QHBoxLayout(pref_group)
        pref_layout.addWidget(QLabel("Default Mode:"))

        self.combo_preference = QComboBox()
        self.combo_preference.addItems([
            "local - 100% Local GPU (Ollama, Zero API Cost)",
            "auto - Smart Escalation (Local-first, Chutes fallback)",
            "cloud - Force Cloud (Chutes AI Only)"
        ])
        pref_layout.addWidget(self.combo_preference, stretch=1)
        layout.addWidget(pref_group)

        # Local Ollama Group
        local_group = QGroupBox("Local Models (Ollama - RTX 5060 Ti)")
        local_form = QFormLayout(local_group)
        local_form.setSpacing(8)

        self.edit_ollama_url = QLineEdit("http://localhost:11434")
        local_form.addRow("Ollama Base URL:", self.edit_ollama_url)

        # Reasoning Model
        reasoning_box = QHBoxLayout()
        self.combo_local_reasoning = QComboBox()
        self.combo_local_reasoning.setEditable(True)
        reasoning_box.addWidget(self.combo_local_reasoning, stretch=1)
        self.btn_refresh_ollama = QPushButton("🔄 Refresh")
        self.btn_refresh_ollama.setStyleSheet("background-color: #282a36; color: #f8f8f2; padding: 5px 10px; border-radius: 4px;")
        self.btn_refresh_ollama.clicked.connect(lambda: self._refresh_ollama_models(silent=False))
        reasoning_box.addWidget(self.btn_refresh_ollama)
        local_form.addRow("Reasoning Model:", reasoning_box)

        # Vision Model
        self.combo_local_vision = QComboBox()
        self.combo_local_vision.setEditable(True)
        local_form.addRow("Vision Model:", self.combo_local_vision)

        # Test Ollama Button
        ollama_btn_box = QHBoxLayout()
        self.btn_test_ollama = QPushButton("⚡ Test Ollama Connection")
        self.btn_test_ollama.setStyleSheet("background-color: #6272a4; color: #f8f8f2; padding: 5px 12px; border-radius: 4px;")
        self.btn_test_ollama.clicked.connect(self._test_ollama_connection)
        self.lbl_ollama_status = QLabel("")
        self.lbl_ollama_status.setStyleSheet("font-size: 11px;")
        ollama_btn_box.addWidget(self.btn_test_ollama)
        ollama_btn_box.addWidget(self.lbl_ollama_status, stretch=1)
        local_form.addRow("", ollama_btn_box)

        layout.addWidget(local_group)

        # Cloud Chutes AI Group
        cloud_group = QGroupBox("Cloud Models (Chutes AI)")
        cloud_form = QFormLayout(cloud_group)
        cloud_form.setSpacing(8)

        # API Key with Show/Hide toggle
        key_box = QHBoxLayout()
        self.edit_chutes_key = QLineEdit()
        self.edit_chutes_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_chutes_key.setPlaceholderText("Enter your Chutes API key (or leave empty for local-only)")
        key_box.addWidget(self.edit_chutes_key, stretch=1)

        self.btn_toggle_key = QPushButton("👁️")
        self.btn_toggle_key.setToolTip("Show / Hide API Key")
        self.btn_toggle_key.setStyleSheet("background-color: #282a36; border: 1px solid #44475a; border-radius: 4px; padding: 5px 8px;")
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        key_box.addWidget(self.btn_toggle_key)
        cloud_form.addRow("Chutes API Key:", key_box)

        self.edit_chutes_url = QLineEdit("https://llm.chutes.ai/v1")
        cloud_form.addRow("Base URL:", self.edit_chutes_url)

        # Chutes Vision Model
        self.combo_chutes_vision = QComboBox()
        self.combo_chutes_vision.setEditable(True)
        self.combo_chutes_vision.addItems(CHUTES_VISION_PRESETS)
        cloud_form.addRow("Vision Model:", self.combo_chutes_vision)

        # Chutes Reasoning Model
        self.combo_chutes_reasoning = QComboBox()
        self.combo_chutes_reasoning.setEditable(True)
        self.combo_chutes_reasoning.addItems(CHUTES_REASONING_PRESETS)
        cloud_form.addRow("Reasoning Model:", self.combo_chutes_reasoning)

        # Test Chutes Button
        chutes_btn_box = QHBoxLayout()
        self.btn_test_chutes = QPushButton("⚡ Test Chutes Connection")
        self.btn_test_chutes.setStyleSheet("background-color: #bd93f9; color: #282a36; font-weight: bold; padding: 5px 12px; border-radius: 4px;")
        self.btn_test_chutes.clicked.connect(self._test_chutes_connection)
        self.lbl_chutes_status = QLabel("")
        self.lbl_chutes_status.setStyleSheet("font-size: 11px;")
        chutes_btn_box.addWidget(self.btn_test_chutes)
        chutes_btn_box.addWidget(self.lbl_chutes_status, stretch=1)
        cloud_form.addRow("", chutes_btn_box)

        layout.addWidget(cloud_group)
        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 2: SAFETY & CONFIRMATIONS
    # -------------------------------------------------------------
    def _build_safety_tab(self):
        layout = QVBoxLayout(self.tab_safety)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # Policy Group
        safety_group = QGroupBox("Action Confirmation Policies")
        safety_layout = QVBoxLayout(safety_group)
        safety_layout.setSpacing(10)

        self.chk_confirm_ui = QCheckBox("Require confirmation before Level 1 UI actions (mouse clicks & typing)")
        self.chk_confirm_ui.setStyleSheet("color: #f8f8f2; font-size: 13px;")
        safety_layout.addWidget(self.chk_confirm_ui)

        self.chk_confirm_shell = QCheckBox("Require confirmation before Level 2 Shell commands (Recommended: ON)")
        self.chk_confirm_shell.setStyleSheet("color: #f8f8f2; font-size: 13px;")
        safety_layout.addWidget(self.chk_confirm_shell)

        # Destructive explanation badge
        desc_badge = QLabel(
            "🔒 <b>Level 3 Destructive Commands:</b> Commands like <code>rm</code>, <code>dd</code>, "
            "<code>reboot</code>, and <code>sudo</code> <b>always</b> require one-time approval and "
            "cannot be bypassed or added to the allowlist."
        )
        desc_badge.setWordWrap(True)
        desc_badge.setStyleSheet(
            "background-color: #282a36; color: #ffb86c; border: 1px solid #44475a; "
            "border-radius: 4px; padding: 10px; font-size: 12px;"
        )
        safety_layout.addWidget(desc_badge)

        layout.addWidget(safety_group)

        # Allowlist Management launcher
        allowlist_group = QGroupBox("Command Allowlist")
        allow_layout = QVBoxLayout(allowlist_group)
        allow_label = QLabel(
            "Commands that you previously clicked 'Remember and allow' are stored in your allowlist. "
            "You can review or revoke their execution permissions at any time."
        )
        allow_label.setWordWrap(True)
        allow_label.setStyleSheet("color: #bfbfbf; font-size: 12px;")
        allow_layout.addWidget(allow_label)

        self.btn_open_allowlist = QPushButton("🛡️ Open Command Allowlist Manager")
        self.btn_open_allowlist.setStyleSheet(
            "background-color: #50fa7b; color: #282a36; font-weight: bold; padding: 8px 16px; border-radius: 4px;"
        )
        self.btn_open_allowlist.clicked.connect(self._on_open_allowlist_clicked)
        allow_layout.addWidget(self.btn_open_allowlist)
        layout.addWidget(allowlist_group)

        # Kill Switch Info
        kill_group = QGroupBox("Global Hardware Kill Switch")
        kill_layout = QVBoxLayout(kill_group)
        kill_info = QLabel(
            "Press <b>Ctrl + Alt + Esc</b> at any time to instantly kill all running tasks, "
            "terminate subprocesses, and halt the agent loop."
        )
        kill_info.setStyleSheet("color: #ff5555; font-size: 13px;")
        kill_layout.addWidget(kill_info)
        layout.addWidget(kill_group)

        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 3: VOICE & SPEECH
    # -------------------------------------------------------------
    def _build_voice_tab(self):
        layout = QVBoxLayout(self.tab_voice)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        voice_group = QGroupBox("Text-to-Speech (Kokoro-82M)")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(10)

        self.chk_voice_enabled = QCheckBox("Enable voice subsystem (push-to-talk + TTS playback)")
        self.chk_voice_enabled.setStyleSheet("color: #f8f8f2; font-size: 13px;")
        voice_layout.addWidget(self.chk_voice_enabled)

        self.chk_auto_speak = QCheckBox("Automatically speak chat responses (Recommended: ON)")
        self.chk_auto_speak.setStyleSheet("color: #f8f8f2; font-size: 13px;")
        voice_layout.addWidget(self.chk_auto_speak)

        self.chk_auto_submit = QCheckBox("Auto-send transcribed speech as the next chat turn (Recommended: ON)")
        self.chk_auto_submit.setStyleSheet("color: #f8f8f2; font-size: 13px;")
        voice_layout.addWidget(self.chk_auto_submit)

        voice_form = QFormLayout()
        voice_form.setSpacing(8)

        self.combo_tts_voice = QComboBox()
        self.combo_tts_voice.setEditable(True)
        self.combo_tts_voice.addItems([
            "bm_george", "bm_daniel", "bm_fable", "bm_lewis",
            "af_bella", "af_heart", "af_nicole", "af_sky",
            "am_adam", "am_echo", "am_eric", "am_michael",
        ])
        voice_form.addRow("TTS Voice:", self.combo_tts_voice)

        self.spin_tts_speed = QDoubleSpinBox()
        self.spin_tts_speed.setRange(0.5, 2.0)
        self.spin_tts_speed.setSingleStep(0.1)
        self.spin_tts_speed.setDecimals(1)
        voice_form.addRow("Speech Speed:", self.spin_tts_speed)

        voice_layout.addLayout(voice_form)

        tts_test_box = QHBoxLayout()
        self.btn_test_tts = QPushButton("🔊 Test Speech")
        self.btn_test_tts.setStyleSheet(
            "background-color: #6272a4; color: #f8f8f2; padding: 5px 12px; border-radius: 4px;"
        )
        self.btn_test_tts.clicked.connect(self._test_tts)
        self.lbl_tts_status = QLabel("")
        self.lbl_tts_status.setStyleSheet("font-size: 11px;")
        tts_test_box.addWidget(self.btn_test_tts)
        tts_test_box.addWidget(self.lbl_tts_status, stretch=1)
        voice_layout.addLayout(tts_test_box)

        voice_hint = QLabel(
            "When auto-speak is on, Eidos reads each chat reply aloud in short-then-longer clips "
            "so speech starts a moment after text appears and continues without a long buffer pause. "
            "Push-to-talk (pill microphone or Ctrl+Alt+Space) transcribes your speech and, with "
            "auto-send on, immediately starts the next turn."
        )
        voice_hint.setWordWrap(True)
        voice_hint.setStyleSheet(
            "background-color: #282a36; color: #bfbfbf; border: 1px solid #44475a; "
            "border-radius: 4px; padding: 10px; font-size: 12px;"
        )
        voice_layout.addWidget(voice_hint)

        layout.addWidget(voice_group)
        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 4: DISPLAY & HUD
    # -------------------------------------------------------------
    def _build_display_tab(self):
        layout = QVBoxLayout(self.tab_display)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        hud_group = QGroupBox("Transparent HUD Overlay")
        hud_form = QFormLayout(hud_group)
        hud_form.setSpacing(10)

        self.chk_hud_enabled = QCheckBox("Enable Click-Through HUD Overlay (Target reticles & alerts)")
        hud_form.addRow("", self.chk_hud_enabled)

        self.spin_reticle_ms = QSpinBox()
        self.spin_reticle_ms.setRange(50, 2000)
        self.spin_reticle_ms.setSingleStep(50)
        self.spin_reticle_ms.setSuffix(" ms")
        hud_form.addRow("Click Reticle Duration:", self.spin_reticle_ms)

        self.combo_pill_pos = QComboBox()
        self.combo_pill_pos.addItems(["top-right", "top-left", "bottom-right", "bottom-left"])
        hud_form.addRow("Default Status Pill Position:", self.combo_pill_pos)

        layout.addWidget(hud_group)
        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 5: USER PROFILE MEMORY
    # -------------------------------------------------------------
    def _build_memory_tab(self):
        layout = QVBoxLayout(self.tab_memory)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        intro = QLabel(
            "Atomic facts stored at <b>~/.eidos/user_profile.yaml</b> on this machine only. "
            "Eidos injects them into the system prompt; the agent can update them with your approval."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #bd93f9; font-size: 12px;")
        layout.addWidget(intro)

        form_group = QGroupBox("Profile Facts")
        form = QFormLayout(form_group)
        form.setSpacing(8)

        self.edit_profile_name = QLineEdit()
        form.addRow("Display name:", self.edit_profile_name)

        self.edit_profile_projects = QLineEdit()
        form.addRow("Projects directory:", self.edit_profile_projects)

        self.edit_profile_editor = QLineEdit()
        form.addRow("Primary editor:", self.edit_profile_editor)

        self.combo_profile_style = QComboBox()
        self.combo_profile_style.setEditable(True)
        self.combo_profile_style.addItems(list(INTERACTION_STYLE_CHOICES))
        form.addRow("Interaction style:", self.combo_profile_style)

        self.edit_profile_tools = QLineEdit()
        form.addRow("Tool preference:", self.edit_profile_tools)

        layout.addWidget(form_group)

        rules_group = QGroupBox("Workflow rules (one per line)")
        rules_layout = QVBoxLayout(rules_group)
        from PyQt6.QtWidgets import QTextEdit

        self.edit_profile_rules = QTextEdit()
        self.edit_profile_rules.setPlaceholderText("Always run pytest with -v before finishing code tasks")
        self.edit_profile_rules.setMaximumHeight(120)
        rules_layout.addWidget(self.edit_profile_rules)
        layout.addWidget(rules_group)

        self.lbl_profile_path = QLabel("")
        self.lbl_profile_path.setStyleSheet("color: #8be9fd; font-size: 11px;")
        layout.addWidget(self.lbl_profile_path)

        btn_row = QHBoxLayout()
        self.btn_run_onboarding = QPushButton("Run setup wizard…")
        self.btn_run_onboarding.setStyleSheet(
            "background-color: #6272a4; color: #f8f8f2; padding: 6px 12px; border-radius: 4px;"
        )
        self.btn_run_onboarding.clicked.connect(self._open_profile_wizard)
        btn_row.addWidget(self.btn_run_onboarding)

        self.btn_save_profile = QPushButton("Save user profile")
        self.btn_save_profile.setStyleSheet(
            "background-color: #50fa7b; color: #282a36; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_save_profile.clicked.connect(self._save_user_profile_from_tab)
        btn_row.addWidget(self.btn_save_profile)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

    def _load_user_profile_tab(self):
        profile = self.user_store.load(force_reload=True)
        self.edit_profile_name.setText(str(profile.user_identity.get("name", "")))
        self.edit_profile_projects.setText(str(profile.environment_facts.get("projects_dir", "")))
        self.edit_profile_editor.setText(str(profile.environment_facts.get("primary_editor", "")))
        style = str(profile.preferences.get("interaction_style", ""))
        if style:
            idx = self.combo_profile_style.findText(style)
            if idx >= 0:
                self.combo_profile_style.setCurrentIndex(idx)
            else:
                self.combo_profile_style.setEditText(style)
        else:
            self.combo_profile_style.setCurrentIndex(0)
        self.edit_profile_tools.setText(str(profile.preferences.get("tool_preference", "")))
        self.edit_profile_rules.setPlainText("\n".join(profile.workflow_rules))
        self.lbl_profile_path.setText(f"File: {self.user_store.path}")

    def _save_user_profile_from_tab(self):
        try:
            rules = [
                line.strip()
                for line in self.edit_profile_rules.toPlainText().splitlines()
                if line.strip()
            ]
            profile = self.user_store.load(force_reload=True)
            profile.user_identity["name"] = self.edit_profile_name.text().strip()
            if self.edit_profile_projects.text().strip():
                profile.environment_facts["projects_dir"] = os.path.expanduser(
                    self.edit_profile_projects.text().strip()
                )
            if self.edit_profile_editor.text().strip():
                profile.environment_facts["primary_editor"] = self.edit_profile_editor.text().strip()
            if self.combo_profile_style.currentText().strip():
                profile.preferences["interaction_style"] = (
                    self.combo_profile_style.currentText().strip()
                )
            if self.edit_profile_tools.text().strip():
                profile.preferences["tool_preference"] = self.edit_profile_tools.text().strip()
            profile.workflow_rules = rules
            profile.onboarding_complete = True
            self.user_store.save(profile)
            self.status_msg.setText("User profile saved.")
            self.status_msg.setStyleSheet("color: #50fa7b; font-size: 12px;")
        except Exception as exc:
            QMessageBox.critical(self, "Profile Error", f"Could not save profile: {exc}")

    def _open_profile_wizard(self):
        dialog = ProfileOnboardingDialog(user_store=self.user_store, parent=self)
        if dialog.exec():
            self._load_user_profile_tab()

    # -------------------------------------------------------------
    # TAB 6: TELEGRAM GATEWAY
    # -------------------------------------------------------------
    def _build_telegram_tab(self):
        layout = QVBoxLayout(self.tab_telegram)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Overview / Guide Box
        guide_box = QLabel(
            "📱 <b>Telegram Remote Gateway & Human-in-the-Loop</b><br>"
            "Control EidosBot from your mobile phone, receive task summaries, and approve Level 2/3 host commands "
            "remotely using interactive inline buttons (<code>[Approve]</code> / <code>[Reject]</code>).<br><br>"
            "<b>Setup Guide:</b><br>"
            "1. Search <b>@BotFather</b> in Telegram, send <code>/newbot</code>, and copy your <b>Bot Token</b>.<br>"
            "2. Search <b>@userinfobot</b> in Telegram to find your numeric <b>User ID</b>.<br>"
            "3. Paste both below and click <b>⚡ Test Bot Token</b> to verify."
        )
        guide_box.setWordWrap(True)
        guide_box.setStyleSheet(
            "background-color: #282a36; color: #8be9fd; border: 1px solid #44475a; "
            "border-radius: 6px; padding: 12px; font-size: 12px;"
        )
        layout.addWidget(guide_box)

        # Credentials Group
        cred_group = QGroupBox("Bot Credentials & Access Control")
        cred_layout = QVBoxLayout(cred_group)
        cred_layout.setSpacing(10)

        self.chk_telegram_enabled = QCheckBox("Enable Telegram Remote Companion")
        self.chk_telegram_enabled.setStyleSheet("color: #50fa7b; font-weight: bold; font-size: 13px;")
        cred_layout.addWidget(self.chk_telegram_enabled)

        cred_form = QFormLayout()
        cred_form.setSpacing(10)

        # Bot API Token with show/hide
        token_box = QHBoxLayout()
        self.edit_telegram_token = QLineEdit()
        self.edit_telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_telegram_token.setPlaceholderText("e.g. 1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ")
        self.btn_toggle_tg_token = QPushButton("👁️")
        self.btn_toggle_tg_token.setFixedWidth(36)
        self.btn_toggle_tg_token.setStyleSheet("background-color: #44475a; color: #f8f8f2; border-radius: 4px;")
        self.btn_toggle_tg_token.clicked.connect(self._toggle_tg_token_visibility)
        token_box.addWidget(self.edit_telegram_token)
        token_box.addWidget(self.btn_toggle_tg_token)
        cred_form.addRow("Bot API Token:", token_box)

        # Allowed User IDs
        self.edit_telegram_user_ids = QLineEdit()
        self.edit_telegram_user_ids.setPlaceholderText("e.g. 123456789, 987654321")
        cred_form.addRow("Allowed User ID(s):", self.edit_telegram_user_ids)

        cred_hint = QLabel("🔒 <i>Strict Security: Messages from any user ID not in this list are completely ignored and blocked.</i>")
        cred_hint.setStyleSheet("color: #6272a4; font-size: 11px;")
        cred_form.addRow("", cred_hint)

        # Test Token Button
        test_btn_box = QHBoxLayout()
        self.btn_test_telegram = QPushButton("⚡ Test Bot Token")
        self.btn_test_telegram.setStyleSheet(
            "background-color: #50fa7b; color: #282a36; font-weight: bold; padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_test_telegram.clicked.connect(self._test_telegram_connection)
        self.lbl_telegram_status = QLabel("")
        self.lbl_telegram_status.setStyleSheet("font-size: 12px;")
        test_btn_box.addWidget(self.btn_test_telegram)
        test_btn_box.addWidget(self.lbl_telegram_status, stretch=1)
        cred_form.addRow("", test_btn_box)

        cred_layout.addLayout(cred_form)
        layout.addWidget(cred_group)

        # Remote Controls & Notifications Group
        ctrl_group = QGroupBox("Remote Controls & Notification Rules")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(8)

        self.chk_tg_remote_kill = QCheckBox("Enable Remote Kill Switch (/kill command & button)")
        self.chk_tg_remote_kill.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        ctrl_layout.addWidget(self.chk_tg_remote_kill)

        self.chk_tg_risk_approval = QCheckBox("Push interactive cards with [Approve] / [Reject] buttons for Level 2 & 3 commands")
        self.chk_tg_risk_approval.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        ctrl_layout.addWidget(self.chk_tg_risk_approval)

        self.chk_tg_job_milestones = QCheckBox("Push batch job milestone progress updates to your phone")
        self.chk_tg_job_milestones.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        ctrl_layout.addWidget(self.chk_tg_job_milestones)

        self.chk_tg_task_completed = QCheckBox("Push final summary notification when an autonomous task completes")
        self.chk_tg_task_completed.setStyleSheet("color: #f8f8f2; font-size: 12px;")
        ctrl_layout.addWidget(self.chk_tg_task_completed)

        layout.addWidget(ctrl_group)
        layout.addStretch()

    # -------------------------------------------------------------
    # CONFIG LOADING & SAVING
    # -------------------------------------------------------------
    def load_from_config(self):
        """Populate GUI widgets from config dictionary."""
        models_cfg = self.config.get("models", {})
        pref = models_cfg.get("provider_preference", "local").lower()
        if pref == "local":
            self.combo_preference.setCurrentIndex(0)
        elif pref == "auto":
            self.combo_preference.setCurrentIndex(1)
        elif pref == "cloud":
            self.combo_preference.setCurrentIndex(2)

        # Local Ollama
        local_cfg = models_cfg.get("local", {})
        self.edit_ollama_url.setText(local_cfg.get("base_url", "http://localhost:11434"))
        reasoning = local_cfg.get("reasoning_model", "batiai/gemma4-26b:iq4")
        vision = local_cfg.get("vision_model", "batiai/gemma4-12b:q6")
        self.combo_local_reasoning.setEditText(reasoning)
        self.combo_local_vision.setEditText(vision)

        # Cloud Chutes
        cloud_cfg = models_cfg.get("cloud", {})
        self.edit_chutes_key.setText(cloud_cfg.get("api_key", ""))
        self.edit_chutes_url.setText(cloud_cfg.get("base_url", "https://llm.chutes.ai/v1"))
        chutes_vision = cloud_cfg.get("vision_model", "Qwen/Qwen3.5-397B-A17B-TEE")
        chutes_reasoning = cloud_cfg.get("reasoning_model", "moonshotai/Kimi-K2.6-TEE")
        self.combo_chutes_vision.setEditText(chutes_vision)
        self.combo_chutes_reasoning.setEditText(chutes_reasoning)

        # Safety
        safety_cfg = self.config.get("safety", {})
        self.chk_confirm_ui.setChecked(bool(safety_cfg.get("confirm_level_1_ui", False)))
        self.chk_confirm_shell.setChecked(bool(safety_cfg.get("confirm_level_2_shell", True)))

        # Voice
        voice_cfg = self.config.get("voice", {})
        self.chk_voice_enabled.setChecked(bool(voice_cfg.get("enabled", True)))
        self.chk_auto_speak.setChecked(bool(voice_cfg.get("auto_speak_responses", True)))
        self.chk_auto_submit.setChecked(bool(voice_cfg.get("auto_submit_transcription", True)))
        self.combo_tts_voice.setEditText(voice_cfg.get("tts_voice", "bm_george"))
        self.spin_tts_speed.setValue(float(voice_cfg.get("tts_speed", 1.0)))

        # Display
        ui_cfg = self.config.get("ui", {})
        self.chk_hud_enabled.setChecked(bool(ui_cfg.get("hud_overlay_enabled", True)))
        self.spin_reticle_ms.setValue(int(ui_cfg.get("target_reticle_duration_ms", 250)))
        pill_pos = ui_cfg.get("status_pill", {}).get("position", "top-right")
        idx = self.combo_pill_pos.findText(pill_pos)
        if idx >= 0:
            self.combo_pill_pos.setCurrentIndex(idx)

        # Telegram Gateway
        gw_cfg = self.config.get("gateway", {})
        tg_cfg = gw_cfg.get("telegram", {})
        self.chk_telegram_enabled.setChecked(bool(tg_cfg.get("enabled", False)))
        self.edit_telegram_token.setText(tg_cfg.get("bot_token", ""))

        user_ids = tg_cfg.get("allowed_user_ids", [])
        if isinstance(user_ids, list):
            self.edit_telegram_user_ids.setText(", ".join(str(u) for u in user_ids))
        elif user_ids:
            self.edit_telegram_user_ids.setText(str(user_ids))
        else:
            self.edit_telegram_user_ids.setText("")

        self.chk_tg_remote_kill.setChecked(bool(tg_cfg.get("remote_kill_switch_enabled", True)))

        notifs = tg_cfg.get("notifications", {})
        self.chk_tg_risk_approval.setChecked(bool(notifs.get("on_risk_approval", True)))
        self.chk_tg_job_milestones.setChecked(bool(notifs.get("on_job_milestones", True)))
        self.chk_tg_task_completed.setChecked(bool(notifs.get("on_task_completed", True)))

        self._load_user_profile_tab()

    def extract_config(self) -> Dict[str, Any]:
        """Extract updated values from GUI widgets into config dictionary."""
        cfg = copy.deepcopy(self.config)

        # Provider preference
        pref_idx = self.combo_preference.currentIndex()
        pref = ["local", "auto", "cloud"][pref_idx]

        if "models" not in cfg:
            cfg["models"] = {}
        cfg["models"]["provider_preference"] = pref

        # Local Ollama
        if "local" not in cfg["models"]:
            cfg["models"]["local"] = {}
        cfg["models"]["local"]["base_url"] = self.edit_ollama_url.text().strip()
        cfg["models"]["local"]["reasoning_model"] = self.combo_local_reasoning.currentText().strip()
        cfg["models"]["local"]["vision_model"] = self.combo_local_vision.currentText().strip()

        # Cloud Chutes
        if "cloud" not in cfg["models"]:
            cfg["models"]["cloud"] = {}
        cfg["models"]["cloud"]["api_key"] = self.edit_chutes_key.text().strip()
        cfg["models"]["cloud"]["base_url"] = self.edit_chutes_url.text().strip()
        cfg["models"]["cloud"]["vision_model"] = self.combo_chutes_vision.currentText().strip()
        cfg["models"]["cloud"]["reasoning_model"] = self.combo_chutes_reasoning.currentText().strip()

        # Safety
        if "safety" not in cfg:
            cfg["safety"] = {}
        cfg["safety"]["confirm_level_1_ui"] = self.chk_confirm_ui.isChecked()
        cfg["safety"]["confirm_level_2_shell"] = self.chk_confirm_shell.isChecked()
        cfg["safety"]["confirm_level_3_destructive"] = True  # Hard-locked

        # Voice
        if "voice" not in cfg:
            cfg["voice"] = {}
        cfg["voice"]["enabled"] = self.chk_voice_enabled.isChecked()
        cfg["voice"]["auto_speak_responses"] = self.chk_auto_speak.isChecked()
        cfg["voice"]["auto_submit_transcription"] = self.chk_auto_submit.isChecked()
        cfg["voice"]["tts_voice"] = self.combo_tts_voice.currentText().strip()
        cfg["voice"]["tts_speed"] = float(self.spin_tts_speed.value())

        # UI
        if "ui" not in cfg:
            cfg["ui"] = {}
        cfg["ui"]["hud_overlay_enabled"] = self.chk_hud_enabled.isChecked()
        cfg["ui"]["target_reticle_duration_ms"] = self.spin_reticle_ms.value()
        if "status_pill" not in cfg["ui"]:
            cfg["ui"]["status_pill"] = {}
        cfg["ui"]["status_pill"]["position"] = self.combo_pill_pos.currentText()

        # Telegram Gateway
        if "gateway" not in cfg:
            cfg["gateway"] = {}
        if "telegram" not in cfg["gateway"]:
            cfg["gateway"]["telegram"] = {}

        tg_cfg = cfg["gateway"]["telegram"]
        tg_cfg["enabled"] = self.chk_telegram_enabled.isChecked()
        tg_cfg["bot_token"] = self.edit_telegram_token.text().strip()

        raw_uids = self.edit_telegram_user_ids.text().strip()
        parsed_uids = []
        if raw_uids:
            for part in raw_uids.replace(";", ",").split(","):
                part_clean = part.strip()
                if part_clean:
                    try:
                        parsed_uids.append(int(part_clean))
                    except ValueError:
                        parsed_uids.append(part_clean)
        tg_cfg["allowed_user_ids"] = parsed_uids
        tg_cfg["remote_kill_switch_enabled"] = self.chk_tg_remote_kill.isChecked()

        if "notifications" not in tg_cfg:
            tg_cfg["notifications"] = {}
        tg_cfg["notifications"]["on_risk_approval"] = self.chk_tg_risk_approval.isChecked()
        tg_cfg["notifications"]["on_job_milestones"] = self.chk_tg_job_milestones.isChecked()
        tg_cfg["notifications"]["on_task_completed"] = self.chk_tg_task_completed.isChecked()

        return cfg

    def _on_save_clicked(self):
        """Save config to disk and emit signal."""
        updated_cfg = self.extract_config()
        self.config = updated_cfg

        # Save to file
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(updated_cfg, f, default_flow_style=False, sort_keys=False)
            logger.info("Saved updated settings to %s", self.config_path)
            self.signal_settings_applied.emit(updated_cfg)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Settings", f"Failed to save settings: {str(e)}")

    def _on_open_allowlist_clicked(self):
        self.signal_open_allowlist.emit()

    # -------------------------------------------------------------
    # HELPER ACTIONS: SHOW/HIDE KEY, TEST CONNECTIONS
    # -------------------------------------------------------------
    def _toggle_key_visibility(self):
        if self.edit_chutes_key.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_chutes_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_key.setText("🔒")
        else:
            self.edit_chutes_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_key.setText("👁️")

    def _refresh_ollama_models(self, silent: bool = False):
        """Fetch model names from local Ollama and populate dropdowns."""
        base_url = self.edit_ollama_url.text().strip()
        client = OllamaClient(base_url=base_url)
        models = client.list_models()

        if models:
            curr_reasoning = self.combo_local_reasoning.currentText()
            curr_vision = self.combo_local_vision.currentText()

            self.combo_local_reasoning.clear()
            self.combo_local_vision.clear()

            self.combo_local_reasoning.addItems(models)
            self.combo_local_vision.addItems(models)

            if curr_reasoning:
                self.combo_local_reasoning.setEditText(curr_reasoning)
            if curr_vision:
                self.combo_local_vision.setEditText(curr_vision)

            self.lbl_ollama_status.setText(f"✅ Found {len(models)} local models")
            self.lbl_ollama_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            if not silent:
                self.lbl_ollama_status.setText("❌ No models or Ollama offline")
                self.lbl_ollama_status.setStyleSheet("color: #ff5555; font-size: 11px;")

    def _test_ollama_connection(self):
        """Test local Ollama availability."""
        base_url = self.edit_ollama_url.text().strip()
        client = OllamaClient(base_url=base_url)
        if client.check_connection():
            self._refresh_ollama_models(silent=True)
            self.lbl_ollama_status.setText("✅ Ollama is running & reachable")
            self.lbl_ollama_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            self.lbl_ollama_status.setText("❌ Cannot connect to Ollama server")
            self.lbl_ollama_status.setStyleSheet("color: #ff5555; font-size: 11px;")

    def _refresh_chutes_models(self, silent: bool = False):
        """Fetch model names from Chutes AI and populate dropdowns."""
        api_key = self.edit_chutes_key.text().strip()
        base_url = self.edit_chutes_url.text().strip()
        if not api_key:
            return

        client = ChutesClient(api_key=api_key, base_url=base_url)
        models = client.list_models()
        if models:
            curr_reasoning = self.combo_chutes_reasoning.currentText().strip()
            curr_vision = self.combo_chutes_vision.currentText().strip()

            self.combo_chutes_reasoning.clear()
            self.combo_chutes_vision.clear()

            self.combo_chutes_reasoning.addItems(models)
            self.combo_chutes_vision.addItems(models)

            if curr_reasoning:
                self.combo_chutes_reasoning.setEditText(curr_reasoning)
            if curr_vision:
                self.combo_chutes_vision.setEditText(curr_vision)

            self.lbl_chutes_status.setText(f"✅ Loaded {len(models)} Chutes models")
            self.lbl_chutes_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            if not silent:
                self.lbl_chutes_status.setText("❌ Failed to fetch Chutes models")
                self.lbl_chutes_status.setStyleSheet("color: #ff5555; font-size: 11px;")

    def _test_chutes_connection(self):
        """Test Chutes AI authentication and refresh models list."""
        api_key = self.edit_chutes_key.text().strip()
        base_url = self.edit_chutes_url.text().strip()

        if not api_key:
            self.lbl_chutes_status.setText("⚠️ Please enter a Chutes API key first")
            self.lbl_chutes_status.setStyleSheet("color: #ffb86c; font-size: 11px;")
            return

        client = ChutesClient(api_key=api_key, base_url=base_url)
        if client.check_connection():
            self._refresh_chutes_models(silent=True)
            self.lbl_chutes_status.setText("✅ Chutes API key verified & authenticated!")
            self.lbl_chutes_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            self.lbl_chutes_status.setText("❌ Authentication failed or invalid key")
            self.lbl_chutes_status.setStyleSheet("color: #ff5555; font-size: 11px;")

    def _test_tts(self):
        """Test Kokoro TTS playback with current voice settings."""
        voice_cfg = self.config.get("voice", {})
        tts = KokoroTTS(
            python_path=voice_cfg.get("python_path"),
            voice_dir=voice_cfg.get("optimalx_voice_dir"),
            default_voice=self.combo_tts_voice.currentText().strip() or "bm_george",
            default_speed=float(self.spin_tts_speed.value()),
            device=voice_cfg.get("device", "cpu"),
        )
        if not tts.is_available:
            self.lbl_tts_status.setText("❌ Kokoro TTS not found (check python_path / optimalx_voice_dir in config.yaml)")
            self.lbl_tts_status.setStyleSheet("color: #ff5555; font-size: 11px;")
            return

        ok = tts.speak("Eidos voice test successful.", blocking=True)
        runtime = getattr(tts, "_voice_runtime", None)
        path_hint = ""
        if runtime and runtime.scripts_dir:
            path_hint = f" ({runtime.python_path})"
        if ok:
            self.lbl_tts_status.setText(f"✅ Speech played successfully{path_hint}")
            self.lbl_tts_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            self.lbl_tts_status.setText("❌ TTS synthesis or playback failed (check logs)")
            self.lbl_tts_status.setStyleSheet("color: #ff5555; font-size: 11px;")

    def _toggle_tg_token_visibility(self):
        if self.edit_telegram_token.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_telegram_token.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_tg_token.setText("🔒")
        else:
            self.edit_telegram_token.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_tg_token.setText("👁️")

    def _test_telegram_connection(self):
        """Test Telegram Bot Token via Telegram getMe API."""
        token = self.edit_telegram_token.text().strip()
        if not token:
            self.lbl_telegram_status.setText("⚠️ Please enter a Bot Token first")
            self.lbl_telegram_status.setStyleSheet("color: #ffb86c; font-size: 11px;")
            return

        self.lbl_telegram_status.setText("⏳ Testing bot token with Telegram API...")
        self.lbl_telegram_status.setStyleSheet("color: #8be9fd; font-size: 11px;")
        QApplication.processEvents()

        try:
            import requests
            resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                bot_user = data.get("result", {}).get("username", "UnknownBot")
                bot_name = data.get("result", {}).get("first_name", "Bot")
                self.lbl_telegram_status.setText(f"✅ Bot connected: @{bot_user} ({bot_name})")
                self.lbl_telegram_status.setStyleSheet("color: #50fa7b; font-size: 11px; font-weight: bold;")
            else:
                desc = data.get("description", "Unauthorized or invalid token")
                self.lbl_telegram_status.setText(f"❌ Verification failed: {desc}")
                self.lbl_telegram_status.setStyleSheet("color: #ff5555; font-size: 11px;")
        except Exception as e:
            self.lbl_telegram_status.setText(f"❌ Connection error: {str(e)[:60]}")
            self.lbl_telegram_status.setStyleSheet("color: #ff5555; font-size: 11px;")

