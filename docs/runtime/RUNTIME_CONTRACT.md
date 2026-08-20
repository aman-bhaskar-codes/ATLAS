# ATLAS Runtime Contract

> **Purpose**: Define the exact contract for what "alive" means for the ATLAS runtime system.
> **Status**: Version 1.0
> **Last Updated**: 2026-08-20

## System States

The ATLAS runtime operates in a well-defined state machine:

```
BOOTING → INITIALIZING → DEGRADED → READY → BUSY → RECOVERING → SHUTTING_DOWN → FAILED
```

### State Definitions

| State | Description | User-Visible | Can Accept Tasks |
|-------|-------------|--------------|------------------|
| `BOOTING` | Runtime is starting core infrastructure | No | No |
| `INITIALIZING` | Components are being initialized | No | No |
| `DEGRADED` | Core works, some capabilities unavailable | Yes | Yes (with limitations) |
| `READY` | Full capability, accepting tasks | Yes | Yes |
| `BUSY` | Processing tasks, still accepting more | Yes | Yes |
| `RECOVERING` | Handling failure, limited operation | Yes | No |
| `SHUTTING_DOWN` | Graceful shutdown in progress | Yes | No |
| `FAILED` | Critical failure, cannot operate | No | No |

## Lifecycle Contract

### Startup Sequence

The runtime MUST follow this exact startup sequence:

1. **Load Configuration** (`BOOTING`)
   - Load settings from config files
   - Validate configuration completeness
   - Resolve environment variables
   - **Fail**: Invalid configuration → `FAILED`

2. **Initialize Infrastructure** (`INITIALIZING`)
   - Start database connection
   - Initialize message bus
   - Start ID generator, clock, metrics
   - **Fail**: Infrastructure failure → `FAILED`

3. **Initialize Safety** (`INITIALIZING`)
   - Load safety manifest
   - Initialize kill switch
   - Start audit log
   - **Fail**: Safety failure → `FAILED` (no degraded mode)

4. **Initialize Intelligence** (`INITIALIZING`)
   - Start model gateway
   - Initialize providers
   - Start embedder
   - **Fail**: Intelligence failure → `DEGRADED` (local-only mode)

5. **Initialize Memory** (`INITIALIZING`)
   - Start vector store
   - Initialize memory subsystems
   - Start embedding worker
   - **Fail**: Memory failure → `DEGRADED` (reduced context)

6. **Initialize Capabilities** (`INITIALIZING`)
   - Build capability registry
   - Start capability platforms
   - Initialize tool registry
   - **Fail**: Capability failure → `DEGRADED` (partial capabilities)

7. **Initialize Orchestration** (`INITIALIZING`)
   - Start orchestrator
   - Initialize planning/reasoning
   - Start background workers
   - **Fail**: Orchestration failure → `FAILED`

8. **Run Readiness Probes** (`INITIALIZING` → `READY`/`DEGRADED`)
   - Check all critical components
   - Verify end-to-end paths
   - **Fail**: Critical probe failure → `DEGRADED`

9. **READY** / **DEGRADED**
   - Emit runtime ready event
   - Start accepting tasks
   - Begin health monitoring

### Shutdown Sequence

The runtime MUST follow this exact shutdown sequence:

1. **Stop Accepting New Tasks** (`SHUTTING_DOWN`)
   - Signal task acceptance closed
   - Complete graceful task drain

2. **Finish Safe Current Work** (`SHUTTING_DOWN`)
   - Allow in-progress tasks to complete
   - Enforce shutdown timeout (30s default)

3. **Checkpoint Running Work** (`SHUTTING_DOWN`)
   - Save incomplete task state
   - Flush pending events
   - Persist memory updates

4. **Stop Background Workers** (`SHUTTING_DOWN`)
   - Signal worker shutdown
   - Wait for worker completion
   - Force kill after timeout

5. **Flush Event Bus** (`SHUTTING_DOWN`)
   - Process remaining events
   - Close event subscriptions
   - Flush event queue

6. **Close Providers** (`SHUTTING_DOWN`)
   - Close model connections
   - Stop embedder
   - Release resources

7. **Close Database** (`SHUTTING_DOWN`)
   - Flush database transactions
   - Close connections
   - Verify data integrity

8. **Final State** → `SHUTDOWN`

## Health Contract

