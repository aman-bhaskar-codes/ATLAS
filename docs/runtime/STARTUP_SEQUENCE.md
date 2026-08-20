# ATLAS Startup Sequence

> **Purpose**: Define the exact startup sequence for the ATLAS runtime.
> **Status**: Version 1.0
> **Last Updated**: 2026-08-20

## Overview

The ATLAS runtime follows a strict, phased startup sequence that ensures:
- Components are initialized in dependency order
- Failures are detected early and handled appropriately
- System reaches a known-good state before accepting tasks
- Degraded modes are supported for non-critical failures

## Startup State Machine

```
BOOTING → INITIALIZING → DEGRADED → READY
           ↓
         FAILED
```

## Detailed Startup Sequence

### Phase 0: Bootstrap (BOOTING)

**Purpose**: Load minimal configuration and prepare for startup

**Steps**:
1. Parse command-line arguments
2. Load environment variables
3. Read configuration files
4. Validate configuration completeness
5. Set up logging infrastructure
6. Initialize basic error handling

**Success Criteria**:
- Configuration loaded without errors
- Logging operational
- Error handlers installed

**Failure Handling**:
- Invalid configuration → `FAILED` with clear error message
- Missing configuration → `FAILED` with setup instructions
- Logging failure → `FAILED` (cannot continue without logging)

**Estimated Duration**: < 100ms

### Phase 1: Infrastructure Initialization (INITIALIZING)

**Purpose**: Initialize core infrastructure components

**Steps**:
1. Initialize database connection
2. Run database migrations if needed
3. Initialize message bus
4. Start ID generator
5. Start system clock
6. Initialize metrics collector
7. Initialize tracing system

**Success Criteria**:
- Database connection established
- Database schema up-to-date
- Message bus operational
- All infrastructure components healthy

**Failure Handling**:
- Database connection failure → `FAILED` (critical)
- Migration failure → `FAILED` (data integrity risk)
- Message bus failure → `FAILED` (cannot operate)

**Estimated Duration**: 500ms - 2s (depends on DB size)

**Events Emitted**:
- `infrastructure.database.connected`
- `infrastructure.bus.started`
- `infrastructure.ready`

### Phase 2: Safety Initialization (INITIALIZING)

**Purpose**: Initialize safety-critical components

**Steps**:
1. Load safety manifest from config
2. Initialize kill switch
3. Start audit log
4. Initialize tier classifier
5. Load permission rules
6. Verify safety configuration
7. Initialize sandbox environments

**Success Criteria**:
- Safety manifest loaded and valid
- Kill switch operational
- Audit log writable
- Tier classifier functional
- At least one sandbox available

**Failure Handling**:
- Manifest invalid → `FAILED` (safety cannot be compromised)
- Kill switch failure → `FAILED` (emergency stop unavailable)
- Audit log failure → `FAILED` (compliance requirement)
- No sandbox available → `DEGRADED` (native sandbox fallback)

**Estimated Duration**: 100ms - 500ms

**Events Emitted**:
- `safety.manifest.loaded`
- `safety.killswitch.initialized`
- `safety.audit.started`
- `safety.ready`

### Phase 3: Intelligence Initialization (INITIALIZING)

**Purpose**: Initialize AI/ML components

**Steps**:
1. Initialize model gateway
2. Load model registry
3. Start embedder
4. Initialize providers (local first)
5. Connect to local Ollama instance
6. Initialize cost governor
7. Start quota manager
8. Initialize circuit breaker

**Success Criteria**:
- Model gateway operational
- At least one model provider available
- Embedder functional
- Cost tracking enabled

**Failure Handling**:
- Model gateway failure → `DEGRADED` (local-only mode)
- No providers available → `DEGRADED` (cannot reason)
- Embedder failure → `DEGRADED` (reduced memory)
- Ollama unavailable → `DEGRADED` (cloud fallback if allowed)

**Estimated Duration**: 1s - 5s (model loading)

**Events Emitted**:
- `intelligence.gateway.started`
- `intelligence.providers.loaded`
- `intelligence.embedder.started`
- `intelligence.ready` / `intelligence.degraded`

### Phase 4: Memory Initialization (INITIALIZING)

**Purpose**: Initialize memory subsystems

**Steps**:
1. Initialize vector store
2. Start embedding worker
3. Initialize episodic memory
4. Initialize semantic memory
5. Initialize user model
6. Initialize working memory
7. Initialize knowledge store
8. Initialize trajectory store
9. Connect memory to event bus

