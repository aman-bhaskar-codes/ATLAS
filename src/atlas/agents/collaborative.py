"""Collaborative Multi-Agent Reasoning - Agents work together with consensus and debate.

This implements advanced multi-agent collaboration inspired by:
- Society of Mind (Minsky)
- Multi-agent debate and consensus mechanisms
- Collaborative reasoning with different perspectives
- Ensemble methods for improved robustness

Key features:
1. Multiple agents propose solutions from different perspectives
2. Debate mechanism for challenging and refining ideas
3. Consensus building with weighted voting
4. Specialized agents for different aspects (research, critique, synthesis)
5. Dynamic role assignment based on task requirements
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.collaborative")


class AgentRole(Enum):
    """Roles agents can play in collaborative reasoning."""
    PROPOSER = "proposer"            # Generate initial solutions
    CRITIC = "critic"                # Challenge assumptions and find flaws
    SYNTHESIZER = "synthesizer"      # Combine ideas into coherent solution
    VALIDATOR = "validator"          # Check correctness and completeness
    RESEARCHER = "researcher"        # Gather information
    PLANNER = "planner"             # Structure approach
    EXECUTOR = "executor"           # Focus on practical implementation
    EVALUATOR = "evaluator"         # Assess quality and trade-offs


@dataclass
class AgentPerspective:
    """A perspective from one agent in the collaboration."""
    agent_id: str
    role: AgentRole
    proposal: str
    confidence: float
    reasoning: str
    assumptions: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class DebateRound:
    """One round of debate between agents."""
    round_number: int
    proposals: list[AgentPerspective]
    critiques: list[tuple[str, str]]  # (agent_id, critique)
    refinements: list[AgentPerspective]
    consensus_score: float


@dataclass
class CollaborativeResult:
    """Result of collaborative reasoning."""
    final_solution: str
    confidence: float
    contributing_agents: list[str]
    debate_rounds: int
    consensus_achieved: bool
    minority_opinions: list[str]
    reasoning_trace: list[DebateRound]
    metadata: dict[str, Any] = field(default_factory=dict)


class CollaborativeReasoner:
    """Multi-agent collaborative reasoning engine."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        max_debate_rounds: int = 3,
        consensus_threshold: float = 0.75,
        min_agents: int = 3,
        max_agents: int = 7,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._max_rounds = max_debate_rounds
        self._consensus_threshold = consensus_threshold
        self._min_agents = min_agents
        self._max_agents = max_agents
        
        # Role prompts for different perspectives
        self._role_prompts = {
            AgentRole.PROPOSER: "You are a creative problem solver. Generate innovative solutions.",
            AgentRole.CRITIC: "You are a critical thinker. Find flaws and challenge assumptions.",
            AgentRole.SYNTHESIZER: "You are a synthesizer. Combine ideas into coherent solutions.",
            AgentRole.VALIDATOR: "You are a validator. Check correctness and completeness.",
            AgentRole.RESEARCHER: "You are a researcher. Gather relevant information.",
            AgentRole.PLANNER: "You are a strategic planner. Structure the approach.",
            AgentRole.EXECUTOR: "You are pragmatic. Focus on practical implementation.",
            AgentRole.EVALUATOR: "You are an evaluator. Assess quality and trade-offs.",
        }

    async def reason_collaboratively(
        self,
        task: str,
        context: str = "",
        required_roles: list[AgentRole] | None = None,
    ) -> CollaborativeResult:
        """Perform collaborative reasoning with multiple agents.
        
        Process:
        1. Select agent roles based on task
        2. Each agent proposes initial solution
        3. Agents critique each other's proposals
        4. Refine based on critiques
        5. Build consensus or continue debate
        6. Synthesize final solution
        """
        
        _log.info(
            "collaborative.started",
            event_type="reasoning",
            task=task[:100],
        )
        
        # Step 1: Select roles
        roles = required_roles or await self._select_roles(task)
        roles = roles[:self._max_agents]
        
        # Step 2: Initial proposals
        proposals = await self._generate_proposals(task, context, roles)
        
        # Step 3: Debate and refinement
        debate_history = []
        current_proposals = proposals
        
        for round_num in range(self._max_rounds):
            # Generate critiques
            critiques = await self._generate_critiques(task, current_proposals)
            
            # Check for consensus
            consensus_score = self._calculate_consensus(current_proposals)
            
            debate_round = DebateRound(
                round_number=round_num + 1,
                proposals=current_proposals,
                critiques=critiques,
                refinements=[],
                consensus_score=consensus_score,
            )
            debate_history.append(debate_round)
            
            if consensus_score >= self._consensus_threshold:
                _log.info(
                    "collaborative.consensus",
                    event_type="reasoning",
                    round=round_num + 1,
                    score=consensus_score,
                )
                break
            
            # Refine proposals based on critiques
            current_proposals = await self._refine_proposals(
                task,
                current_proposals,
                critiques,
            )
            debate_round.refinements = current_proposals
        
        # Step 4: Synthesize final solution
        final_solution = await self._synthesize_solution(
            task,
            current_proposals,
            debate_history,
        )
        
        # Extract minority opinions
        minority = self._extract_minority_opinions(current_proposals, final_solution)
        
        result = CollaborativeResult(
            final_solution=final_solution.proposal,
            confidence=final_solution.confidence,
            contributing_agents=[p.agent_id for p in current_proposals],
            debate_rounds=len(debate_history),
            consensus_achieved=consensus_score >= self._consensus_threshold,
            minority_opinions=minority,
            reasoning_trace=debate_history,
            metadata={
                "roles_used": [r.value for r in roles],
                "total_proposals": len(proposals),
                "final_consensus": consensus_score,
            },
        )
        
        _log.info(
            "collaborative.completed",
            event_type="reasoning",
            confidence=result.confidence,
            consensus=result.consensus_achieved,
            rounds=result.debate_rounds,
        )
        
        return result

    async def _select_roles(
        self,
        task: str,
    ) -> list[AgentRole]:
        """Select which agent roles are needed for this task."""
        
        prompt = f"""Analyze this task and select 3-5 agent roles needed:

TASK: {task}

AVAILABLE ROLES:
{chr(10).join(f'- {r.value}: {self._role_prompts[r][:50]}' for r in AgentRole)}

Select roles that provide diverse perspectives.
Output JSON: {{"roles": ["role1", "role2", ...]}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a team composition expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=300,
                temperature=0.3,
            )
        )
        
        data = self._parse_json(resp.text)
        role_names = data.get("roles", ["proposer", "critic", "synthesizer"])
        
        roles = []
        for name in role_names:
            try:
                roles.append(AgentRole(name))
            except ValueError:
                pass
        
        # Ensure minimum diversity
        if len(roles) < self._min_agents:
            defaults = [AgentRole.PROPOSER, AgentRole.CRITIC, AgentRole.SYNTHESIZER]
            for default_role in defaults:
                if default_role not in roles:
                    roles.append(default_role)
                if len(roles) >= self._min_agents:
                    break
        
        return roles

    async def _generate_proposals(
        self,
        task: str,
        context: str,
        roles: list[AgentRole],
    ) -> list[AgentPerspective]:
        """Generate initial proposals from each agent role."""
        
        tasks = [
            self._agent_propose(task, context, role, idx)
            for idx, role in enumerate(roles)
        ]
        
        proposals = await asyncio.gather(*tasks)
        return proposals

    async def _agent_propose(
        self,
        task: str,
        context: str,
        role: AgentRole,
        agent_idx: int,
    ) -> AgentPerspective:
        """One agent proposes a solution from their perspective."""
        
        role_context = self._role_prompts[role]
        
        prompt = f"""As a {role.value}, propose a solution to this task:

