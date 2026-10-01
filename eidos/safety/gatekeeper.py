"""Safety Gatekeeper that intercepts all actions before execution."""

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from eidos.safety.allowlist import AllowlistManager
from eidos.safety.audit import AuditLogger
from eidos.safety.kill_switch import KillSwitch
from eidos.safety.risk_classifier import RiskClassifier, RiskLevel

logger = logging.getLogger(__name__)

PROFILE_MEMORY_ACTIONS = frozenset({
    "update_user_profile",
    "add_workflow_rule",
    "remove_workflow_rule",
    "remove_user_fact",
})


class Gatekeeper:
    """Evaluates actions against policies, allowlists, and user confirmations."""

    def __init__(
        self,
        allowlist: Optional[AllowlistManager] = None,
        audit_logger: Optional[AuditLogger] = None,
        kill_switch: Optional[KillSwitch] = None,
        confirm_level_1_ui: bool = False,
        confirm_level_2_shell: bool = True,
        confirm_profile_writes: bool = True,
    ):
        self.allowlist = allowlist or AllowlistManager()
        self.audit = audit_logger or AuditLogger()
        self.kill_switch = kill_switch or KillSwitch()
        self.confirm_level_1_ui = confirm_level_1_ui
        self.confirm_level_2_shell = confirm_level_2_shell
        self.confirm_profile_writes = confirm_profile_writes
        # Optional custom confirmation callback (e.g. for PyQt6 dialogs)
        # Signature: prompt_callback(action_type, details, risk_level, reason) -> Tuple[bool, bool]
        # returns (approved: bool, remember_allowlist: bool)
        self.custom_prompt_callback: Optional[Callable[[str, Dict[str, Any], RiskLevel, str], Tuple[bool, bool]]] = None

    def evaluate(self, action_type: str, details: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Evaluate if an action is permitted to run.
        Returns: (is_permitted: bool, reason: str)
        """
        if self.kill_switch.is_triggered():
            self.audit.log(action_type, details, int(RiskLevel.LEVEL_3_DESTRUCTIVE), "killed", "Kill switch is active")
            return False, "Action aborted: Global kill switch is active."

        risk_level, reason = RiskClassifier.classify_action(action_type, details)

        if action_type in PROFILE_MEMORY_ACTIONS:
            if not self.confirm_profile_writes:
                self.audit.log(action_type, details, int(risk_level), "auto_allowed", reason)
                return True, "Auto-allowed (profile memory write)"
            approved, _ = self._request_confirmation(
                action_type,
                details,
                risk_level,
                f"Save to local user profile (~/.eidos/user_profile.yaml): {reason}",
            )
            decision = "user_approved" if approved else "user_denied"
            self.audit.log(action_type, details, int(risk_level), decision, reason)
            return approved, "Approved by user" if approved else "Denied by user"

        # Level 0: Always allowed automatically
        if risk_level == RiskLevel.LEVEL_0_PASSIVE:
            self.audit.log(action_type, details, int(risk_level), "auto_allowed", reason)
            return True, "Auto-allowed (Passive/Read-only)"

        # Level 1: UI action
        if risk_level == RiskLevel.LEVEL_1_UI:
            if not self.confirm_level_1_ui:
                self.audit.log(action_type, details, int(risk_level), "auto_allowed", reason)
                return True, "Auto-allowed (UI Navigation)"
            # If confirmation configured for Level 1, prompt
            approved, _ = self._request_confirmation(action_type, details, risk_level, reason)
            decision = "user_approved" if approved else "user_denied"
            self.audit.log(action_type, details, int(risk_level), decision, reason)
            return approved, "Approved by user" if approved else "Denied by user"

        # Level 2: Shell / Modifications
        if risk_level == RiskLevel.LEVEL_2_SHELL:
            cmd = details.get("command", "")
            # Check allowlist
            if cmd and self.allowlist.is_allowed(cmd):
                self.allowlist.record_usage(cmd)
                self.audit.log(action_type, details, int(risk_level), "allowlisted", reason)
                return True, "Allowed via persistent allowlist"

            if not self.confirm_level_2_shell:
                self.audit.log(action_type, details, int(risk_level), "auto_allowed", reason)
                return True, "Auto-allowed by config policy"

            # Prompt user
            approved, remember = self._request_confirmation(action_type, details, risk_level, reason)
            if approved and remember and cmd:
                self.allowlist.add(cmd, description=f"Approved interactively ({reason})")
                self.allowlist.record_usage(cmd)
                decision = "allowlisted_by_user"
            elif approved:
                decision = "user_approved"
            else:
                decision = "user_denied"

            self.audit.log(action_type, details, int(risk_level), decision, reason)
            return approved, "Approved by user" if approved else "Denied by user"

        # Level 3: Destructive (Hard-locked)
        if risk_level == RiskLevel.LEVEL_3_DESTRUCTIVE:
            approved, _ = self._request_confirmation(
                action_type, details, risk_level, f"DESTRUCTIVE ACTION: {reason}"
            )
            decision = "user_approved_destructive" if approved else "user_denied_destructive"
            self.audit.log(action_type, details, int(risk_level), decision, reason)
            return approved, "Approved by user (Destructive)" if approved else "Denied by user"

        return False, "Unknown security classification"

    def _request_confirmation(
        self, action_type: str, details: Dict[str, Any], risk_level: RiskLevel, reason: str
    ) -> Tuple[bool, bool]:
        """Request user confirmation either via custom GUI callback or CLI stdin."""
        if self.custom_prompt_callback is not None:
            return self.custom_prompt_callback(action_type, details, risk_level, reason)

        # CLI fallback prompt
        print("\n" + "=" * 60)
        print(f"⚠️  EIDOSBOT SAFETY GATEKEEPER - ACTION CONFIRMATION NEEDED")
        print(f"Action:     {action_type}")
        print(f"Details:    {details}")
        print(f"Risk Level: Level {int(risk_level)} ({risk_level.name})")
        print(f"Reason:     {reason}")
        print("=" * 60)

        if risk_level == RiskLevel.LEVEL_3_DESTRUCTIVE:
            print("🚨 THIS IS A DESTRUCTIVE OPERATION. IT CANNOT BE ADDED TO ALLOWLIST.")
            choice = input("Authorize execution? [y/N]: ").strip().lower()
            return (choice == "y" or choice == "yes"), False

        if risk_level == RiskLevel.LEVEL_2_SHELL:
            print("Options:")
            print("  [y] Approve this execution once")
            print("  [a] Always allow this command in the future (add to allowlist)")
            print("  [n] Deny execution")
            choice = input("Your choice [y/a/N]: ").strip().lower()
            if choice == "a":
                return True, True
            if choice in ("y", "yes"):
                return True, False
            return False, False

        # Generic Level 1 prompt
        choice = input("Authorize action? [y/N]: ").strip().lower()
        return (choice in ("y", "yes")), False
