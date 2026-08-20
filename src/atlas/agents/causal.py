"""Causal Reasoning Engine - Understanding cause-effect relationships and counterfactuals.

This implements state-of-the-art causal reasoning inspired by:
- Pearl's causal hierarchy (association, intervention, counterfactual)
- Structural causal models
- Counterfactual reasoning for decision making
- Causal discovery from observational data

Key features:
1. Causal graph construction from observations
2. Intervention reasoning ("What if we do X?")
3. Counterfactual analysis ("What if we had done Y instead?")
4. Causal explanation generation
5. Decision support with causal understanding
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.infra.ids import CorrelationId
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.causal")


class CausalRelationType(Enum):
    """Types of causal relationships."""
    DIRECT_CAUSE = "direct_cause"        # A directly causes B
    INDIRECT_CAUSE = "indirect_cause"    # A causes B through intermediaries
    CONTRIBUTING = "contributing"        # A contributes to B but is not sufficient
    NECESSARY = "necessary"              # B cannot happen without A
    SUFFICIENT = "sufficient"            # A alone can cause B
    ENABLING = "enabling"                # A enables B but doesn't cause it
    PREVENTING = "preventing"            # A prevents B


class InterventionType(Enum):
    """Types of interventions."""
    DO = "do"                            # Do-intervention (set variable)
    ENCOURAGE = "encourage"              # Increase probability
    DISCOURAGE = "discourage"            # Decrease probability
    REMOVE = "remove"                    # Remove cause
    ADD = "add"                          # Add new cause


@dataclass
class CausalVariable:
    """A variable in a causal model."""
    name: str
    description: str
    possible_values: list[Any]
    observed_value: Any | None = None
    is_exogenous: bool = False  # External/unexplained


@dataclass
class CausalRelation:
    """A causal relationship between variables."""
    cause: str
    effect: str
    relation_type: CausalRelationType
    strength: float  # 0-1, how strong the causal effect
    confidence: float  # 0-1, how confident in this relation
    mechanism: str  # Description of how cause leads to effect
    conditions: list[str] = field(default_factory=list)  # Enabling conditions


@dataclass
class CausalGraph:
    """A causal graph representing causal relationships."""
    variables: dict[str, CausalVariable]
    relations: list[CausalRelation]
    
    def get_causes(self, effect: str) -> list[CausalRelation]:
        """Get all direct causes of an effect."""
        return [r for r in self.relations if r.effect == effect]
    
    def get_effects(self, cause: str) -> list[CausalRelation]:
        """Get all direct effects of a cause."""
        return [r for r in self.relations if r.cause == cause]
    
    def get_ancestors(self, variable: str) -> set[str]:
        """Get all ancestor variables (transitive causes)."""
        ancestors = set()
        to_process = [variable]
        
        while to_process:
            current = to_process.pop()
            for rel in self.get_causes(current):
                if rel.cause not in ancestors:
                    ancestors.add(rel.cause)
                    to_process.append(rel.cause)
        
        return ancestors


@dataclass
class Intervention:
    """An intervention on a causal model."""
    intervention_type: InterventionType
    target_variable: str
    intervention_value: Any
    description: str


@dataclass
class Counterfactual:
    """A counterfactual scenario."""
    counterfactual_id: str
    premise: str  # The counterfactual premise
    factual_scenario: dict[str, Any]  # What actually happened
    counterfactual_scenario: dict[str, Any]  # What could have happened
    predicted_outcome: str  # Predicted outcome under counterfactual
    confidence: float
    reasoning: str


@dataclass
class CausalExplanation:
    """A causal explanation of an event."""
    explanation_id: str
    event: str
    causes: list[tuple[str, float]]  # (cause, contribution)
    mechanism: str  # How causes led to event
    counterfactuals: list[Counterfactual]
    confidence: float


class CausalReasoningEngine:
    """Advanced causal reasoning system."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        discovery_threshold: float = 0.6,
        max_causal_depth: int = 4,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._discovery_threshold = discovery_threshold
        self._max_depth = max_causal_depth
        
        # Causal model cache
        self._causal_graphs: dict[str, CausalGraph] = {}
        
        # Intervention history
        self._intervention_history: list[tuple[Intervention, dict[str, Any]]] = []
        
        # Statistics
        self._stats = {
            "graphs_built": 0,
            "interventions_analyzed": 0,
            "counterfactuals_generated": 0,
            "explanations_generated": 0,
        }

    async def build_causal_model(
        self,
        observations: list[dict[str, Any]],
        context: str = "",
    ) -> CausalGraph:
        """Build a causal model from observations.
        
        Uses LLM-guided causal discovery to identify:
        1. Relevant variables
        2. Causal relationships
        3. Relationship strengths
        """
        
        _log.info(
            "causal.building_model",
            event_type="causal",
            observations=len(observations),
        )
        
        # Step 1: Identify variables
        variables = await self._discover_variables(observations, context)
        
        # Step 2: Discover causal relations
        relations = await self._discover_relations(variables, observations, context)
        
        graph = CausalGraph(variables=variables, relations=relations)
        
        # Cache
        graph_id = self._ids.execution_id()
        self._causal_graphs[graph_id] = graph
        self._stats["graphs_built"] += 1
        
        return graph

    async def analyze_intervention(
        self,
        graph: CausalGraph,
        intervention: Intervention,
        current_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyze the effects of an intervention.
        
        Answers: "What would happen if we do X?"
        """
        
        self._stats["interventions_analyzed"] += 1
        
        # Build intervention prompt
        prompt = self._build_intervention_prompt(graph, intervention, current_state)
        
        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._intervention_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1500,
                temperature=0.4,
            )
        )
        
        result = self._parse_json(resp.text)
        
        # Record intervention
        self._intervention_history.append((intervention, result))
        
        return result

    async def generate_counterfactual(
        self,
        graph: CausalGraph,
        factual_event: dict[str, Any],
        counterfactual_premise: str,
    ) -> Counterfactual:
        """Generate a counterfactual scenario.
        
        Answers: "What would have happened if Y instead of X?"
        """
        
        self._stats["counterfactuals_generated"] += 1
        
        prompt = f"""Generate a counterfactual analysis:

CAUSAL MODEL:
Variables: {', '.join(graph.variables.keys())}
Relations: {chr(10).join(f'- {r.cause} -> {r.effect} ({r.relation_type.value})' for r in graph.relations[:10])}

WHAT ACTUALLY HAPPENED:
{json.dumps(factual_event, indent=2)}

COUNTERFACTUAL PREMISE:
{counterfactual_premise}

Predict what would have happened under the counterfactual.

Output JSON:
{{
  "counterfactual_scenario": {{"variable": "value"}},
  "predicted_outcome": "description",
  "reasoning": "explanation",
  "confidence": 0.0-1.0
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._counterfactual_system_prompt()),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1200,
                temperature=0.5,
            )
        )
        
        data = self._parse_json(resp.text)
        
        return Counterfactual(
            counterfactual_id=self._ids.execution_id(),
            premise=counterfactual_premise,
            factual_scenario=factual_event,
            counterfactual_scenario=data.get("counterfactual_scenario", {}),
            predicted_outcome=data.get("predicted_outcome", ""),
            confidence=data.get("confidence", 0.5),
            reasoning=data.get("reasoning", ""),
        )

    async def explain_event(
        self,
        graph: CausalGraph,
        event: str,
        context: dict[str, Any],
    ) -> CausalExplanation:
        """Generate a causal explanation for an event.
        
        Provides:
        1. Contributing causes with weights
        2. Mechanism of how causes led to event
        3. Counterfactual alternatives
        """
        
        self._stats["explanations_generated"] += 1
        
        # Identify causes
        causes = await self._identify_causes(graph, event, context)
        
        # Generate mechanism explanation
        mechanism = await self._explain_mechanism(graph, event, causes, context)
        
        # Generate counterfactuals
        counterfactuals = await self._generate_counterfactuals_for_event(
            graph, event, causes, context
        )
        
        # Calculate confidence
        confidence = sum(c[1] for c in causes) / len(causes) if causes else 0.5
        
        return CausalExplanation(
            explanation_id=self._ids.execution_id(),
            event=event,
            causes=causes,
            mechanism=mechanism,
            counterfactuals=counterfactuals,
            confidence=confidence,
        )

    async def suggest_interventions(
        self,
        graph: CausalGraph,
        desired_outcome: str,
        current_state: dict[str, Any],
    ) -> list[tuple[Intervention, float]]:
        """Suggest interventions to achieve a desired outcome.
        
        Uses causal understanding to identify:
        1. Most effective intervention points
        2. Expected effect sizes
        3. Potential side effects
        """
        
        prompt = f"""Suggest interventions to achieve a desired outcome:

CAUSAL MODEL:
Variables: {', '.join(graph.variables.keys())}
Relations: {chr(10).join(f'- {r.cause} -> {r.effect} (strength: {r.strength})' for r in graph.relations[:10])}

CURRENT STATE:
{json.dumps(current_state, indent=2)}

DESIRED OUTCOME:
{desired_outcome}

Suggest 3-5 interventions that would help achieve this outcome.
For each, specify:
- What to intervene on
- What value to set
- Expected effect
- Confidence in this intervention

Output JSON:
{{
  "interventions": [
    {{
      "target_variable": "name",
      "intervention_value": "value",
      "expected_effect": "description",
      "confidence": 0.0-1.0,
      "reasoning": "why this works"
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a causal reasoning expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1500,
                temperature=0.5,
            )
        )
        
        data = self._parse_json(resp.text)
        
        interventions = []
        for int_data in data.get("interventions", []):
            intervention = Intervention(
                intervention_type=InterventionType.DO,
                target_variable=int_data.get("target_variable", ""),
                intervention_value=int_data.get("intervention_value"),
                description=int_data.get("reasoning", ""),
            )
            confidence = int_data.get("confidence", 0.5)
            interventions.append((intervention, confidence))
        
        return interventions

    async def _discover_variables(
        self,
        observations: list[dict[str, Any]],
        context: str,
    ) -> dict[str, CausalVariable]:
        """Discover relevant variables from observations."""
        
        # Extract variable names from observations
        all_keys: set[str] = set()
        for obs in observations:
            all_keys.update(obs.keys())
        
        # Use LLM to identify relevant variables
        prompt = f"""Identify relevant causal variables from these observations:

CONTEXT: {context}

OBSERVATIONS:
{json.dumps(observations[:5], indent=2)}

For each variable, provide:
1. Name
2. Description of what it represents
3. Possible values

Output JSON:
{{
  "variables": [
    {{
      "name": "var_name",
      "description": "what it represents",
      "possible_values": ["value1", "value2"],
      "is_exogenous": true/false
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a causal discovery expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=1500,
                temperature=0.3,
            )
        )
        
        data = self._parse_json(resp.text)
        
        variables = {}
        for var_data in data.get("variables", []):
            name = var_data.get("name", "")
            if name:
                variables[name] = CausalVariable(
                    name=name,
                    description=var_data.get("description", ""),
                    possible_values=var_data.get("possible_values", []),
                    is_exogenous=var_data.get("is_exogenous", False),
                )
        
        # Add any missing keys from observations
        for key in all_keys:
            if key not in variables:
                variables[key] = CausalVariable(
                    name=key,
                    description=f"Variable {key}",
                    possible_values=[],
                )
        
        return variables

    async def _discover_relations(
        self,
        variables: dict[str, CausalVariable],
        observations: list[dict[str, Any]],
        context: str,
    ) -> list[CausalRelation]:
        """Discover causal relations between variables."""
        
        prompt = f"""Identify causal relationships between variables:

CONTEXT: {context}

VARIABLES:
{chr(10).join(f'- {name}: {var.description}' for name, var in variables.items())}

SAMPLE OBSERVATIONS:
{json.dumps(observations[:3], indent=2)}

Identify causal relationships. For each:
1. Cause variable
2. Effect variable
3. Relationship type (direct_cause, contributing, necessary, sufficient, preventing)
4. Strength (0.0-1.0)
5. Mechanism (how cause leads to effect)

Output JSON:
{{
  "relations": [
    {{
      "cause": "var1",
      "effect": "var2",
      "type": "direct_cause",
      "strength": 0.8,
      "confidence": 0.7,
      "mechanism": "explanation",
      "conditions": ["condition1"]
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a causal discovery expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=2000,
                temperature=0.4,
            )
        )
        
        data = self._parse_json(resp.text)
        
        relations = []
        for rel_data in data.get("relations", []):
            try:
                rel_type = CausalRelationType(rel_data.get("type", "direct_cause"))
            except ValueError:
                rel_type = CausalRelationType.DIRECT_CAUSE
            
            relations.append(CausalRelation(
                cause=rel_data.get("cause", ""),
                effect=rel_data.get("effect", ""),
                relation_type=rel_type,
                strength=rel_data.get("strength", 0.5),
                confidence=rel_data.get("confidence", 0.5),
                mechanism=rel_data.get("mechanism", ""),
                conditions=rel_data.get("conditions", []),
            ))
        
        # Filter by threshold
        relations = [r for r in relations if r.confidence >= self._discovery_threshold]
        
        return relations

    async def _identify_causes(
        self,
        graph: CausalGraph,
        event: str,
        context: dict[str, Any],
    ) -> list[tuple[str, float]]:
        """Identify causes for an event."""
        
        # Get direct causes from graph
        direct_causes = graph.get_causes(event)
        
        if not direct_causes:
            # Use LLM to infer causes
            prompt = f"""Identify causes for this event:

EVENT: {event}

CONTEXT: {json.dumps(context, indent=2)}

List contributing causes with their contribution strength (0.0-1.0).

Output JSON:
{{"causes": [{{"cause": "name", "contribution": 0.8}}]}}"""

            resp = await self._gw.infer(
                InferenceRequest(
                    correlation_id=CorrelationId(self._ids.execution_id()),
                    messages=[
                        Message(role=Role.SYSTEM, content="You are a causal analysis expert."),
                        Message(role=Role.USER, content=prompt),
                    ],
                    constraints=Constraints(prefer_local=True),
                    max_tokens=800,
                    temperature=0.4,
                )
            )
            
            data = self._parse_json(resp.text)
            return [(c["cause"], c["contribution"]) for c in data.get("causes", [])]
        
        return [(r.cause, r.strength) for r in direct_causes]

    async def _explain_mechanism(
        self,
        graph: CausalGraph,
        event: str,
        causes: list[tuple[str, float]],
        context: dict[str, Any],
    ) -> str:
        """Explain the mechanism of how causes led to event."""
        
        prompt = f"""Explain how these causes led to this event:

EVENT: {event}

CAUSES:
{chr(10).join(f'- {cause} (contribution: {contrib:.2f})' for cause, contrib in causes)}

CONTEXT:
{json.dumps(context, indent=2)}

Provide a clear, step-by-step explanation of the causal mechanism."""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a causal explanation expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=1000,
                temperature=0.5,
            )
        )
        
        return resp.text

    async def _generate_counterfactuals_for_event(
        self,
        graph: CausalGraph,
        event: str,
        causes: list[tuple[str, float]],
        context: dict[str, Any],
    ) -> list[Counterfactual]:
        """Generate counterfactuals for an event."""
        
        counterfactuals = []
        
        # Generate one counterfactual per major cause
        for cause, contribution in causes[:2]:
            if contribution > 0.5:
                cf_premise = f"If {cause} had not occurred"
                cf = await self.generate_counterfactual(
                    graph,
                    {**context, "event": event},
                    cf_premise,
                )
                counterfactuals.append(cf)
        
        return counterfactuals

    def _build_intervention_prompt(
        self,
        graph: CausalGraph,
        intervention: Intervention,
        current_state: dict[str, Any],
    ) -> str:
        """Build prompt for intervention analysis."""
        
        return f"""Analyze the effects of this intervention:

CAUSAL MODEL:
Variables: {', '.join(graph.variables.keys())}
Relations: {chr(10).join(f'- {r.cause} -> {r.effect}' for r in graph.relations[:10])}

CURRENT STATE:
{json.dumps(current_state, indent=2)}

INTERVENTION:
Type: {intervention.intervention_type.value}
Target: {intervention.target_variable}
Value: {intervention.intervention_value}
Description: {intervention.description}

Predict the effects of this intervention.

Output JSON:
{{
  "direct_effects": [{{"variable": "name", "change": "description"}}],
  "indirect_effects": [{{"variable": "name", "change": "description"}}],
  "overall_outcome": "summary",
  "confidence": 0.0-1.0,
  "side_effects": ["potential side effect"]
}}"""

    def _intervention_system_prompt(self) -> str:
        return """You are a causal reasoning expert. Analyze interventions by:
1. Identifying direct effects on the target
2. Tracing indirect effects through the causal graph
3. Considering potential side effects
4. Providing confidence estimates

Be thorough but practical."""

    def _counterfactual_system_prompt(self) -> str:
        return """You are a counterfactual reasoning expert. When analyzing counterfactuals:
1. Consider the causal structure
2. Identify what changes and what stays the same
3. Trace effects through the causal graph
4. Provide reasoned predictions

Be imaginative but grounded in causal logic."""

    def _parse_json(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from text."""
        
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get causal reasoning statistics."""
        
        return {
            **self._stats,
            "cached_graphs": len(self._causal_graphs),
            "intervention_history_size": len(self._intervention_history),
        }