TASK: {task}

CONTEXT: {context}

YOUR ROLE: {role_context}

Provide:
1. Your proposed solution
2. Your reasoning
3. Key assumptions you're making
4. Potential concerns or risks

Output JSON:
{{
  "solution": "your solution",
  "reasoning": "your reasoning",
  "assumptions": ["assumption1", "assumption2"],
  "concerns": ["concern1", "concern2"],
  "confidence": 0.0-1.0
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=role_context),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1000,
                temperature=0.7,
            )
        )
        
        data = self._parse_json(resp.text)
        
        return AgentPerspective(
            agent_id=f"agent_{role.value}_{agent_idx}",
            role=role,
            proposal=data.get("solution", ""),
            confidence=data.get("confidence", 0.5),
            reasoning=data.get("reasoning", ""),
            assumptions=data.get("assumptions", []),
            concerns=data.get("concerns", []),
        )

    async def _generate_critiques(
        self,
        task: str,
        proposals: list[AgentPerspective],
    ) -> list[tuple[str, str]]:
        """Generate critiques of proposals from critic agents."""
        
        critiques = []
        
        # Each proposal gets critiqued by others
        for proposal in proposals:
            # Skip self-critique from non-critic roles
            other_proposals = [p for p in proposals if p.agent_id != proposal.agent_id]
            
            if not other_proposals:
                continue
            
            critique = await self._critique_proposal(task, proposal, other_proposals)
            critiques.append((proposal.agent_id, critique))
        
        return critiques

    async def _critique_proposal(
        self,
        task: str,
        proposal: AgentPerspective,
        other_proposals: list[AgentPerspective],
    ) -> str:
        """Generate a critique of one proposal."""
        
        others_text = "\n\n".join(
            f"Alternative from {p.agent_id} ({p.role.value}):\n{p.proposal[:300]}"
            for p in other_proposals[:3]
        )
        
        prompt = f"""Critique this proposal:

TASK: {task}

PROPOSAL from {proposal.agent_id} ({proposal.role.value}):
{proposal.proposal}

REASONING: {proposal.reasoning}

ASSUMPTIONS: {', '.join(proposal.assumptions)}

OTHER ALTERNATIVES:
{others_text}

Provide constructive critique:
1. What are the strengths?
2. What are the weaknesses or gaps?
3. What could be improved?
4. How does it compare to alternatives?"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a constructive critic."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=800,
                temperature=0.5,
            )
        )
        
        return resp.text

    async def _refine_proposals(
        self,
        task: str,
        proposals: list[AgentPerspective],
        critiques: list[tuple[str, str]],
    ) -> list[AgentPerspective]:
        """Refine proposals based on critiques."""
        
        # Create critique map
        critique_map = dict(critiques)
        
        # Refine each proposal
        tasks = [
            self._refine_single_proposal(task, p, critique_map.get(p.agent_id, ""))
            for p in proposals
        ]
        
        refined = await asyncio.gather(*tasks)
        return refined

    async def _refine_single_proposal(
        self,
        task: str,
        proposal: AgentPerspective,
        critique: str,
    ) -> AgentPerspective:
        """Refine one proposal based on critique."""
        
        if not critique:
            return proposal
        
        prompt = f"""Refine your proposal based on this critique:

