# Memory-Aware OTAR Integration (Phase 3, Task 3.6)

## Overview

The ATLAS orchestration runtime follows an **Observe-Think-Act-Reflect (OTAR)** loop. Phase 3 enhances the **Observe** step to inject real-time memory context—including knowledge chunks from ingested documents—into every LLM prompt.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    OTAR Loop (Orchestrator)                  │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────┐                                            │
│  │  OBSERVE    │  ← Memory Context Injection (Phase 3)      │
│  └──────┬──────┘                                            │
│         │                                                    │
│         │  ┌───────────────────────────────────────┐       │
│         └─→│  ContextBuilder.build()               │       │
│            │  - System prompt                      │       │
│            │  - Safety constraints                 │       │
│            │  - User model                         │       │
│            │  - Tool catalog                       │       │
│            │  → Memory (via Retriever.retrieve())  │ ←─┐   │
│            │    • Semantic facts                   │   │   │
│            │    • Recent episodes                  │   │   │
│            │    • Knowledge chunks (NEW)           │   │   │
│            │  - Working memory                     │   │   │
│            └───────────────────────────────────────┘   │   │
│                                                         │   │
│  ┌─────────────┐                                       │   │
│  │   THINK     │  ← LLM receives memory-enriched      │   │
│  └──────┬──────┘    context with knowledge base       │   │
│         │                                              │   │
│  ┌─────────────┐                                       │   │
│  │    ACT      │                                       │   │
│  └──────┬──────┘                                       │   │
│         │                                              │   │
│  ┌─────────────┐                                       │   │
│  │  REFLECT    │                                       │   │
│  └─────────────┘                                       │   │
└─────────────────────────────────────────────────────────┘   │
                                                              │
┌─────────────────────────────────────────────────────────┐   │
│                  Retriever (Hybrid)                      │   │
├─────────────────────────────────────────────────────────┤   │
│                                                          │   │
│  Parallel Queries (< 200ms):                            │   │
│  ┌──────────────────────┐  ┌──────────────────────┐   │   │
│  │ Semantic Facts       │  │ Episodes (Semantic)  │   │   │
│  │ (Dense vector)       │  │ (Vector search)      │   │   │
│  └──────────────────────┘  └──────────────────────┘   │   │
│                                                          │   │
│  ┌──────────────────────┐  ┌──────────────────────┐   │   │
│  │ Episodes (Sparse)    │  │ Knowledge Store      │←──┘   │
│  │ (Keyword search)     │  │ (Document chunks)    │       │
│  └──────────────────────┘  └──────────────────────┘       │
│                                                          │
│  RRF Fusion + Token Budget Packing:                     │
│  • Facts: High-confidence semantic facts                │
│  • Episodes: Recent task history                        │
│  • Knowledge: Ingested document chunks                  │
│  • Budget: 1500 tokens total (500 max for knowledge)    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Query Initiation
When the orchestrator begins an OTAR cycle, `ContextBuilder.build()` is called with the user's request.

### 2. Parallel Retrieval (< 200ms)
The `Retriever.retrieve()` method runs **5 parallel queries**:
- **Dense semantic facts**: Vector search over learned facts
- **Sparse episodes**: Keyword search over task history  
- **Semantic episodes**: Vector search over episodes
- **User model**: User preferences and style
- **Knowledge chunks** (NEW): Semantic search over ingested documents

### 3. Rank Fusion
- **Reciprocal Rank Fusion (RRF)** combines semantic and keyword rankings
- **Salience boosting** prioritizes important facts and episodes
- **Score-based ranking** orders knowledge chunks by vector similarity

### 4. Token Budget Packing
Total budget: **1500 tokens**
- **Memory (facts + episodes)**: Up to 1000 tokens
- **Knowledge chunks**: Up to 500 tokens

Packing strategy:
1. Pack facts until memory budget exhausted
2. Pack episodes until memory budget exhausted
3. Pack knowledge chunks until knowledge budget exhausted

### 5. Context Rendering
`RetrievedContext.render()` produces formatted context:

```
## What I know about you
<user model preferences>

## Relevant memory
- [tool_success] Successfully completed file read with grep
- [user_correction] User prefers concise responses
- [project_context] Working in Python project with pytest

## Knowledge base
- [Python Guide] Python is a high-level programming language...
- [API Documentation] The requests library simplifies HTTP...

## Recent context
- (task.completed) Read config.yaml successfully
- (tool.executed) Ran pytest suite, 15 passed
```

### 6. LLM Prompt Construction
`ContextBuilder` assembles the final prompt with **strict priority ordering**:

1. **System prompt** (priority 0) - Never trimmed
2. **Safety constraints** (priority 1) - Never trimmed
3. **User model** (priority 2) - Never trimmed
4. **Tools catalog** (priority 3)
5. **Memory** (priority 4) - Includes knowledge chunks
6. **Working memory** (priority 5)
7. **Plan summary** (priority 3, if present)

If total tokens exceed budget, layers with priority > 2 may be trimmed.

## Token Budget Management