### Health Endpoints

#### `GET /live`
**Purpose**: Process liveness check
**Response**: `{"alive": true/false}`
**Contract**: 
- Returns `true` if process is running
- No dependency checks
- Used by container orchestration

#### `GET /ready`
**Purpose**: Readiness check for task acceptance
**Response**: `{"ready": true/false, "state": "..."}`
**Contract**:
- Returns `true` only in `READY` or `DEGRADED` states
- Critical components must be healthy
- Used by load balancers and schedulers

#### `GET /health`
**Purpose**: Detailed component health
**Response**: Detailed health report
**Contract**:
- Returns health of all major components
- Includes latency, last success/failure
- Used for diagnostics

### Component Health Levels

Each component returns one of:

| Level | Description | System Impact |
|-------|-------------|---------------|
| `healthy` | Operating normally | None |
| `degraded` | Reduced capability | May affect features |
| `unavailable` | Not responding | Feature unavailable |
| `failed` | Critical failure | May trigger recovery |

### Critical Components

These components MUST be healthy for `READY` state:

- Database
- Safety Engine
- Orchestrator
- Configuration
- Model Gateway (at least one provider)

### Optional Components

These components MAY fail without preventing `READY` state:

- Browser Platform
- Cloud Providers
- External APIs
- Optional Capabilities

## Task Execution Contract

### Task Lifecycle

Every task MUST follow this state machine:

```
CREATED → QUEUED → CONTEXT → PLANNING → REASONING → WAITING_TOOL → WAITING_APPROVAL → 
EXECUTING → OBSERVING → VERIFYING → REPLANNING → COMPLETED/FAILED/CANCELLED
```

### Task Acceptance Contract

When a task is submitted:

1. **Immediate Acknowledgment** (< 100ms)
   - Return task ID immediately
   - Do NOT wait for execution
   - Return HTTP 202 Accepted

2. **State Persistence**
   - Persist task to database
   - Assign initial state `CREATED`
   - Emit `task.created` event

3. **Execution Start**
   - Transition to `QUEUED`
   - Schedule for execution
   - Emit `task.started` event

### Task Execution Contract

During execution:

1. **Every Operation Must Emit Events**
   - Tool requests: `tool.requested`
   - Safety decisions: `safety.decision`
   - Tool execution: `tool.executing`, `tool.completed`
   - Reasoning steps: `reasoning.step`
   - State changes: `task.state_changed`

2. **No Silent Failures**
   - Every failure must emit event
   - Every failure must be logged
   - Every failure must update task state

3. **Timeout Protection**
   - Every operation must have timeout
   - Timeout must trigger recovery
   - Recovery must be logged

4. **Cancellation Support**
   - Every step must check cancellation token
   - Cancellation must be graceful
   - Cancellation must be persisted

### Task Completion Contract

When a task completes:

1. **Final State Persistence**
   - Update task state to terminal
   - Persist final result
   - Record completion time

2. **Trajectory Storage**
   - Save complete execution trace
   - Record all actions/observations
   - Calculate cost

3. **Memory Updates**
   - Extract experiences
   - Update episodic memory
   - Trigger consolidation if needed

4. **Event Emission**
   - Emit `task.completed` or `task.failed`
   - Include final result/error
   - Trigger any automations

## Failure Recovery Contract

### Failure Classification

Failures are classified as:

| Type | Example | Recovery Strategy |
|------|---------|-------------------|
| `transient` | Network timeout | Retry with backoff |
| `recoverable` | Provider failure | Fallback to alternative |
| `permanent` | Invalid configuration | Fail fast, alert user |
| `catastrophic` | Database corruption | Emergency shutdown |

### Recovery Strategies

#### Transient Failures
- Retry with exponential backoff
- Max retry limit (default: 3)
- Emit retry events
- Log final success/failure

#### Recoverable Failures
- Attempt fallback provider
- Degrade capability gracefully
- Emit degradation event
- Update capability health

#### Permanent Failures
- Fail fast with clear error
- Emit failure event
- Alert user/operator
- Prevent retry loops

#### Catastrophic Failures
- Emergency shutdown
- Emit critical event
- Preserve state for analysis
- Require manual intervention

### Crash Recovery Contract

On process restart:

1. **Load Incomplete State**
   - Read incomplete tasks from database
   - Load pending approvals
   - Load unfinished events

