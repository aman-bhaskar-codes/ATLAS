"""Advanced Memory Consolidation with Knowledge Graphs - Structured memory with semantic links.

This implements state-of-the-art memory consolidation inspired by:
- Knowledge graph construction from episodic memories
- Semantic memory consolidation with entity extraction
- Temporal knowledge graphs for tracking changes
- Graph-based retrieval for contextual memory access

Key features:
1. Entity and relation extraction from episodes
2. Knowledge graph construction and maintenance
3. Temporal evolution tracking
4. Graph-based similarity and retrieval
5. Automated consolidation with confidence scoring
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.memory_graph")


class EntityType(Enum):
    """Types of entities in the knowledge graph."""
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    EVENT = "event"
    CONCEPT = "concept"
    TOOL = "tool"
    FILE = "file"
    PROJECT = "project"
    TASK = "task"
    SKILL = "skill"
    FACT = "fact"
    GOAL = "goal"


class RelationType(Enum):
    """Types of relations between entities."""
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    CAUSES = "causes"
    ENABLES = "enables"
    PREVENTS = "prevents"
    USES = "uses"
    CREATED_BY = "created_by"
    OWNED_BY = "owned_by"
    LOCATED_IN = "located_in"
    OCCURRED_AT = "occurred_at"
    SIMILAR_TO = "similar_to"
    OPPOSITE_OF = "opposite_of"
    PREREQUISITE = "prerequisite"
    SUBSEQUENT = "subsequent"


@dataclass
class Entity:
    """An entity in the knowledge graph."""
    entity_id: str
    name: str
    type: EntityType
    description: str
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    occurrence_count: int = 1
    source_episodes: list[str] = field(default_factory=list)


@dataclass
class Relation:
    """A relation between two entities."""
    relation_id: str
    source_entity: str  # entity_id
    target_entity: str  # entity_id
    relation_type: RelationType
    description: str
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    first_observed: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_observed: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence_episodes: list[str] = field(default_factory=list)


@dataclass
class TemporalFact:
    """A fact with temporal validity."""
    fact_id: str
    statement: str
    entities: list[str]
    valid_from: datetime
    valid_until: datetime | None
    confidence: float
    source_episode: str


@dataclass
class ConsolidatedKnowledge:
    """Result of memory consolidation."""
    entities: list[Entity]
    relations: list[Relation]
    temporal_facts: list[TemporalFact]
    confidence: float
    processing_time_ms: int


class MemoryGraphConsolidator:
    """Advanced memory consolidation with knowledge graphs."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        min_confidence: float = 0.7,
        consolidation_interval_hours: int = 6,
        max_entities_per_consolidation: int = 100,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._min_confidence = min_confidence
        self._interval = timedelta(hours=consolidation_interval_hours)
        self._max_entities = max_entities_per_consolidation
        
        # Knowledge graph
        self._entities: dict[str, Entity] = {}
        self._relations: dict[str, Relation] = {}
        self._temporal_facts: list[TemporalFact] = []
        
        # Episode tracking
        self._last_consolidation: datetime | None = None
        self._unconsolidated_episodes: list[str] = []
        
        # Statistics
        self._stats = {
            "consolidations_run": 0,
            "entities_created": 0,
            "relations_created": 0,
            "facts_extracted": 0,
            "avg_confidence": 0.0,
        }

    async def consolidate_episodes(
        self,
        episodes: list[dict[str, Any]],
        force: bool = False,
    ) -> ConsolidatedKnowledge:
        """Consolidate episodes into knowledge graph.
        
        Process:
        1. Extract entities and relations from episodes
        2. Merge with existing knowledge
        3. Update temporal facts
        4. Resolve conflicts
        5. Return consolidated knowledge
        """
        
        start_time = time.perf_counter()
        
        _log.info(
            "memory_graph.consolidation_started",
            event_type="memory",
            episodes=len(episodes),
        )
        
        # Step 1: Extract entities and relations from new episodes
        new_entities, new_relations, new_facts = await self._extract_from_episodes(episodes)
        
        # Step 2: Merge with existing knowledge
        merged_entities = self._merge_entities(new_entities)
        merged_relations = self._merge_relations(new_relations)
        
        # Step 3: Update temporal facts
        self._update_temporal_facts(new_facts)
        
        # Step 4: Resolve conflicts and update confidence
        merged_entities, merged_relations = await self._resolve_conflicts(
            merged_entities,
            merged_relations,
        )
        
        # Step 5: Prune low-confidence items
        self._prune_low_confidence()
        
        processing_time = int((time.perf_counter() - start_time) * 1000)
        
        result = ConsolidatedKnowledge(
            entities=list(merged_entities.values())[:self._max_entities],
            relations=list(merged_relations.values()),
            temporal_facts=self._temporal_facts,
            confidence=self._calculate_overall_confidence(),
            processing_time_ms=processing_time,
        )
        
        self._last_consolidation = self._clock.now()
        self._stats["consolidations_run"] += 1
        
        _log.info(
            "memory_graph.consolidation_completed",
            event_type="memory",
            entities=len(merged_entities),
            relations=len(merged_relations),
            facts=len(self._temporal_facts),
            time_ms=processing_time,
        )
        
        return result

    async def query_knowledge(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Query the knowledge graph using natural language."""
        
        # Convert query to graph traversal
        entities = await self._identify_relevant_entities(query)
        
        if not entities:
            return []
        
        # Get subgraph around relevant entities
        subgraph = self._get_subgraph(entities, max_hops=2)
        
        # Convert to readable format
        results = []
        for entity in subgraph["entities"].values():
            results.append({
                "entity": entity.name,
                "type": entity.type.value,
                "description": entity.description,
                "confidence": entity.confidence,
            })
        
        for relation in subgraph["relations"].values():
            source = self._entities.get(relation.source_entity)
            target = self._entities.get(relation.target_entity)
            if source and target:
                results.append({
                    "relation": f"{source.name} {relation.relation_type.value} {target.name}",
                    "description": relation.description,
                    "confidence": relation.confidence,
                })
        
        return results[:max_results]

    async def get_entity_timeline(
        self,
        entity_id: str,
    ) -> list[dict[str, Any]]:
        """Get temporal timeline for an entity."""
        
        entity = self._entities.get(entity_id)
        if not entity:
            return []
        
        timeline = []
        
        # Add entity creation
        timeline.append({
            "timestamp": entity.first_seen.isoformat(),
            "event": "entity_created",
            "description": f"Entity '{entity.name}' first observed",
        })
        
        # Add relations over time
        for relation in self._relations.values():
            if relation.source_entity == entity_id or relation.target_entity == entity_id:
                timeline.append({
                    "timestamp": relation.first_observed.isoformat(),
                    "event": "relation_created",
                    "description": f"Relation: {relation.description}",
                })
        
        # Add temporal facts
        for fact in self._temporal_facts:
            if entity_id in fact.entities:
                timeline.append({
                    "timestamp": fact.valid_from.isoformat(),
                    "event": "fact_valid",
                    "description": fact.statement,
                    "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                })
        
        # Sort by timestamp
        timeline.sort(key=lambda x: x["timestamp"])
        
        return timeline

    async def _extract_from_episodes(
        self,
        episodes: list[dict[str, Any]],
    ) -> tuple[dict[str, Entity], dict[str, Relation], list[TemporalFact]]:
        """Extract entities, relations, and facts from episodes."""
        
        all_entities = {}
        all_relations = {}
        all_facts = []
        
        # Process episodes in batches
        for episode in episodes:
            entities, relations, facts = await self._extract_from_single_episode(episode)
            
            # Merge
            all_entities = self._merge_dicts(all_entities, entities)
            all_relations = self._merge_dicts(all_relations, relations)
            all_facts.extend(facts)
        
        return all_entities, all_relations, all_facts

    async def _extract_from_single_episode(
        self,
        episode: dict[str, Any],
    ) -> tuple[dict[str, Entity], dict[str, Relation], list[TemporalFact]]:
        """Extract knowledge from a single episode."""
        
        content = episode.get("content", "")
        episode_id = episode.get("id", self._ids.execution_id())
        timestamp = episode.get("ts", self._clock.now().isoformat())
        
        if not content:
            return {}, {}, []
        
        prompt = f"""Extract entities, relations, and facts from this episode:

EPISODE: {content}
TIMESTAMP: {timestamp}

Extract:
1. Entities (people, organizations, concepts, tools, files, etc.)
2. Relations between entities
3. Factual statements with temporal validity

Output JSON:
{{
  "entities": [
    {{
      "name": "entity_name",
      "type": "person|organization|location|event|concept|tool|file|project|task|skill|fact|goal",
      "description": "what it is",
      "properties": {{}}
    }}
  ],
  "relations": [
    {{
      "source": "entity_name",
      "target": "entity_name",
      "type": "related_to|causes|enables|uses|created_by|part_of|...",
      "description": "how they relate"
    }}
  ],
  "facts": [
    {{
      "statement": "factual statement",
      "entities": ["entity1", "entity2"],
      "valid_until": "ISO date or null"
    }}
  ]
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                messages=[
                    Message(role=Role.SYSTEM, content="You are a knowledge extraction expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=2000,
                temperature=0.3,
            )
        )
        
        data = self._parse_json(resp.content)
        
        entities = {}
        relations = {}
        facts = []
        
        # Build entities
        for idx, ent_data in enumerate(data.get("entities", [])):
            try:
                ent_type = EntityType(ent_data.get("type", "concept"))
            except ValueError:
                ent_type = EntityType.CONCEPT
            
            entity_id = self._ids.execution_id()
            entity = Entity(
                entity_id=entity_id,
                name=ent_data.get("name", f"entity_{idx}"),
                type=ent_type,
                description=ent_data.get("description", ""),
                properties=ent_data.get("properties", {}),
                source_episodes=[episode_id],
            )
            entities[entity_id] = entity
        
        # Build relations (need to match entity names to IDs)
        name_to_id = {e.name: e.entity_id for e in entities.values()}
        
        for rel_data in data.get("relations", []):
            source_name = rel_data.get("source", "")
            target_name = rel_data.get("target", "")
            
            if source_name in name_to_id and target_name in name_to_id:
                try:
                    rel_type = RelationType(rel_data.get("type", "related_to"))
                except ValueError:
                    rel_type = RelationType.RELATED_TO
                
                relation = Relation(
                    relation_id=self._ids.execution_id(),
                    source_entity=name_to_id[source_name],
                    target_entity=name_to_id[target_name],
                    relation_type=rel_type,
                    description=rel_data.get("description", ""),
                    evidence_episodes=[episode_id],
                )
                relations[relation.relation_id] = relation
        
        # Build facts
        for fact_data in data.get("facts", []):
            fact = TemporalFact(
                fact_id=self._ids.execution_id(),
                statement=fact_data.get("statement", ""),
                entities=[name_to_id.get(e, "") for e in fact_data.get("entities", [])],
                valid_from=self._clock.now(),
                valid_until=None,  # Could parse from fact_data
                confidence=0.8,
                source_episode=episode_id,
            )
            facts.append(fact)
        
        return entities, relations, facts

    def _merge_entities(
        self,
        new_entities: dict[str, Entity],
    ) -> dict[str, Entity]:
        """Merge new entities with existing knowledge."""
        
        merged = dict(self._entities)
        
        for entity_id, new_entity in new_entities.items():
            # Check if similar entity exists
            existing = self._find_similar_entity(merged, new_entity)
            
            if existing:
                # Update existing
                existing.last_seen = new_entity.last_seen
                existing.occurrence_count += 1
                existing.source_episodes.extend(new_entity.source_episodes)
                existing.confidence = max(existing.confidence, new_entity.confidence)
                # Merge properties
                existing.properties.update(new_entity.properties)
            else:
                # Add new
                merged[entity_id] = new_entity
                self._stats["entities_created"] += 1
        
        self._entities = merged
        return merged

    def _merge_relations(
        self,
        new_relations: dict[str, Relation],
    ) -> dict[str, Relation]:
        """Merge new relations with existing knowledge."""
        
        merged = dict(self._relations)
        
        for relation_id, new_relation in new_relations.items():
            # Check if similar relation exists
            existing = self._find_similar_relation(merged, new_relation)
            
            if existing:
                # Update existing
                existing.last_observed = new_relation.last_observed
                existing.evidence_episodes.extend(new_relation.evidence_episodes)
                existing.confidence = max(existing.confidence, new_relation.confidence)
            else:
                # Add new
                merged[relation_id] = new_relation
                self._stats["relations_created"] += 1
        
        self._relations = merged
        return merged

    def _find_similar_entity(
        self,
        entities: dict[str, Entity],
        new_entity: Entity,
    ) -> Entity | None:
        """Find similar entity by name and type."""
        
        for entity in entities.values():
            if entity.type == new_entity.type:
                # Simple name matching
                if entity.name.lower() == new_entity.name.lower():
                    return entity
                # Could add fuzzy matching
        return None

    def _find_similar_relation(
        self,
        relations: dict[str, Relation],
        new_relation: Relation,
    ) -> Relation | None:
        """Find similar relation by source, target, and type."""
        
        for relation in relations.values():
            if (relation.source_entity == new_relation.source_entity and
                relation.target_entity == new_relation.target_entity and
                relation.relation_type == new_relation.relation_type):
                return relation
        return None

    def _merge_dicts(
        self,
        existing: dict,
        new_items: dict,
    ) -> dict:
        """Merge two dictionaries."""
        result = dict(existing)
        result.update(new_items)
        return result

    def _update_temporal_facts(
        self,
        new_facts: list[TemporalFact],
    ) -> None:
        """Update temporal facts with new information."""
        
        for fact in new_facts:
            # Check if similar fact exists
            existing = None
            for f in self._temporal_facts:
                if f.statement == fact.statement:
                    existing = f
                    break
            
            if existing:
                # Update validity
                if fact.valid_from < existing.valid_from:
                    existing.valid_from = fact.valid_from
                existing.confidence = max(existing.confidence, fact.confidence)
            else:
                self._temporal_facts.append(fact)
                self._stats["facts_extracted"] += 1

    async def _resolve_conflicts(
        self,
        entities: dict[str, Entity],
        relations: dict[str, Relation],
    ) -> tuple[dict[str, Entity], dict[str, Relation]]:
        """Resolve conflicts in the knowledge graph."""
        
        # For now, just return as-is
        # In the future, could use LLM to resolve contradictions
        return entities, relations

    def _prune_low_confidence(self) -> None:
        """Remove low-confidence items."""
        
        # Prune entities
        self._entities = {
            eid: ent for eid, ent in self._entities.items()
            if ent.confidence >= self._min_confidence
        }
        
        # Prune relations
        self._relations = {
            rid: rel for rid, rel in self._relations.items()
            if rel.confidence >= self._min_confidence
        }
        
        # Prune temporal facts
        self._temporal_facts = [
            f for f in self._temporal_facts
            if f.confidence >= self._min_confidence
        ]

    def _calculate_overall_confidence(self) -> float:
        """Calculate overall confidence of the knowledge graph."""
        
        if not self._entities and not self._relations:
            return 0.0
        
        all_confidences = [
            e.confidence for e in self._entities.values()
        ] + [
            r.confidence for r in self._relations.values()
        ]
        
        if not all_confidences:
            return 0.0
        
        return sum(all_confidences) / len(all_confidences)

    def _get_subgraph(
        self,
        entity_ids: list[str],
        max_hops: int = 2,
    ) -> dict[str, dict]:
        """Get subgraph around entities."""
        
        subgraph_entities = {}
        subgraph_relations = {}
        visited = set(entity_ids)
        frontier = set(entity_ids)
        
        for _ in range(max_hops):
            new_frontier = set()
            
            for eid in frontier:
                if eid in self._entities:
                    subgraph_entities[eid] = self._entities[eid]
                
                # Find connected relations
                for rid, relation in self._relations.items():
                    if relation.source_entity == eid or relation.target_entity == eid:
                        subgraph_relations[rid] = relation
                        
                        # Add connected entities to frontier
                        if relation.source_entity not in visited:
                            new_frontier.add(relation.source_entity)
                        if relation.target_entity not in visited:
                            new_frontier.add(relation.target_entity)
            
            visited.update(frontier)
            frontier = new_frontier
        
        return {
            "entities": subgraph_entities,
            "relations": subgraph_relations,
        }

    async def _identify_relevant_entities(
        self,
        query: str,
    ) -> list[str]:
        """Identify relevant entities for a query."""
        
        prompt = f"""Identify relevant entities from the knowledge graph for this query:

QUERY: {query}

AVAILABLE ENTITIES:
{chr(10).join(f'- {e.entity_id}: {e.name} ({e.type.value})' for e in list(self._entities.values())[:50])}

Return up to 5 entity IDs most relevant to the query.

Output JSON: {{"entity_ids": ["id1", "id2", ...]}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                messages=[
                    Message(role=Role.SYSTEM, content="You are a knowledge retrieval expert."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=500,
                temperature=0.2,
            )
        )
        
        data = self._parse_json(resp.content)
        return data.get("entity_ids", [])

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
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get memory graph statistics."""
        
        return {
            **self._stats,
            "total_entities": len(self._entities),
            "total_relations": len(self._relations),
            "total_temporal_facts": len(self._temporal_facts),
            "entities_by_type": {
                t.value: sum(1 for e in self._entities.values() if e.type == t)
                for t in EntityType
            },
            "relations_by_type": {
                t.value: sum(1 for r in self._relations.values() if r.relation_type == t)
                for t in RelationType
            },
        }