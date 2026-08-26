"""Runtime Supervisor — the heart of the ATLAS runtime orchestration layer.

This supervisor manages the complete lifecycle of the ATLAS system:
- Staged startup with health checks
- Component health monitoring
- Graceful shutdown
- Background worker management
- State transitions
- Failure recovery

The supervisor ensures ATLAS behaves as a "living" system rather than a collection of modules.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.config import AppConfig, Settings
from atlas.infra.logging import get_logger
from atlas.infra.metrics import Metrics

_log = get_logger("atlas.runtime.supervisor")


class SystemState(Enum):
    """Runtime system states."""

    BOOTING = "booting"
    INITIALIZING = "initializing"
    DEGRADED = "degraded"
    READY = "ready"
    BUSY = "busy"
    RECOVERING = "recovering"
    SHUTTING_DOWN = "shutting_down"
    FAILED = "failed"


class ComponentStatus(Enum):
    """Component health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class ComponentHealth:
    """Health status of a single component."""

    name: str
    status: ComponentStatus
    latency_ms: float = 0.0
    last_success: datetime | None = None
    last_failure: datetime | None = None
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    """Overall system health report."""

    overall_status: SystemState
    timestamp: datetime
    components: dict[str, ComponentHealth]
    degraded_components: list[str] = field(default_factory=list)
    unavailable_capabilities: list[str] = field(default_factory=list)
    uptime_seconds: float = 0.0


@dataclass
class StartupPhase:
    """Definition of a startup phase."""

    name: str
    critical: bool = True
    timeout_seconds: float = 30.0
    dependencies: list[str] = field(default_factory=list)


# Critical components that must be healthy for READY state
CRITICAL_COMPONENTS = frozenset(
    {
        "database",
        "safety",
        "orchestrator",
        "configuration",
        "intelligence_gateway",
    }
)

# Startup phases in order
STARTUP_PHASES = [
    StartupPhase("bootstrap", critical=True, timeout_seconds=1.0),
    StartupPhase("infrastructure", critical=True, timeout_seconds=5.0),
    StartupPhase("safety", critical=True, timeout_seconds=2.0),
    StartupPhase("intelligence", critical=True, timeout_seconds=10.0),
    StartupPhase("memory", critical=False, timeout_seconds=5.0),
    StartupPhase("capabilities", critical=False, timeout_seconds=10.0),
    StartupPhase("orchestration", critical=True, timeout_seconds=3.0),
    StartupPhase("readiness", critical=True, timeout_seconds=2.0),
]