**Success Criteria**:
- Vector store operational
- Embedding worker started
- All memory subsystems initialized
- Event bus connections established

**Failure Handling**:
- Vector store failure → `DEGRADED` (keyword search only)
- Embedding worker failure → `DEGRADED` (no semantic search)
- Episodic memory failure → `DEGRADED` (no task history)
- Semantic memory failure → `DEGRADED` (no long-term memory)

**Estimated Duration**: 500ms - 2s

**Events Emitted**:
- `memory.vectorstore.started`
- `memory.episodic.started`
- `memory.semantic.started`
- `memory.worker.started`
- `memory.ready` / `memory.degraded`

### Phase 5: Capability Initialization (INITIALIZING)

**Purpose**: Initialize capability platforms

**Steps**:
1. Initialize capability registry
2. Initialize capability health tracker
3. Start identity platform
4. Start notification platform
5. Initialize knowledge providers
6. Initialize email platform
7. Initialize calendar platform
8. Initialize contacts platform
9. Initialize browser platform (optional)
10. Start capability dispatcher

**Success Criteria**:
- Capability registry operational
- Core platforms initialized
- At least filesystem and shell tools available
- Capability health tracking active

**Failure Handling**:
- Registry failure → `FAILED` (cannot execute tools)
- Identity failure → `DEGRADED` (no auth capabilities)
- Notification failure → `DEGRADED` (no notifications)
- Browser failure → `DEGRADED` (no web automation)
- Individual provider failure → `DEGRADED` (partial capabilities)

**Estimated Duration**: 1s - 3s

**Events Emitted**:
- `capabilities.registry.started`
- `capabilities.identity.started`
- `capabilities.notification.started`
- `capabilities.browser.started` (if enabled)
- `capabilities.ready` / `capabilities.degraded`

### Phase 6: Orchestration Initialization (INITIALIZING)

**Purpose**: Initialize orchestration and reasoning components

**Steps**:
1. Initialize tool registry
2. Initialize tool router
3. Initialize tool health tracker
4. Start planner
5. Initialize reasoning loop
6. Initialize replanner
7. Initialize verifier
8. Start orchestrator
9. Initialize DAG executor
10. Start background workers

**Success Criteria**:
- Tool registry operational
- Planner functional
- Reasoning loop initialized
- Orchestrator ready to accept tasks
- Background workers started

**Failure Handling**:
- Tool registry failure → `FAILED` (cannot execute)
- Planner failure → `FAILED` (cannot reason)
- Reasoning failure → `FAILED` (cannot operate)
- Orchestrator failure → `FAILED` (core component)

**Estimated Duration**: 500ms - 1s

**Events Emitted**:
- `orchestration.tools.loaded`
- `orchestration.planner.started`
- `orchestration.reasoning.started`
- `orchestration.orchestrator.started`
- `orchestration.ready`

### Phase 7: Readiness Probes (INITIALIZING → READY/DEGRADED)

**Purpose**: Verify system readiness and determine final state

**Steps**:
1. Run database health check
2. Run safety system health check
3. Run intelligence health check
4. Run memory health check
5. Run capability health check
6. Run orchestration health check
7. Check critical dependencies
8. Verify end-to-end paths
9. Determine overall system state

**Health Check Logic**:
```python
def determine_state(health_results):
    critical_failures = [r for r in health_results if r.component in CRITICAL and r.status == 'failed']
    if critical_failures:
        return 'FAILED'
    
    degraded_components = [r for r in health_results if r.status != 'healthy']
    if degraded_components:
        return 'DEGRADED'
    
    return 'READY'
```

**Success Criteria**:
- All critical components healthy
- Non-critical failures documented
- System state determined
- Readiness endpoints responding

**Failure Handling**:
- Critical component failure → `FAILED`
- Partial degradation → `DEGRADED`
- All healthy → `READY`

**Estimated Duration**: 100ms - 500ms

**Events Emitted**:
- `runtime.readiness_check.started`
- `runtime.readiness_check.completed`
- `runtime.state_changed` (to READY/DEGRADED/FAILED)

### Phase 8: Post-Startup (READY/DEGRADED)

**Purpose**: Complete startup and begin normal operation

**Steps**:
1. Start accepting tasks
2. Begin health monitoring
3. Start scheduler
4. Initialize automations
5. Load crash recovery state
6. Resume interrupted tasks (if safe)
7. Emit runtime ready event
8. Update runtime metrics

