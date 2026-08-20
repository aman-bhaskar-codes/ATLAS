# ATLAS Readiness Model

> **Purpose**: Define the readiness model for ATLAS runtime components.
> **Status**: Version 1.0
> **Last Updated**: 2026-08-20

## Overview

The readiness model defines when ATLAS is ready to accept tasks and how component health affects overall system readiness.

## Readiness States

### Component Readiness Levels

Each component reports one of these readiness levels:

| Level | Description | Task Acceptance |
|-------|-------------|-----------------|
| `ready` | Fully operational | Yes |
| `degraded` | Partially operational | Yes (with limitations) |
| `unavailable` | Not responding | No |
| `failed` | Critical failure | No |

### System Readiness States

The overall system readiness is derived from component readiness:

| System State | Component Requirements | Task Acceptance |
|-------------|----------------------|-----------------|
| `READY` | All critical components `ready` | Yes |
| `DEGRADED` | Critical components `ready`, some non-critical `degraded` | Yes (limited) |
| `NOT_READY` | Any critical component not `ready` | No |
| `FAILED` | Critical component `failed` | No |

## Critical vs Non-Critical Components

### Critical Components

These components MUST be `ready` for system to be `READY`:

1. **Database**
   - Required for all operations
   - Failure: `NOT_READY` → `FAILED`

2. **Safety Engine**
   - Required for all tool execution
   - Failure: `NOT_READY` → `FAILED`

3. **Orchestrator**
   - Required for task execution
   - Failure: `NOT_READY` → `FAILED`

4. **Configuration**
   - Required for all operations
   - Failure: `NOT_READY` → `FAILED`

5. **Model Gateway** (at least one provider)
   - Required for reasoning
   - Failure: `NOT_READY` → `DEGRADED` (local-only fallback)

### Non-Critical Components

These components MAY be `degraded` without preventing `READY`:

1. **Browser Platform**
   - Degraded: No web automation
   - Failure: `DEGRADED`

2. **Cloud Providers**
   - Degraded: Local-only mode
   - Failure: `DEGRADED`

3. **Memory Subsystems**
   - Degraded: Reduced context
   - Failure: `DEGRADED`

4. **Capability Platforms**
   - Degraded: Partial capabilities
   - Failure: `DEGRADED`

5. **Notification System**
   - Degraded: No notifications
   - Failure: `DEGRADED`

## Readiness Probes

### Probe Types

#### Liveness Probe
```http
GET /live
```

**Purpose**: Check if process is running
**Response**: `{"alive": true|false}`
**Frequency**: Every 10s
**Timeout**: 5s
**Failure Action**: Restart process

#### Readiness Probe
```http
GET /ready
```

**Purpose**: Check if system can accept tasks
**Response**: `{"ready": true|false, "state": "..."}`
**Frequency**: Every 5s
**Timeout**: 2s
**Failure Action**: Stop routing traffic

#### Health Probe
```http
GET /health
```

**Purpose**: Detailed component health
**Response**: Detailed health report
**Frequency**: Every 30s
**Timeout**: 10s
**Failure Action**: Alert operators

### Component-Specific Probes

#### Database Probe
```python
async def database_probe(db: Database) -> ProbeResult:
    try:
        # Execute simple query
        cursor = await db.conn.execute("SELECT 1")
        result = await cursor.fetchone()
        
        # Check connection health
        if result and result[0] == 1:
            return ProbeResult(
                component="database",
                status="ready",
                latency_ms=measure_latency(),
                detail="Connected and responsive"
            )
        else:
            return ProbeResult(
                component="database",
                status="failed",
                detail="Query returned unexpected result"
            )
    except Exception as e:
        return ProbeResult(
            component="database",
            status="failed",
            detail=f"Connection failed: {str(e)}"
        )
```

#### Safety Engine Probe
```python
async def safety_probe(safety: SafetyEngine) -> ProbeResult:
    try:
        # Check kill switch status
        if safety.killswitch.is_active():
            return ProbeResult(
                component="safety",
                status="degraded",
                detail="Kill switch active"
            )
        
        # Check audit log
        if not await safety.audit.health():
            return ProbeResult(
                component="safety",
                status="failed",
                detail="Audit log unavailable"
            )
        
        return ProbeResult(
            component="safety",
            status="ready",
            detail="Safety systems operational"
        )
    except Exception as e:
        return ProbeResult(
            component="safety",
            status="failed",
            detail=f"Safety check failed: {str(e)}"
        )
```