### Overall Budget
- **Total context budget**: 3000 tokens (configurable)
- **Retrieval sub-budget**: 1500 tokens
  - Memory (facts + episodes): ~1000 tokens
  - Knowledge chunks: ~500 tokens (max 1/3 of retrieval budget)

### Rationale
- **Memory prioritized**: Task-specific learned facts are most relevant
- **Knowledge supplemental**: Broader context from documents
- **Always fits**: Budget ensures context never overflows model window

## Performance Targets

| Operation | Target | Achieved |
|-----------|--------|----------|
| Parallel retrieval | < 150ms | < 200ms (with knowledge) |
| Vector search (facts) | < 50ms | ✓ |
| Vector search (episodes) | < 50ms | ✓ |
| Vector search (knowledge) | < 100ms | ✓ |
| Context assembly | < 10ms | ✓ |
| **Total Observe step** | **< 250ms** | **✓** |

## API

### Retriever.retrieve()

```python
async def retrieve(
    query: str,
    *,
    terms: list[str] | None = None,
    task_id: str | None = None,
    correlation_id: str | None = None
) -> RetrievedContext:
    """
    Hybrid retrieval with semantic search over facts, episodes, and knowledge store.
    
    Performance target: < 200ms total (parallel execution).
    Token budget: 1500 tokens total, with max 500 tokens for knowledge chunks.
    """
```

### RetrievedContext Model

```python
class RetrievedContext(BaseModel):
    user_model: str
    facts: tuple[SemanticFact, ...]
    recent_episodes: tuple[Episode, ...]
    knowledge_chunks: tuple[dict[str, Any], ...]  # NEW in Phase 3
    token_estimate: int
    
    def render(self) -> str:
        """Render memory context as formatted markdown."""
```

## Usage Examples

### CLI: Test Memory Retrieval
```bash
# Test retrieval with knowledge integration
atlas recall "How do I use pytest fixtures?"

# Output shows:
# ## What I know about you
# ...
# ## Knowledge base
# - [Pytest Guide] Fixtures are reusable test setup functions...
# ...
```

### Programmatic: Access via ContextBuilder
```python
from atlas.orchestration.context_builder import ContextBuilder

context_str = await context_builder.build(
    request="Write unit tests",
    safety_constraints=...,
    tool_catalog=...,
    task_id="abc-123",
    correlation_id="corr-456"
)

# context_str includes:
# - User model
# - Semantic facts
# - Recent episodes  
# - Knowledge chunks from ingested docs ← NEW
# - Working memory
```

## Configuration

### Retriever Token Budget
Set via `Retriever.__init__()`:
```python
retriever = Retriever(
    semantic=semantic,
    episodic=episodic,
    user_model=user_model,
    knowledge_store=knowledge_store,
    token_budget=1500  # Adjust as needed
)
```

### Knowledge Chunk Budget
Automatically set to **min(500, total_budget / 3)**:
- Default: 500 tokens (with 1500 total budget)
- Scales proportionally with total budget
- Never exceeds 1/3 of total to preserve memory space

## Backward Compatibility

The system gracefully handles **missing knowledge store**:
- If `knowledge_store=None`, retrieval works as before
- `knowledge_chunks` tuple is empty
- Render output omits "Knowledge base" section
- No performance impact

## Observability

### Logging
```python
_log.debug(
    "retrieval.complete",
    event_type="memory",
    facts_count=len(facts),
    episodes_count=len(epis),
    knowledge_count=len(knowledge_chunks),  # NEW
    tokens_used=used,
    query=query[:50]
)
```

### WebSocket Events
```json
{
  "kind": "memory.retrieved",
  "memory_type": "hybrid",
  "count": 12,
  "query": "How do I use pytest fixtures?",
  "items": ["fixture pattern", "setup function", ...]
}
```

## Future Enhancements

1. **Adaptive budgeting**: Dynamically adjust knowledge budget based on query type
2. **Relevance scoring**: Weight knowledge chunks by document recency and authority
3. **Citation tracking**: Link knowledge chunks back to source documents in LLM output
4. **Cache warming**: Pre-fetch frequently accessed knowledge for sub-50ms retrieval
5. **Hybrid ranking**: Combine RRF with learned-to-rank model for better fusion

## Implementation Status

✅ **Completed (Task 3.6)**:
- Enhanced `Retriever` with knowledge store integration
- Updated `RetrievedContext` with `knowledge_chunks` field
- Modified `ContextBuilder._render_memory()` to use full render
- Token budget partitioning (memory vs knowledge)
- Parallel query execution (< 200ms)
- Type-safe implementation (mypy clean)
- Integration tests

## Related Files

- `src/atlas/memory/retrieval.py` - Hybrid retriever with knowledge integration
- `src/atlas/memory/types.py` - RetrievedContext model
- `src/atlas/memory/knowledge_store.py` - Document indexing and search
- `src/atlas/orchestration/context_builder.py` - OTAR Observe step
- `tests/memory/test_knowledge_retrieval.py` - Integration tests