**Success Criteria**:
- Task acceptance enabled
- Health monitoring active
- Scheduler operational
- Automations loaded
- Crash recovery complete

**Estimated Duration**: 500ms - 2s

**Events Emitted**:
- `runtime.ready` / `runtime.degraded`
- `runtime.tasks_accepting`
- `runtime.monitoring.started`
- `runtime.startup.completed`

## Startup Timing Targets

| Phase | Target Duration | Maximum Duration |
|-------|----------------|------------------|
| Phase 0: Bootstrap | < 100ms | 200ms |
| Phase 1: Infrastructure | < 1s | 5s |
| Phase 2: Safety | < 500ms | 2s |
| Phase 3: Intelligence | < 3s | 10s |
| Phase 4: Memory | < 1s | 5s |
| Phase 5: Capabilities | < 2s | 10s |
| Phase 6: Orchestration | < 1s | 3s |
| Phase 7: Readiness | < 500ms | 2s |
| Phase 8: Post-Startup | < 1s | 5s |
| **Total** | **< 10s** | **< 30s** |

## Startup Failure Modes

### Critical Failures (→ FAILED)

These failures prevent runtime from starting:

1. **Configuration Invalid**
   - Error: "Configuration file missing or invalid"
   - Action: Provide clear error message with fix instructions
   - Recovery: Fix configuration and restart

2. **Database Unavailable**
   - Error: "Cannot connect to database"
   - Action: Check database connection string and database status
   - Recovery: Fix database connectivity and restart

3. **Safety System Failure**
   - Error: "Safety manifest invalid or audit log unavailable"
   - Action: Do not start without safety
   - Recovery: Fix safety configuration and restart

4. **Orchestration Failure**
   - Error: "Cannot initialize orchestrator"
   - Action: Critical runtime component
   - Recovery: Fix orchestration configuration and restart

### Degraded Failures (→ DEGRADED)

These failures allow limited operation:

1. **No Cloud Providers**
   - Impact: Local-only mode
   - Action: Use local models only
   - Recovery: Configure cloud providers when available

2. **Browser Unavailable**
   - Impact: No web automation
   - Action: Use API-based capabilities
   - Recovery: Install browser dependencies

3. **Memory Subsystem Failure**
   - Impact: Reduced context window
   - Action: Use current task context only
   - Recovery: Fix vector store configuration

4. **Capability Platform Failure**
   - Impact: Specific capabilities unavailable
   - Action: Document unavailable capabilities
   - Recovery: Fix platform configuration

## Startup Events

All startup events follow this structure:

```python
{
    "kind": "runtime.phase.started",
    "phase": "infrastructure",
    "timestamp": "2026-08-20T10:00:00Z",
    "correlation_id": "startup-abc123",
    "metadata": {
        "step": "database_connection",
        "component": "database"
    }
}
```

### Event Sequence

A successful startup emits these events in order:

1. `runtime.bootstrapping`
2. `runtime.phase.started` (infrastructure)
3. `infrastructure.database.connected`
4. `infrastructure.bus.started`
5. `runtime.phase.completed` (infrastructure)
6. `runtime.phase.started` (safety)
7. `safety.manifest.loaded`
8. `safety.killswitch.initialized`
9. `runtime.phase.completed` (safety)
10. `runtime.phase.started` (intelligence)
11. `intelligence.gateway.started`
12. `intelligence.providers.loaded`
13. `runtime.phase.completed` (intelligence)
14. `runtime.phase.started` (memory)
15. `memory.vectorstore.started`
16. `memory.episodic.started`
17. `runtime.phase.completed` (memory)
18. `runtime.phase.started` (capabilities)
19. `capabilities.registry.started`
20. `runtime.phase.completed` (capabilities)
21. `runtime.phase.started` (orchestration)
22. `orchestration.orchestrator.started`
23. `runtime.phase.completed` (orchestration)
24. `runtime.readiness_check.started`
25. `runtime.readiness_check.completed`
26. `runtime.state_changed` (to READY/DEGRADED)
27. `runtime.ready` / `runtime.degraded`
28. `runtime.startup.completed`

## Startup Configuration

### Environment Variables

```bash
# Required
ATLAS_ENV=dev|prod
ATLAS_DATA_DIR=/path/to/data
ATLAS_CONFIG_DIR=/path/to/config

# Optional
ATLAS_LOG_LEVEL=info
ATLAS_STARTUP_TIMEOUT=30
ATLAS_HEALTH_CHECK_INTERVAL=60
```

### Configuration Files

