"""
Services Base Classes and Error Hierarchy

Provides foundational abstractions for all RoboCo services:
- BaseService: Session-based service with logging and error handling
- SingletonService: Stateless singleton services
- SingletonHolder: Generic singleton pattern
- ServiceError hierarchy: Consistent error types across services
"""

from typing import ClassVar

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# =============================================================================
# EXCEPTION HIERARCHY
# =============================================================================
# Services should raise these errors. The API layer translates to HTTP status.


class ServiceError(Exception):
    """Base exception for all service errors."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(ServiceError):
    """Resource not found. API layer should translate to 404."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        message = f"{resource_type} not found"
        if resource_id:
            message = f"{resource_type} not found: {resource_id}"
        super().__init__(message, details)
        self.resource_type = resource_type
        self.resource_id = resource_id


class ValidationError(ServiceError):
    """Invalid input or state. API layer should translate to 400."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details)
        self.field = field


class ConflictError(ServiceError):
    """Resource conflict (duplicate, state conflict). Translates to 409."""

    def __init__(
        self,
        message: str,
        resource_type: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details)
        self.resource_type = resource_type


class UnauthorizedError(ServiceError):
    """Permission denied. API layer should translate to 403."""

    def __init__(
        self,
        action: str,
        reason: str | None = None,
        details: dict | None = None,
    ) -> None:
        message = f"Not authorized: {action}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message, details)
        self.action = action
        self.reason = reason


class ServiceUnavailableError(ServiceError):
    """Service not available. API layer should translate to 503."""

    def __init__(
        self,
        service_name: str,
        reason: str | None = None,
        details: dict | None = None,
    ) -> None:
        message = f"Service unavailable: {service_name}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message, details)
        self.service_name = service_name
        self.reason = reason


# =============================================================================
# BASE SERVICE CLASSES
# =============================================================================


class BaseService:
    """
    Base class for session-based services.

    Provides:
    - Database session management
    - Structured logging with service context
    - Common error handling patterns

    Usage:
        class TaskService(BaseService):
            service_name = "task"

            async def get(self, task_id: UUID) -> TaskTable | None:
                result = await self.session.execute(
                    select(TaskTable).where(TaskTable.id == task_id)
                )
                return result.scalar_one_or_none()

        # Create via factory
        def get_task_service(session: AsyncSession) -> TaskService:
            return TaskService(session)
    """

    # Service name for logging (override in subclasses)
    service_name: ClassVar[str] = "base"

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize the service with a database session.

        Args:
            session: SQLAlchemy async session for database operations
        """
        self.session = session
        self.log = logger.bind(service=self.service_name)


class SingletonService:
    """
    Base class for stateless singleton services.

    Use for services that don't need a database session per-request:
    - Audit logging
    - Permission checking
    - Configuration-based services

    Usage:
        class AuditService(SingletonService):
            service_name = "audit"

            async def log_event(self, event: AuditEvent) -> None:
                self.log.info("Audit event", **event.dict())
    """

    # Service name for logging (override in subclasses)
    service_name: ClassVar[str] = "singleton"

    def __init__(self) -> None:
        """Initialize the singleton service."""
        self.log = logger.bind(service=self.service_name)


# =============================================================================
# SINGLETON HOLDER PATTERN
# =============================================================================


class SingletonHolder[T]:
    """
    Generic singleton holder for service instances.

    Provides thread-safe (for async) singleton pattern with
    explicit initialization and cleanup.

    Usage:
        class _AuditHolder(SingletonHolder[AuditService]):
            def create_instance(self) -> AuditService:
                return AuditService()

        _audit_holder = _AuditHolder()

        def get_audit_service() -> AuditService:
            return _audit_holder.get()
    """

    def __init__(self) -> None:
        self._instance: T | None = None

    def create_instance(self) -> T:
        """Override to create the service instance."""
        raise NotImplementedError("Subclass must implement create_instance()")

    def get(self) -> T:
        """Get or create the singleton instance."""
        if self._instance is None:
            self._instance = self.create_instance()
        return self._instance

    def set(self, instance: T) -> None:
        """Explicitly set the instance (for testing/dependency injection)."""
        self._instance = instance

    def clear(self) -> None:
        """Clear the instance (for testing/cleanup)."""
        self._instance = None

    @property
    def is_initialized(self) -> bool:
        """Check if the instance is initialized."""
        return self._instance is not None
