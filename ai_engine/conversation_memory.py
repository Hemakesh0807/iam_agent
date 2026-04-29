"""
Conversation memory for the IAM AI Assistant.

Stores up to MAX_TURNS turns in st.session_state.
Each turn records the instruction, parsed action, resolved entities,
execution outcome, and timestamp.

Context expiry: turns older than EXPIRY_MINUTES are automatically dropped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MAX_TURNS      = 10
EXPIRY_MINUTES = 30   # Turns older than this are dropped from context

SESSION_KEY = "iam_conversation_memory"


class Turn:
    """A single conversation turn."""

    def __init__(
        self,
        instruction:      str,
        action:           str,
        extracted:        dict[str, Any],
        resolved_entities: dict[str, Any],
        outcome:          dict[str, Any],
        status:           str,
    ):
        self.instruction       = instruction
        self.action            = action
        self.extracted         = extracted          # raw LLM extraction
        self.resolved_entities = resolved_entities  # after entity resolution
        self.outcome           = outcome            # result from dispatcher
        self.status            = status             # completed | failed | escalated
        self.timestamp         = datetime.utcnow()

    def is_expired(self) -> bool:
        return datetime.utcnow() - self.timestamp > timedelta(minutes=EXPIRY_MINUTES)

    def to_dict(self) -> dict:
        return {
            "instruction":       self.instruction,
            "action":            self.action,
            "extracted":         self.extracted,
            "resolved_entities": self.resolved_entities,
            "outcome":           self.outcome,
            "status":            self.status,
            "timestamp":         self.timestamp.isoformat(),
        }

    def summary(self) -> str:
        """One-line summary for LLM context injection."""
        ts  = self.timestamp.strftime("%H:%M")
        ent = self.resolved_entities
        user = ent.get("user_principal_name") or ent.get("display_name") or ""
        return f"[{ts}] {self.action}: {self.instruction[:80]} — status={self.status} user={user}"


class ConversationMemory:
    """
    Manages the conversation history for the IAM AI Assistant.
    Stored entirely in st.session_state — survives Streamlit reruns
    but is lost on page refresh (by design).

    Usage:
        memory = ConversationMemory.get()          # get or create from session
        memory.add_turn(...)                       # append a completed turn
        context = memory.build_context_string()   # inject into LLM prompt
        entity  = memory.last_user()              # get last mentioned user
    """

    def __init__(self):
        self._turns: list[Turn] = []

    # ── Persistence in session_state ──────────────────────────────────────────

    @classmethod
    def get(cls) -> "ConversationMemory":
        """
        Get the ConversationMemory from st.session_state.
        Creates a new one if it doesn't exist yet.
        Must be called after st.set_page_config().
        """
        import streamlit as st
        if SESSION_KEY not in st.session_state:
            st.session_state[SESSION_KEY] = cls()
        mem: ConversationMemory = st.session_state[SESSION_KEY]
        mem._evict_expired()
        return mem

    def _save(self) -> None:
        """Write self back into session_state (needed after mutation)."""
        import streamlit as st
        st.session_state[SESSION_KEY] = self

    # ── Turn management ───────────────────────────────────────────────────────

    def add_turn(
        self,
        instruction:       str,
        action:            str,
        extracted:         dict[str, Any],
        resolved_entities: dict[str, Any],
        outcome:           dict[str, Any],
        status:            str,
    ) -> None:
        turn = Turn(
            instruction=instruction,
            action=action,
            extracted=extracted,
            resolved_entities=resolved_entities,
            outcome=outcome,
            status=status,
        )
        self._turns.append(turn)
        if len(self._turns) > MAX_TURNS:
            self._turns = self._turns[-MAX_TURNS:]
        self._save()
        logger.info(
            "Memory: added turn %d action=%s status=%s",
            len(self._turns), action, status,
        )

    def _evict_expired(self) -> None:
        before = len(self._turns)
        self._turns = [t for t in self._turns if not t.is_expired()]
        evicted = before - len(self._turns)
        if evicted:
            logger.info("Memory: evicted %d expired turn(s).", evicted)
            self._save()

    def clear(self) -> None:
        self._turns = []
        self._save()

    @property
    def turns(self) -> list[Turn]:
        return list(self._turns)

    @property
    def count(self) -> int:
        return len(self._turns)

    # ── Entity resolution helpers ─────────────────────────────────────────────

    def last_user(self) -> dict[str, Any] | None:
        """
        Return the most recently mentioned user's resolved entity dict.
        Contains: user_id, user_principal_name, display_name (whatever was resolved).
        """
        for turn in reversed(self._turns):
            ent = turn.resolved_entities
            if ent.get("user_principal_name") or ent.get("user_id"):
                return ent
        return None

    def last_app(self) -> dict[str, Any] | None:
        """Return the most recently mentioned app's resolved entity dict."""
        for turn in reversed(self._turns):
            ent = turn.resolved_entities
            if ent.get("app_id") or ent.get("object_id"):
                return ent
        return None

    def find_user_by_name(self, name: str) -> dict[str, Any] | None:
        """
        Search conversation history for a user matching a given name or UPN fragment.
        Case-insensitive. Returns the most recently resolved match.
        """
        name_lower = name.lower()
        for turn in reversed(self._turns):
            ent = turn.resolved_entities
            upn  = (ent.get("user_principal_name") or "").lower()
            dname = (ent.get("display_name") or "").lower()
            if name_lower in upn or name_lower in dname:
                return ent
        return None

    # ── LLM context builder ───────────────────────────────────────────────────

    def build_context_string(self) -> str:
        """
        Build a concise context string to inject into the LLM prompt.
        Shows the last N turns as a numbered list.
        """
        if not self._turns:
            return "No previous context."
        lines = ["Recent conversation history (most recent last):"]
        for i, turn in enumerate(self._turns, start=1):
            lines.append(f"  {i}. {turn.summary()}")
        return "\n".join(lines)

    def get_all_known_users(self) -> list[dict[str, Any]]:
        """Return all uniquely resolved users from conversation history."""
        seen: dict[str, dict] = {}
        for turn in self._turns:
            ent = turn.resolved_entities
            upn = ent.get("user_principal_name")
            if upn and upn not in seen:
                seen[upn] = ent
        return list(seen.values())
    