```yaml
# config/runtime.yaml
startup:
  timeout_seconds: 30
  health_check_interval_seconds: 60
  crash_recovery_enabled: true
  graceful_shutdown_timeout_seconds: 30

phases:
  infrastructure:
    enabled: true
    timeout_seconds: 5
  safety:
    enabled: true
    timeout_seconds: 2
  intelligence:
    enabled: true
    timeout_seconds: 10
    require_cloud: false
  memory:
    enabled: true
    timeout_seconds: 5
  capabilities:
    enabled: true
    timeout_seconds: 10
    browser_required: false
  orchestration:
    enabled: true
    timeout_seconds: 3
```

## Startup Diagnostics

### Startup Logging

Each phase logs:

```
[INFO] runtime.bootstrapping - Starting ATLAS runtime v1.0.0
[INFO] runtime.phase.started - Phase: infrastructure
[INFO] infrastructure.database.connected - Database connection established
[INFO] infrastructure.bus.started - Message bus operational
[INFO] runtime.phase.completed - Phase: infrastructure completed in 1.2s
[INFO] runtime.phase.started - Phase: safety
[INFO] safety.manifest.loaded - Safety manifest loaded: 15 rules
[INFO] safety.killswitch.initialized - Kill switch operational
[INFO] runtime.phase.completed - Phase: safety completed in 0.3s
...
[INFO] runtime.state_changed - State: READY
[INFO] runtime.ready - ATLAS runtime ready in 8.7s
```

### Startup Metrics

The runtime tracks:

- Total startup duration
- Per-phase duration
- Component initialization times
- Health check durations
- Degraded component count

### Startup Failure Logging

On failure:

```
[ERROR] runtime.phase.failed - Phase: intelligence
[ERROR] intelligence.gateway.failed - No model providers available
[ERROR] runtime.failed - Startup failed: No model providers available
[ERROR] runtime.failure_details - 
  Phase: intelligence
  Component: model_gateway
  Error: No providers available
  Recovery: Configure at least one model provider
```

## Startup Testing

### Smoke Test

```bash
# Test basic startup
atlas start --smoke-test

# Expected output
✓ Configuration loaded
✓ Infrastructure initialized (1.2s)
✓ Safety initialized (0.3s)
✓ Intelligence initialized (2.1s)
✓ Memory initialized (0.8s)
✓ Capabilities initialized (1.5s)
✓ Orchestration initialized (0.6s)
✓ Readiness checks passed (0.2s)
✓ Runtime ready (8.7s)
State: READY
```

### Health Check Test

```bash
# Test health endpoints
curl http://localhost:8730/live
# {"alive": true}

curl http://localhost:8730/ready
# {"ready": true, "state": "READY"}

curl http://localhost:8730/health
# {"overall": "healthy", "components": [...]}
```

### Component Health Test

```bash
# Test individual components
atlas doctor --component database
atlas doctor --component safety
atlas doctor --component intelligence
```

## Troubleshooting

### Common Startup Issues

#### Database Connection Timeout
```
Error: Database connection timeout after 5s
Cause: Database not running or wrong connection string
Fix: Check database status and ATLAS_DB_PATH
```

#### Ollama Not Running
```
Error: Cannot connect to Ollama at http://localhost:11434
Cause: Ollama not started
Fix: Start Ollama: ollama serve
```

#### Port Already in Use
```
Error: Port 8730 already in use
Cause: Another ATLAS instance running
Fix: Stop other instance or use different port
```

#### Configuration Invalid
```
Error: Configuration file invalid: missing required field
Cause: config/settings.yaml missing or malformed
Fix: Restore configuration file
```

### Startup Debug Mode

```bash
# Enable debug logging
ATLAS_LOG_LEVEL=debug atlas start

# Enable detailed startup tracing
atlas start --trace-startup

# Skip optional components
atlas start --no-browser --no-cloud
```

## Startup Rollback

If startup fails after a deployment:

1. Automatic rollback to previous version
2. Preserve database state
3. Clear any partially-initialized state
4. Restart with previous version
5. Verify startup success
6. Alert operators of rollback

## Startup Monitoring

### Metrics to Monitor

- `runtime.startup.duration.total`
- `runtime.startup.duration.by_phase`
- `runtime.startup.success_rate`
- `runtime.startup.failure_count`
- `runtime.state.transitions`

### Alerts to Configure

- Startup duration > 30s
- Startup failure rate > 5%
- Critical component failure
- Degraded state on startup
- Crash recovery failures