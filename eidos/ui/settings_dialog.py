"""PyQt6 Settings Dialog for model configuration, API keys, and safety policies."""

import copy
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from eidos.models.chutes_client import ChutesClient
from eidos.models.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

CHUTES_VISION_PRESETS = [
    "Qwen/Qwen3.5-397B-A17B-TEE",
    "google/gemma-4-31B-turbo-TEE",
    "moonshotai/Kimi-K2.6-TEE",
    "Qwen/Qwen3.6-27B-TEE",
]

CHUTES_REASONING_PRESETS = [
    "moonshotai/Kimi-K2.6-TEE",
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
        parent=None,
    ):
        super().__init__(parent)
        self.config = copy.deepcopy(config or {})
        self.config_path = config_path
        self._init_ui()
        self.load_from_config()
        # Query installed local models asynchronously or on init
        self._refresh_ollama_models(silent=True)

    def _init_ui(self):
        self.setWindowTitle("EidosBot Settings")
        self.setMinimumSize(660, 560)
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
        self.tab_display = QWidget()

        self._build_models_tab()
        self._build_safety_tab()
        self._build_display_tab()

        self.tabs.addTab(self.tab_models, "🤖 Models & Cloud")
        self.tabs.addTab(self.tab_safety, "🛡️ Safety & Confirmations")
        self.tabs.addTab(self.tab_display, "🖥️ Display & HUD")

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

        self.setStyleSheet(
            "QDialog { background-color: #1e1f29; color: #f8f8f2; } "
            "QGroupBox { font-weight: bold; border: 1px solid #44475a; border-radius: 6px; margin-top: 10px; padding-top: 12px; } "
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; color: #8be9fd; } "
            "QLineEdit, QComboBox, QSpinBox { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; border-radius: 4px; padding: 6px; } "
            "QLineEdit:focus, QComboBox:focus { border: 1px solid #bd93f9; } "
            "QPushButton:hover { opacity: 0.9; }"
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
    # TAB 3: DISPLAY & HUD
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

        # Display
        ui_cfg = self.config.get("ui", {})
        self.chk_hud_enabled.setChecked(bool(ui_cfg.get("hud_overlay_enabled", True)))
        self.spin_reticle_ms.setValue(int(ui_cfg.get("target_reticle_duration_ms", 250)))
        pill_pos = ui_cfg.get("status_pill", {}).get("position", "top-right")
        idx = self.combo_pill_pos.findText(pill_pos)
        if idx >= 0:
            self.combo_pill_pos.setCurrentIndex(idx)

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

        # UI
        if "ui" not in cfg:
            cfg["ui"] = {}
        cfg["ui"]["hud_overlay_enabled"] = self.chk_hud_enabled.isChecked()
        cfg["ui"]["target_reticle_duration_ms"] = self.spin_reticle_ms.value()
        if "status_pill" not in cfg["ui"]:
            cfg["ui"]["status_pill"] = {}
        cfg["ui"]["status_pill"]["position"] = self.combo_pill_pos.currentText()

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

    def _test_chutes_connection(self):
        """Test Chutes AI authentication."""
        api_key = self.edit_chutes_key.text().strip()
        base_url = self.edit_chutes_url.text().strip()

        if not api_key:
            self.lbl_chutes_status.setText("⚠️ Please enter a Chutes API key first")
            self.lbl_chutes_status.setStyleSheet("color: #ffb86c; font-size: 11px;")
            return

        client = ChutesClient(api_key=api_key, base_url=base_url)
        if client.check_connection():
            self.lbl_chutes_status.setText("✅ Chutes API key verified & authenticated!")
            self.lbl_chutes_status.setStyleSheet("color: #50fa7b; font-size: 11px;")
        else:
            self.lbl_chutes_status.setText("❌ Authentication failed or invalid key")
            self.lbl_chutes_status.setStyleSheet("color: #ff5555; font-size: 11px;")
