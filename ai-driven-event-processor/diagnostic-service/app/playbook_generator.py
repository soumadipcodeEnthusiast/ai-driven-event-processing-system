"""
playbook_generator.py — Incident playbook generation and rendering stub.

Houses IncidentPlaybook (data model) and PlaybookGenerator (service class).

Requirements:
    REQ-H — The system shall parse the LLM response into a structured playbook.
    REQ-I — The system shall persist and expose playbooks by ID.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ─── IncidentPlaybook ─────────────────────────────────────────────────────────

@dataclass
class IncidentPlaybook:
    """
    Represents a generated incident remediation playbook.

    Class-diagram attributes:
        playbook_id  — globally unique playbook identifier
        steps        — ordered list of remediation step dicts
        generated_at — UTC timestamp of generation
    """

    playbook_id: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def render(self) -> str:
        """
        Render the playbook as a human-readable Markdown string.

        REQ-I: The rendered output shall include the playbook ID, generation
        timestamp, and each step formatted with its rank, title, command,
        and expected outcome.

        Returns:
            Markdown-formatted playbook string.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-I
        #   1. Use Jinja2 or f-strings to format header with playbook_id and generated_at
        #   2. Iterate self.steps, format each as numbered Markdown section
        #   3. Return assembled string
        raise NotImplementedError("TODO: implement render — REQ-I")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of the playbook."""
        return {
            "playbook_id": self.playbook_id,
            "steps": self.steps,
            "generated_at": self.generated_at.isoformat(),
        }


# ─── PlaybookGenerator ────────────────────────────────────────────────────────

class PlaybookGenerator:
    """
    Parses LLM response text into an :class:`IncidentPlaybook` and persists it.

    Requirements:
        REQ-H — Parse LLM output into a structured step list.
        REQ-I — Assign a unique ID, persist, and expose for retrieval.
    """

    def __init__(self) -> None:
        # In-memory store (replace with a proper DB implementation)
        self._store: dict[str, IncidentPlaybook] = {}
        logger.info("PlaybookGenerator initialised (in-memory store)")

    def generate(self, component_id: str, llm_response: str) -> IncidentPlaybook:
        """
        Parse the raw LLM response and produce a persisted IncidentPlaybook.

        REQ-H: The system shall extract a ranked list of remediation steps
        from the LLM JSON response, each containing ``rank``, ``title``,
        ``command``, and ``expected_outcome``.

        REQ-I: The resulting playbook shall be persisted (linked to
        *component_id*) and retrievable by its generated ``playbook_id``.

        Args:
            component_id:  The component the playbook addresses.
            llm_response:  Raw text response from the LLM.

        Returns:
            A fully populated and persisted :class:`IncidentPlaybook`.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-H, REQ-I
        #   1. Parse llm_response as JSON (handle fallback plain-text parsing)
        #   2. Extract steps list from parsed JSON
        #   3. Generate playbook_id (e.g. f"pb-{component_id}-{uuid4().hex[:8]}")
        #   4. Instantiate IncidentPlaybook(playbook_id, steps)
        #   5. Persist to self._store[playbook_id]
        #   6. Return playbook
        raise NotImplementedError("TODO: implement generate — REQ-H, REQ-I")

    def get(self, playbook_id: str) -> IncidentPlaybook | None:
        """
        Retrieve a persisted playbook by its ID.

        REQ-I: Returns ``None`` if no playbook with *playbook_id* exists.

        Args:
            playbook_id: Unique playbook identifier.

        Returns:
            The :class:`IncidentPlaybook` if found, else ``None``.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-I
        raise NotImplementedError("TODO: implement get — REQ-I")