TASK: {task}

YOUR ORIGINAL PROPOSAL:
{proposal.proposal}

CRITIQUE RECEIVED:
{critique}

Revise your proposal addressing the critique.
Keep strengths, improve weaknesses.

Output JSON:
{{
  "refined_solution": "improved solution",
  "reasoning": "what you changed and why",
  "confidence": 0.0-1.0
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._role_prompts[proposal.role]),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=1000,
                temperature=0.6,
            )
        )
        
        data = self._parse_json(resp.text)
        
        return AgentPerspective(
            agent_id=proposal.agent_id,
            role=proposal.role,
            proposal=data.get("refined_solution", proposal.proposal),
            confidence=data.get("confidence", proposal.confidence),
            reasoning=data.get("reasoning", proposal.reasoning),
            assumptions=proposal.assumptions,
            concerns=proposal.concerns,
        )

    async def _synthesize_solution(
        self,
        task: str,
        proposals: list[AgentPerspective],
        debate_history: list[DebateRound],
    ) -> AgentPerspective:
        """Synthesize final solution from all proposals."""
        
        proposals_text = "\n\n".join(
            f"From {p.agent_id} ({p.role.value}) - Confidence: {p.confidence:.2f}\n"
            f"{p.proposal}\n"
            f"Reasoning: {p.reasoning[:200]}"
            for p in proposals
        )
        
        prompt = f"""Synthesize a final solution from these agent proposals:

TASK: {task}

PROPOSALS:
{proposals_text}

DEBATE ROUNDS: {len(debate_history)}
CONSENSUS REACHED: {debate_history[-1].consensus_score >= self._consensus_threshold if debate_history else False}

Create a comprehensive solution that:
1. Incorporates the best ideas from all proposals
2. Resolves contradictions and conflicts
3. Addresses concerns raised during debate
4. Provides a clear, actionable solution

Output JSON:
{{
  "final_solution": "synthesized solution",
  "reasoning": "how you combined proposals",
  "confidence": 0.0-1.0,
  "key_insights": ["insight1", "insight2"]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content=self._role_prompts[AgentRole.SYNTHESIZER]),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=False),
                max_tokens=1500,
                temperature=0.4,
            )
        )
        
        data = self._parse_json(resp.text)
        
        return AgentPerspective(
            agent_id="synthesizer_final",
            role=AgentRole.SYNTHESIZER,
            proposal=data.get("final_solution", ""),
            confidence=data.get("confidence", 0.7),
            reasoning=data.get("reasoning", ""),
        )

    def _calculate_consensus(
        self,
        proposals: list[AgentPerspective],
    ) -> float:
        """Calculate consensus score among proposals."""
        
        if len(proposals) <= 1:
            return 1.0
        
        # Simple consensus: average of pairwise agreement weighted by confidence
        total_score = 0.0
        comparisons = 0
        
        for i, p1 in enumerate(proposals):
            for p2 in proposals[i + 1:]:
                # Similarity heuristic: length similarity and confidence
                len_sim = 1.0 - abs(len(p1.proposal) - len(p2.proposal)) / max(len(p1.proposal), len(p2.proposal), 1)
                confidence_product = p1.confidence * p2.confidence
                
                score = len_sim * confidence_product
                total_score += score
                comparisons += 1
        
        if comparisons == 0:
            return 1.0
        
        return total_score / comparisons

    def _extract_minority_opinions(
        self,
        proposals: list[AgentPerspective],
        final_solution: AgentPerspective,
    ) -> list[str]:
        """Extract minority opinions that differ significantly from final solution."""
        
        minority = []
        
        for proposal in proposals:
            # Check if significantly different (heuristic)
            if proposal.confidence < 0.5:
                continue
            
            # If concerns weren't addressed
            if proposal.concerns and len(proposal.concerns) > 2:
                minority.append(
                    f"{proposal.agent_id}: {', '.join(proposal.concerns[:2])}"
                )
        
        return minority

    def _parse_json(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from text with error handling."""
        
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}