#### Model Gateway Probe
```python
async def intelligence_probe(gateway: ModelGateway) -> ProbeResult:
    try:
        # Check if any provider is available
        health = await gateway.health()
        available_providers = [p for p, healthy in health.items() if healthy]
        
        if not available_providers:
            return ProbeResult(
                component="intelligence",
                status="failed",
                detail="No model providers available"
            )
        
        if len(available_providers) < len(health):
            return ProbeResult(
                component="intelligence",
                status="degraded",
                detail=f"Partial providers: {available_providers}"
            )
        
        return ProbeResult(
            component="intelligence",
            status="ready",
            detail=f"All providers available: {available_providers}"
        )
    except Exception as e:
        return ProbeResult(
            component="intelligence",
            status="failed",
            detail=f"Intelligence check failed: {str(e)}"
        )
```

## Readiness Calculation

### Readiness Algorithm

```python
def calculate_system_state(component_health: dict[str, ProbeResult]) -> SystemState:
    # Check critical components first
    critical_failures = [
        name for name, result in component_health.items()
        if name in CRITICAL_COMPONENTS and result.status == "failed"
    ]
    
    if critical_failures:
        return SystemState.FAILED
    
    # Check if critical components are ready
    critical_not_ready = [
        name for name, result in component_health.items()
        if name in CRITICAL_COMPONENTS and result.status != "ready"
    ]
    
    if critical_not_ready:
        return SystemState.NOT_READY
    
    # Check for any degraded components
    degraded_components = [
        name for name, result in component_health.items()
        if result.status == "degraded"
    ]
    
    if degraded_components:
        return SystemState.DEGRADED
    
    return SystemState.READY
```

### Readiness Transition Rules

```
NOT_READY → READY: All critical components become ready
NOT_READY → DEGRADED: Critical components ready, some non-critical degraded
NOT_READY → FAILED: Critical component fails
READY → DEGRADED: Non-critical component degrades
READY → NOT_READY: Critical component becomes not ready
READY → FAILED: Critical component fails
DEGRADED → READY: All degraded components recover
DEGRADED → NOT_READY: Critical component becomes not ready
DEGRADED → FAILED: Critical component fails
FAILED → NOT_READY: Critical component recovers but not ready
FAILED → READY: All critical components recover and ready
```

## Readiness Endpoints

### GET /live

**Purpose**: Process liveness check

**Response**:
```json
{
  "alive": true,
  "timestamp": "2026-08-20T10:00:00Z",
  "uptime_seconds": 3600
}
```

**Implementation**:
```python
@app.get("/live")
async def liveness_probe():
    return {
        "alive": True,
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_seconds": (datetime.utcnow() - startup_time).total_seconds()
    }
```

### GET /ready

**Purpose**: Readiness check for task acceptance

**Response**:
```json
{
  "ready": true,
  "state": "READY",
  "timestamp": "2026-08-20T10:00:00Z",
  "degraded_components": [],
  "unavailable_capabilities": []
}
```

**Implementation**:
```python
@app.get("/ready")
async def readiness_probe():
    state = runtime_supervisor.get_state()
    return {
        "ready": state in (SystemState.READY, SystemState.DEGRADED),
        "state": state.value,
        "timestamp": datetime.utcnow().isoformat(),
        "degraded_components": runtime_supervisor.get_degraded_components(),
        "unavailable_capabilities": runtime_supervisor.get_unavailable_capabilities()
    }
```

### GET /health

**Purpose**: Detailed component health

**Response**:
```json
{
  "overall": "healthy",
  "timestamp": "2026-08-20T10:00:00Z",
  "components": [
    {
      "name": "database",
      "status": "ready",
      "latency_ms": 5,
      "last_success": "2026-08-20T09:59:55Z",
      "last_failure": null,
      "detail": "Connected and responsive"
    },
    {
      "name": "safety",
      "status": "ready",
      "latency_ms": 2,
      "last_success": "2026-08-20T09:59:58Z",
      "last_failure": null,
      "detail": "Safety systems operational"
    }
  ]
}
```

