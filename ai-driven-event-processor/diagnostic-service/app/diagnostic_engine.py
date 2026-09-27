"""
diagnostic_engine.py — GraphRAG-based diagnostic engine stub.

Houses DiagnosticEngine, which coordinates graph context retrieval,
prompt construction, and LLM invocation to produce IncidentPlaybooks.

Requirements:
    REQ-G — Retrieve dependency context for a failing component from the graph.
    REQ-H — Build a structured prompt and invoke the LLM.
    REQ-I — Persist and return the generated IncidentPlaybook.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.dependency_graph_client import DependencyGraphClient
from app.playbook_generator import IncidentPlaybook, PlaybookGenerator

logger = logging.getLogger(__name__)

LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:11434")  # Ollama default
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")


class DiagnosticEngine:
    """
    Orchestrates the GraphRAG diagnostic pipeline:
      1. Retrieve graph context  (REQ-G)
      2. Build structured prompt (REQ-H)
      3. Invoke LLM              (REQ-H)
      4. Generate playbook       (REQ-I)

    Class-diagram operations:
        retrieve_context(component_id) → dict
        build_prompt()                 → str
        invoke_llm()                   → str
    """

    def __init__(self) -> None:
        self._graph_client: DependencyGraphClient = DependencyGraphClient()
        self._playbook_generator: PlaybookGenerator = PlaybookGenerator()
        self._llm_api_url: str = LLM_API_URL
        self._llm_model: str = LLM_MODEL
        logger.info(
            "DiagnosticEngine initialised (llm_model=%s, api=%s)",
            self._llm_model,
            self._llm_api_url,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def retrieve_context(self, component_id: str) -> dict[str, Any]:
        """
        Retrieve dependency graph context for the given component.

        REQ-G: The system shall query the dependency graph for the failing
        component and return structured context including direct dependencies,
        transitive dependencies (up to 2 hops), and recent health signals.

        Args:
            component_id: Identifier of the component under investigation.

        Returns:
            A context dict containing the subgraph, node metadata, and
            recent metric anomalies relevant to *component_id*.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-G
        #   1. Call self._graph_client.query_dependencies(component_id)
        #   2. Enrich with recent metric anomalies (call forecasting-service?)
        #   3. Return structured context dict for prompt assembly
        raise NotImplementedError("TODO: implement retrieve_context — REQ-G")

    def build_prompt(
        self,
        component_id: str,
        context: dict[str, Any],
    ) -> str:
        """
        Assemble the LLM prompt from the retrieved context.

        REQ-H: The prompt shall include the component identifier, its
        dependency subgraph, recent anomaly signals, and an instruction
        to produce a ranked list of remediation steps in JSON format.

        Args:
            component_id: Component under investigation.
            context:      Output of :meth:`retrieve_context`.

        Returns:
            A fully assembled prompt string ready for LLM submission.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-H
        #   1. Load Jinja2 template from templates/diagnostic_prompt.j2
        #   2. Render with component_id, context["subgraph"], context["anomalies"]
        #   3. Return rendered string
        raise NotImplementedError("TODO: implement build_prompt — REQ-H")

    def invoke_llm(self, prompt: str) -> str:
        """
        Submit the prompt to the configured LLM and return the raw response.

        REQ-H: The system shall invoke the LLM API with the assembled prompt
        and return the raw text response for downstream parsing by
        PlaybookGenerator.

        Args:
            prompt: Assembled prompt string from :meth:`build_prompt`.

        Returns:
            Raw LLM response text.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-H
        #   1. POST to self._llm_api_url with model=self._llm_model, prompt=prompt
        #   2. Handle timeout / retry with exponential backoff
        #   3. Return response text
        raise NotImplementedError("TODO: implement invoke_llm — REQ-H")

    def run(self, component_id: str) -> IncidentPlaybook:
        """
        Execute the full diagnostic pipeline for *component_id*.

        REQ-G, REQ-H, REQ-I: Coordinates retrieve_context → build_prompt →
        invoke_llm → PlaybookGenerator.generate().

        Args:
            component_id: Component identifier passed from the API layer.

        Returns:
            A fully populated :class:`IncidentPlaybook`.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-G, REQ-H, REQ-I
        raise NotImplementedError("TODO: implement run — REQ-G, REQ-H, REQ-I")