2. **State Validation**
   - Verify state consistency
   - Check for orphaned operations
   - Validate checkpoint integrity

3. **Safe Recovery**
   - Resume only safe operations
   - Cancel unsafe operations
   - Emit recovery events

4. **User Notification**
   - Report recovered tasks
   - Report cancelled tasks
   - Report any data loss

## Performance Contract

### Latency Targets

| Operation | Target P50 | Target P95 | Target P99 |
|-----------|-----------|-----------|-----------|
| Task acknowledgment | 50ms | 100ms | 200ms |
| Capability lookup | 2ms | 5ms | 10ms |
| Event publication | 5ms | 10ms | 20ms |
| Memory retrieval | 10ms | 50ms | 100ms |
| Tool dispatch overhead | 10ms | 25ms | 50ms |
| API health check | 5ms | 10ms | 20ms |

### Resource Limits

| Resource | Limit | Action on Exceed |
|----------|-------|-----------------|
| Concurrent tasks | 10 | Queue new tasks |
| Task runtime | 30min | Cancel task |
| Memory per task | 1GB | Fail task |
| Events per second | 1000 | Rate limit |

### Monitoring Requirements

The runtime MUST track:

- Task throughput (tasks/minute)
- Task latency distribution
- Component health changes
- Error rates by component
- Resource utilization
- Queue depths
- Cache hit rates

## Security Contract

### Safety Enforcement

1. **No Bypasses**
   - Every tool execution MUST go through SafetyEngine
   - No debug modes that skip safety
   - No admin overrides

2. **Audit Trail**
   - Every safety decision MUST be audited
   - Audit chain MUST be verifiable
   - Audit records MUST be tamper-evident

3. **Kill Switch**
   - Global kill switch MUST work instantly
   - Kill switch state MUST persist
   - Kill switch MUST block all new tasks

### Credential Protection

1. **No Credential Leakage**
   - Credentials MUST be encrypted at rest
   - Credentials MUST NOT appear in logs
   - Credentials MUST NOT appear in audit records

2. **Credential Rotation**
   - Support automatic credential rotation
   - Rotation MUST be logged
   - Rotation MUST NOT break active tasks

## Observability Contract

### Logging Requirements

1. **Structured Logging**
   - All logs MUST be structured
   - All logs MUST include correlation ID
   - All logs MUST include timestamp

2. **Log Levels**
   - ERROR: Failures requiring intervention
   - WARNING: Degradation or recoverable failures
   - INFO: Normal operations
   - DEBUG: Detailed diagnostics

### Metrics Requirements

The runtime MUST emit:

1. **Counter Metrics**
   - Tasks created/completed/failed
   - Tool executions by type
   - Safety decisions by tier
   - API requests by endpoint

2. **Gauge Metrics**
   - Active task count
   - Queue depths
   - Memory utilization
   - Component health status

3. **Histogram Metrics**
   - Task latency distribution
   - Tool execution latency
   - Model inference latency
   - API response latency

### Tracing Requirements

The runtime MUST support:

1. **Distributed Tracing**
   - Every task MUST have trace ID
   - Every operation MUST be span
   - Spans MUST be properly nested

2. **Trace Context**
   - Trace context MUST propagate
   - Context MUST include correlation ID
   - Context MUST include user ID

## Compliance Contract

### Data Protection

1. **User Data**
   - User data MUST be encrypted at rest
   - User data MUST be encrypted in transit
   - User data MUST be retrievable/deletable

2. **Audit Data**
   - Audit records MUST be immutable
   - Audit records MUST be retained
   - Audit records MUST be searchable

### Operational Compliance

1. **Backup Requirements**
   - Database MUST be backed up daily
   - Backups MUST be tested
   - Recovery MUST be documented

2. **Change Management**
   - All changes MUST be versioned
   - All changes MUST be tested
   - Rollback MUST be possible

## Versioning Contract

### Runtime Version

The runtime MUST report:

- Semantic version (e.g., 1.0.0)
- Git commit hash
- Build timestamp
- Dependency versions

### API Versioning

The API MUST:

- Use semantic versioning
- Support version negotiation
- Maintain backward compatibility
- Document breaking changes

### Migration Support

The runtime MUST:

- Support database migrations
- Support configuration migrations
- Support data migrations
- Test migrations before deployment