**Implementation**:
```python
@app.get("/health")
async def health_probe():
    component_health = await runtime_supervisor.check_all_components()
    return {
        "overall": calculate_overall_health(component_health),
        "timestamp": datetime.utcnow().isoformat(),
        "components": [
            {
                "name": name,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "last_success": result.last_success,
                "last_failure": result.last_failure,
                "detail": result.detail
            }
            for name, result in component_health.items()
        ]
    }
```

## Readiness Monitoring

### Readiness Metrics

Track these metrics:

- `readiness.probe.duration.total`
- `readiness.probe.duration.by_component`
- `readiness.state.transitions`
- `readiness.component failures`
- `readiness.degraded.duration`

### Readiness Alerts

Configure alerts for:

- System not ready for > 1 minute
- Critical component failure
- Degraded state for > 5 minutes
- Readiness probe failures
- Component flapping (frequent state changes)

## Readiness Testing

### Readiness Test Suite

```python
@pytest.mark.asyncio
async def test_readiness_on_startup():
    """System should become ready after successful startup"""
    await runtime_supervisor.start()
    assert runtime_supervisor.get_state() == SystemState.READY

@pytest.mark.asyncio
async def test_readiness_with_degraded_component():
    """System should be degraded when non-critical component fails"""
    # Simulate browser failure
    await runtime_supervisor.start()
    runtime_supervisor.set_component_health("browser", "degraded")
    assert runtime_supervisor.get_state() == SystemState.DEGRADED

@pytest.mark.asyncio
async def test_readiness_with_critical_failure():
    """System should fail when critical component fails"""
    # Simulate database failure
    await runtime_supervisor.start()
    runtime_supervisor.set_component_health("database", "failed")
    assert runtime_supervisor.get_state() == SystemState.FAILED
```

### Manual Readiness Test

```bash
# Test liveness
curl -f http://localhost:8730/live || echo "Liveness check failed"

# Test readiness
curl -f http://localhost:8730/ready || echo "Readiness check failed"

# Test health
curl -f http://localhost:8730/health || echo "Health check failed"

# Test with jq
curl http://localhost:8730/health | jq '.overall'
```

## Readiness Recovery

### Automatic Recovery

The runtime supervisor should:

1. **Monitor component health** continuously
2. **Attempt automatic recovery** for transient failures
3. **Update system state** when components recover
4. **Emit recovery events** for monitoring

### Recovery Strategies

#### Database Recovery
```python
async def recover_database():
    """Attempt to recover database connection"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await database.reconnect()
            if await database.health():
                set_component_health("database", "ready")
                return True
        except Exception:
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    return False
```

#### Provider Recovery
```python
async def recover_provider(provider_name: str):
    """Attempt to recover a failed provider"""
    try:
        await gateway.recover_provider(provider_name)
        if await gateway.provider_health(provider_name):
            set_component_health(f"provider.{provider_name}", "ready")
            return True
    except Exception:
        return False
```

### Manual Recovery

Operators can trigger manual recovery:

```bash
# Trigger specific component recovery
atlas recover database
atlas recover provider.ollama
atlas recover browser

# Trigger full system recovery
atlas recover all
```

## Readiness Documentation

### Component Readiness Documentation

Each component should document:

1. **Readiness criteria**: What makes it ready
2. **Degradation modes**: How it can be degraded
3. **Failure modes**: How it can fail
4. **Recovery strategies**: How to recover
5. **Dependencies**: What it depends on

### Example: Database Component

```markdown
## Database Component

### Readiness Criteria
- Connection established
- Schema up-to-date
- Can execute queries
- Can write transactions

### Degradation Modes
- High latency (> 100ms)
- Connection pool exhaustion
- Read replica lag

### Failure Modes
- Connection refused
- Authentication failure
- Disk full
- Corruption detected

### Recovery Strategies
- Automatic reconnection
- Connection pool reset
- Failover to replica
- Alert operators

### Dependencies
- Network connectivity
- Database server running
- Valid credentials
- Sufficient disk space
```

## Readiness Best Practices

1. **Fast Probes**: Keep probes under 2 seconds
2. **Idempotent**: Probes should not have side effects
3. **Circuit Breakers**: Stop probing failing components temporarily
4. **Exponential Backoff**: Don't spam failing components
5. **Comprehensive**: Probe all important aspects
6. **Actionable**: Provide clear failure reasons
7. **Monitored**: Track probe metrics
8. **Tested**: Include probe tests in test suite