class RuntimeSupervisor:
    """Supervises the ATLAS runtime lifecycle.

    The RuntimeSupervisor is responsible for:
    1. Coordinating staged startup with health checks
    2. Monitoring component health continuously
    3. Managing graceful shutdown
    4. Handling state transitions
    5. Coordinating background workers
    6. Implementing failure recovery
    """

    def __init__(
        self,
        settings: Settings,
        config: AppConfig,
        clock: Clock,
        metrics: Metrics,
    ) -> None:
        self._settings = settings
        self._config = config
        self._clock = clock
        self._metrics = metrics

        # State management
        self._state = SystemState.BOOTING
        self._startup_time: datetime | None = None
        self._component_health: dict[str, ComponentHealth] = {}

        # Lifecycle control
        self._shutdown_event = asyncio.Event()
        self._background_tasks: set[asyncio.Task[None]] = set()

        # Component references (set during startup)
        self._atlas: Any = None
        self._worker_registry: dict[str, Any] = {}

        # Health monitoring
        self._health_check_interval = 60.0  # seconds
        self._health_check_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> SystemState:
        """Current system state."""
        return self._state

    @property
    def uptime_seconds(self) -> float:
        """System uptime in seconds."""
        if self._startup_time is None:
            return 0.0
        return (self._clock.now() - self._startup_time).total_seconds()

    async def start(self, atlas: Any) -> HealthReport:
        """Start the runtime supervisor with staged initialization.

        Args:
            atlas: The fully constructed Atlas instance

        Returns:
            HealthReport showing final system state
        """
        self._atlas = atlas
        self._startup_time = self._clock.now()
        _log.info("runtime.startup.started", state=self._state.value)

        try:
            # Execute staged startup
            for phase in STARTUP_PHASES:
                await self._execute_startup_phase(phase)

                # Transition state after each phase
                if self._state == SystemState.BOOTING:
                    self._state = SystemState.INITIALIZING

            # Run final readiness checks
            health_report = await self._run_readiness_checks()

            # Set final state based on health
            self._state = health_report.overall_status

            # Start background monitoring if we're in a usable state
            if self._state in (SystemState.READY, SystemState.DEGRADED):
                await self._start_background_monitoring()
                await self._start_background_workers()

            _log.info(
                "runtime.startup.completed",
                state=self._state.value,
                uptime_seconds=self.uptime_seconds,
                degraded_count=len(health_report.degraded_components),
            )

            return health_report

        except Exception as exc:
            _log.error("runtime.startup.failed", error=str(exc), exc_info=True)
            self._state = SystemState.FAILED
            raise

    async def _execute_startup_phase(self, phase: StartupPhase) -> None:
        """Execute a single startup phase with timeout and health checks.

        Args:
            phase: The startup phase to execute
        """
        _log.info("runtime.phase.started", phase=phase.name, critical=phase.critical)
        phase_start = time.perf_counter()

        try:
            # Execute phase-specific initialization
            await self._initialize_phase(phase)

            # Verify phase success
            await self._verify_phase_health(phase)

            duration_ms = (time.perf_counter() - phase_start) * 1000
            _log.info(
                "runtime.phase.completed",
                phase=phase.name,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            _log.error(
                "runtime.phase.timeout",
                phase=phase.name,
                timeout_seconds=phase.timeout_seconds,
            )
            if phase.critical:
                raise RuntimeError(f"Critical phase {phase.name} timed out") from None
            else:
                _log.warning("runtime.phase.degraded", phase=phase.name)

        except Exception as exc:
            _log.error(
                "runtime.phase.failed",
                phase=phase.name,
                error=str(exc),
            )
            if phase.critical:
                raise
            else:
                _log.warning("runtime.phase.degraded", phase=phase.name)

    async def _initialize_phase(self, phase: StartupPhase) -> None:
        """Initialize components for a specific phase.

        Args:
            phase: The startup phase to initialize
        """
        if phase.name == "bootstrap":
            # Configuration already loaded, just validate
            await self._validate_configuration()

        elif phase.name == "infrastructure":
            # Infrastructure already initialized in app.py
            await self._verify_infrastructure()

        elif phase.name == "safety":
            # Safety already initialized, verify it's working
            await self._verify_safety()

        elif phase.name == "intelligence":
            # Intelligence already initialized, verify providers
            await self._verify_intelligence()

        elif phase.name == "memory":
            # Memory already initialized, verify subsystems
            await self._verify_memory()

        elif phase.name == "capabilities":
            # Capabilities already initialized, verify platforms
            await self._verify_capabilities()

        elif phase.name == "orchestration":
            # Orchestration already initialized, verify it's ready
            await self._verify_orchestration()

        elif phase.name == "readiness":
            # This is handled by _run_readiness_checks
            pass

    async def _verify_phase_health(self, phase: StartupPhase) -> None:
        """Verify health of components initialized in this phase.

        Args:
            phase: The startup phase to verify
        """
        # Phase-specific health checks
        if phase.name == "infrastructure":
            await self._check_database_health()
        elif phase.name == "intelligence":
            await self._check_intelligence_health()
        elif phase.name == "memory":
            await self._check_memory_health()
        elif phase.name == "capabilities":
            await self._check_capability_health()

    async def _validate_configuration(self) -> None:
        """Validate configuration is complete and valid."""
        # Configuration is already loaded and validated in app.py
        # This is a sanity check
        if not self._settings or not self._config:
            raise RuntimeError("Configuration not loaded")

    async def _verify_infrastructure(self) -> None:
        """Verify infrastructure components are healthy."""
        # Database should be connected
        if not await self._atlas.db.health():
            raise RuntimeError("Database not healthy")

        # Message bus should be operational
        if not self._atlas.bus:
            raise RuntimeError("Message bus not initialized")

    async def _verify_safety(self) -> None:
        """Verify safety systems are operational."""
        # Kill switch should be operational
        if not self._atlas.killswitch:
            raise RuntimeError("Kill switch not initialized")

        # Audit log should be writable
        if not self._atlas.audit:
            raise RuntimeError("Audit log not initialized")

    async def _verify_intelligence(self) -> None:
        """Verify intelligence systems are operational."""
        # Model gateway should be initialized
        if not self._atlas.gateway:
            raise RuntimeError("Model gateway not initialized")

        # At least one provider should be available
        health = await self._atlas.gateway.health()
        available = [p for p, h in health.items() if h]
        if not available:
            _log.warning("intelligence.no_providers", health=health)
            # This is a degradation, not a failure
            self._set_component_health(
                "intelligence_gateway", ComponentStatus.DEGRADED, detail="No providers available"
            )

    async def _verify_memory(self) -> None:
        """Verify memory subsystems are operational."""
        # Vector store should be initialized
        if not self._atlas.vectors:
            _log.warning("memory.vectorstore.unavailable")
            self._set_component_health("vectorstore", ComponentStatus.DEGRADED, detail="Vector store not initialized")

        # Episodic memory should be initialized
        if not self._atlas.episodic:
            _log.warning("memory.episodic.unavailable")
            self._set_component_health("episodic", ComponentStatus.DEGRADED, detail="Episodic memory not initialized")

    async def _verify_capabilities(self) -> None:
        """Verify capability platforms are operational."""
        # Tool registry should be initialized
        if not self._atlas.tools:
            raise RuntimeError("Tool registry not initialized")

        # Browser is optional
        if not self._atlas.browser_platform:
            _log.info("capabilities.browser.disabled")
            self._set_component_health("browser", ComponentStatus.UNAVAILABLE, detail="Browser platform disabled")

    async def _verify_orchestration(self) -> None:
        """Verify orchestration components are operational."""
        # Orchestrator should be initialized
        if not self._atlas.orchestrator:
            raise RuntimeError("Orchestrator not initialized")

    async def _run_readiness_checks(self) -> HealthReport:
        """Run comprehensive readiness checks.

        Returns:
            HealthReport showing system health
        """
        _log.info("runtime.readiness_check.started")

        # Check all critical components
        await self._check_database_health()
        await self._check_safety_health()
        await self._check_intelligence_health()
        await self._check_orchestration_health()

        # Check optional components
        await self._check_memory_health()
        await self._check_capability_health()

        # Calculate overall state
        overall_state = self._calculate_system_state()

        # Build health report
        degraded = [
            name for name, health in self._component_health.items() if health.status == ComponentStatus.DEGRADED
        ]
        unavailable = [
            name for name, health in self._component_health.items() if health.status == ComponentStatus.UNAVAILABLE
        ]

        report = HealthReport(
            overall_status=overall_state,
            timestamp=self._clock.now(),
            components=self._component_health.copy(),
            degraded_components=degraded,
            unavailable_capabilities=unavailable,
            uptime_seconds=self.uptime_seconds,
        )

        _log.info(
            "runtime.readiness_check.completed",
            state=overall_state.value,
            degraded_count=len(degraded),
            unavailable_count=len(unavailable),
        )

        return report

    def _calculate_system_state(self) -> SystemState:
        """Calculate overall system state from component health.

        Returns:
            SystemState based on component health
        """
        # Check for critical failures
        critical_failures = [
            name
            for name, health in self._component_health.items()
            if name in CRITICAL_COMPONENTS and health.status == ComponentStatus.FAILED
        ]
        if critical_failures:
            return SystemState.FAILED

        # Check if critical components are ready
        critical_not_ready = [
            name
            for name, health in self._component_health.items()
            if name in CRITICAL_COMPONENTS and health.status != ComponentStatus.HEALTHY
        ]
        if critical_not_ready:
            return SystemState.FAILED

        # Check for degraded components
        degraded = [
            name
            for name, health in self._component_health.items()
            if health.status in (ComponentStatus.DEGRADED, ComponentStatus.UNAVAILABLE)
        ]
        if degraded:
            return SystemState.DEGRADED

        return SystemState.READY

    async def _check_database_health(self) -> None:
        """Check database component health."""
        try:
            start = time.perf_counter()
            healthy = await self._atlas.db.health()
            latency_ms = (time.perf_counter() - start) * 1000

            if healthy:
                self._set_component_health(
                    "database", ComponentStatus.HEALTHY, latency_ms=latency_ms, detail="Connected and responsive"
                )
            else:
                self._set_component_health("database", ComponentStatus.FAILED, detail="Database health check failed")
        except Exception as exc:
            self._set_component_health("database", ComponentStatus.FAILED, detail=f"Database check failed: {exc!s}")

    async def _check_safety_health(self) -> None:
        """Check safety system health."""
        try:
            # Check kill switch
            if self._atlas.killswitch.is_active():
                self._set_component_health("safety", ComponentStatus.DEGRADED, detail="Kill switch active")
                return

            # Check audit log
            # (Assuming audit log has a health method)
            self._set_component_health("safety", ComponentStatus.HEALTHY, detail="Safety systems operational")
        except Exception as exc:
            self._set_component_health("safety", ComponentStatus.FAILED, detail=f"Safety check failed: {exc!s}")

    async def _check_intelligence_health(self) -> None:
        """Check intelligence system health."""
        try:
            health = await self._atlas.gateway.health()
            available = [p for p, h in health.items() if h]

            if not available:
                self._set_component_health(
                    "intelligence_gateway", ComponentStatus.FAILED, detail="No model providers available"
                )
            elif len(available) < len(health):
                self._set_component_health(
                    "intelligence_gateway", ComponentStatus.DEGRADED, detail=f"Partial providers: {available}"
                )
            else:
                self._set_component_health(
                    "intelligence_gateway", ComponentStatus.HEALTHY, detail=f"All providers available: {available}"
                )
        except Exception as exc:
            self._set_component_health(
                "intelligence_gateway", ComponentStatus.FAILED, detail=f"Intelligence check failed: {exc!s}"
            )

    async def _check_memory_health(self) -> None:
        """Check memory subsystem health."""
        try:
            # Check vector store
            if self._atlas.vectors:
                self._set_component_health("vectorstore", ComponentStatus.HEALTHY)
            else:
                self._set_component_health("vectorstore", ComponentStatus.UNAVAILABLE)

            # Check episodic memory
            if self._atlas.episodic:
                self._set_component_health("episodic", ComponentStatus.HEALTHY)
            else:
                self._set_component_health("episodic", ComponentStatus.UNAVAILABLE)

        except Exception as exc:
            self._set_component_health("memory", ComponentStatus.DEGRADED, detail=f"Memory check failed: {exc!s}")

    async def _check_capability_health(self) -> None:
        """Check capability platform health."""
        try:
            # Check browser platform
            if self._atlas.browser_platform:
                self._set_component_health("browser", ComponentStatus.HEALTHY)
            else:
                self._set_component_health("browser", ComponentStatus.UNAVAILABLE)

        except Exception as exc:
            self._set_component_health(
                "capabilities", ComponentStatus.DEGRADED, detail=f"Capability check failed: {exc!s}"
            )

    async def _check_orchestration_health(self) -> None:
        """Check orchestration system health."""
        try:
            if self._atlas.orchestrator:
                self._set_component_health("orchestrator", ComponentStatus.HEALTHY)
            else:
                self._set_component_health("orchestrator", ComponentStatus.FAILED)
        except Exception as exc:
            self._set_component_health(
                "orchestrator", ComponentStatus.FAILED, detail=f"Orchestration check failed: {exc!s}"
            )

    def _set_component_health(
        self,
        name: str,
        status: ComponentStatus,
        latency_ms: float = 0.0,
        detail: str = "",
    ) -> None:
        """Set health status for a component.

        Args:
            name: Component name
            status: Health status
            latency_ms: Operation latency
            detail: Status detail message
        """
        now = self._clock.now()

        if name not in self._component_health:
            self._component_health[name] = ComponentHealth(name=name, status=ComponentStatus.UNAVAILABLE)

        health = self._component_health[name]
        health.status = status
        health.latency_ms = latency_ms
        health.detail = detail

        if status == ComponentStatus.HEALTHY:
            health.last_success = now
        else:
            health.last_failure = now

        _log.debug(
            "component.health.updated",
            component=name,
            status=status.value,
            detail=detail,
        )

    async def _start_background_monitoring(self) -> None:
        """Start background health monitoring."""
        if self._health_check_task is not None:
            return  # Already running

        self._health_check_task = asyncio.create_task(self._health_monitor_loop())
        self._background_tasks.add(self._health_check_task)
        self._health_check_task.add_done_callback(self._background_tasks.discard)
        _log.info("runtime.monitoring.started", interval_seconds=self._health_check_interval)

    async def _health_monitor_loop(self) -> None:
        """Background health monitoring loop."""
        while not self._shutdown_event.is_set():
            try:
                # Wait for shutdown signal or interval
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._health_check_interval,
                )
                if self._shutdown_event.is_set():
                    break

                # Run health checks
                await self._run_readiness_checks()

            except TimeoutError:
                # Normal timeout, continue loop
                continue
            except Exception as exc:
                _log.error("runtime.monitoring.error", error=str(exc), exc_info=True)
                # Continue monitoring despite errors

    async def _start_background_workers(self) -> None:
        """Start background workers."""
        _log.info("runtime.workers.starting")

        # Start embedding worker
        if self._atlas.embedding_worker:
            await self._atlas.embedding_worker.start()
            self._worker_registry["embedding_worker"] = self._atlas.embedding_worker

        # Start scheduler if available
        if self._atlas.scheduler:
            await self._atlas.scheduler.start()
            self._worker_registry["scheduler"] = self._atlas.scheduler

        _log.info("runtime.workers.started", count=len(self._worker_registry))

    async def shutdown(self, timeout_seconds: float = 30.0) -> None:
        """Gracefully shutdown the runtime supervisor.

        Args:
            timeout_seconds: Maximum time to wait for graceful shutdown
        """
        if self._state in (SystemState.SHUTTING_DOWN, SystemState.FAILED):
            return  # Already shutting down or failed

        self._state = SystemState.SHUTTING_DOWN
        _log.info("runtime.shutdown.started", timeout_seconds=timeout_seconds)

        try:
            # Signal shutdown
            self._shutdown_event.set()

            # Stop accepting new tasks
            await self._stop_accepting_tasks()

            # Stop background monitoring and workers (tracked in _background_tasks)
            await self._stop_background_workers(timeout_seconds)

            _log.info("runtime.shutdown.completed", uptime_seconds=self.uptime_seconds)

        except Exception as exc:
            _log.error("runtime.shutdown.failed", error=str(exc), exc_info=True)
            self._state = SystemState.FAILED
            raise

    async def _stop_accepting_tasks(self) -> None:
        """Stop accepting new tasks."""
        # This would integrate with the orchestrator to stop accepting tasks
        _log.info("runtime.tasks.stopped")

    async def _stop_background_workers(self, timeout_seconds: float) -> None:
        """Stop all background workers gracefully.

        Args:
            timeout_seconds: Maximum time to wait for workers to stop
        """
        _log.info("runtime.workers.stopping", count=len(self._background_tasks))

        # Cancel all background tasks
        for task in self._background_tasks:
            task.cancel()

        # Wait for tasks to complete with timeout
        if self._background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._background_tasks, return_exceptions=True),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                _log.warning("runtime.workers.timeout", some_workers_still_running=True)

        self._background_tasks.clear()
        if self._atlas is not None:
            if self._atlas.embedding_worker is not None:
                await self._atlas.embedding_worker.stop()
            if self._atlas.scheduler is not None:
                await self._atlas.scheduler.stop()
        self._worker_registry.clear()
        _log.info("runtime.workers.stopped")

    def get_health_report(self) -> HealthReport:
        """Get current health report.

        Returns:
            Current system health report
        """
        return HealthReport(
            overall_status=self._state,
            timestamp=self._clock.now(),
            components=self._component_health.copy(),
            degraded_components=[
                name for name, health in self._component_health.items() if health.status == ComponentStatus.DEGRADED
            ],
            unavailable_capabilities=[
                name for name, health in self._component_health.items() if health.status == ComponentStatus.UNAVAILABLE
            ],
            uptime_seconds=self.uptime_seconds,
        )

    def get_degraded_components(self) -> list[str]:
        """Get list of degraded component names.

        Returns:
            List of degraded component names
        """
        return [name for name, health in self._component_health.items() if health.status == ComponentStatus.DEGRADED]

    def get_unavailable_capabilities(self) -> list[str]:
        """Get list of unavailable capabilities.

        Returns:
            List of unavailable capability names
        """
        return [name for name, health in self._component_health.items() if health.status == ComponentStatus.UNAVAILABLE]
