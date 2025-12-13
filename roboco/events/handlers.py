"""
Event Handlers

Workflow trigger handlers that respond to system events.
"""

from typing import Any

import structlog

from roboco.events.bus import Event, EventType, get_event_bus

logger = structlog.get_logger()


async def _handle_task_blocked(
    event: Event,
    task_id: str,
    notification_service: Any,
) -> None:
    """Handle blocked task notification."""
    team = event.data.get("team")
    if not team:
        return
    blocker_reason = event.data.get("reason", "Unknown blocker")
    pm_id = f"{team[:2]}-pm"
    await notification_service.send_blocker_notification(
        task_id=task_id,
        blocker_reason=blocker_reason,
        from_agent=event.source_agent,
        to_pm=pm_id,
    )


async def _handle_task_awaiting_qa(
    event: Event,
    task_id: str,
    notification_service: Any,
) -> None:
    """Handle task awaiting QA notification."""
    team = event.data.get("team")
    if not team:
        return
    qa_id = f"{team[:2]}-qa"
    await notification_service.send_qa_ready_notification(
        task_id=task_id,
        from_agent=event.source_agent,
        to_qa=qa_id,
    )


async def _handle_task_qa_failed(
    event: Event,
    task_id: str,
    notification_service: Any,
) -> None:
    """Handle QA failed notification."""
    developer_id = event.data.get("assigned_to")
    if not developer_id:
        return
    qa_notes = event.data.get("qa_notes", "See task for details")
    await notification_service.send_qa_failed_notification(
        task_id=task_id,
        qa_notes=qa_notes,
        to_developer=developer_id,
    )


async def _handle_task_awaiting_docs(
    event: Event,
    task_id: str,
    notification_service: Any,
) -> None:
    """Handle task awaiting docs notification."""
    team = event.data.get("team")
    if not team:
        return
    doc_id = f"{team[:2]}-doc"
    await notification_service.send_docs_ready_notification(
        task_id=task_id,
        from_agent=event.source_agent,
        to_documenter=doc_id,
    )


async def handle_task_status_change(event: Event) -> None:
    """
    Handle task status change events.

    Triggers:
    - Notify PM when task blocked
    - Notify QA when task ready for review
    - Notify Documenter when task ready for docs
    - Notify developer when QA fails
    """
    task_id_raw = event.data.get("task_id")
    task_id = str(task_id_raw) if task_id_raw else ""

    logger.info(
        "Task status changed",
        task_id=task_id,
        event_type=event.type.value,
        agent=event.source_agent,
    )

    from roboco.services.notification import NotificationService  # noqa: PLC0415

    notification_service = NotificationService()

    handlers = {
        EventType.TASK_BLOCKED: _handle_task_blocked,
        EventType.TASK_AWAITING_QA: _handle_task_awaiting_qa,
        EventType.TASK_QA_FAILED: _handle_task_qa_failed,
        EventType.TASK_AWAITING_DOCS: _handle_task_awaiting_docs,
    }

    handler = handlers.get(event.type)
    if handler:
        await handler(event, task_id, notification_service)


async def handle_session_boundary(event: Event) -> None:
    """
    Handle session boundary events.

    Triggers:
    - Create new session when old one closes
    - Log session metrics
    """
    session_id = event.data.get("session_id")
    group_id = event.data.get("group_id")
    reason = event.data.get("reason", "unknown")

    logger.info(
        "Session boundary reached",
        session_id=session_id,
        group_id=group_id,
        reason=reason,
    )

    # Session creation is handled by the message API when needed
    # Here we just log for metrics/monitoring


async def handle_handoff_created(event: Event) -> None:
    """
    Handle handoff creation events.

    Triggers:
    - Notify documenter that handoff is ready
    - Update task status tracking
    """
    task_id_raw = event.data.get("task_id")
    handoff_id_raw = event.data.get("handoff_id")
    task_id = str(task_id_raw) if task_id_raw else ""
    handoff_id = str(handoff_id_raw) if handoff_id_raw else ""
    from_agent = event.source_agent
    team = event.data.get("team")

    logger.info(
        "Handoff created",
        task_id=task_id,
        handoff_id=handoff_id,
        from_agent=from_agent,
    )

    from roboco.services.notification import NotificationService  # noqa: PLC0415

    notification_service = NotificationService()

    if team:
        doc_id = f"{team[:2]}-doc"
        await notification_service.send_handoff_notification(
            task_id=task_id,
            handoff_id=handoff_id,
            from_agent=from_agent,
            to_documenter=doc_id,
        )


async def _try_resolve_agent_wait(
    agent_id: str | None,
    waiting_for: str,
    resolution: dict[str, Any],
) -> None:
    """Try to resolve a waiting agent if orchestrator is running."""
    if not agent_id:
        return
    try:
        from roboco.bootstrap import _BootstrapHolder  # noqa: PLC0415

        orchestrator = _BootstrapHolder.orchestrator
        if not orchestrator:
            return
        waiting = orchestrator.get_waiting_agents()
        if agent_id not in waiting:
            return
        record = waiting[agent_id]
        if record.waiting_for == waiting_for:
            await orchestrator.resolve_wait(agent_id=agent_id, resolution=resolution)
    except (ImportError, AttributeError):
        pass


async def handle_qa_result(event: Event) -> None:
    """Handle QA result events."""
    task_id = event.data.get("task_id")
    passed = event.type == EventType.TASK_QA_PASSED
    developer_id = event.data.get("assigned_to")

    logger.info(
        "QA result",
        task_id=task_id,
        passed=passed,
        developer=developer_id,
    )

    await _try_resolve_agent_wait(
        developer_id,
        "qa_result",
        {"passed": passed, "notes": event.data.get("qa_notes"), "task_id": task_id},
    )


async def handle_blocker_resolved(event: Event) -> None:
    """Handle blocker resolution events."""
    task_id = event.data.get("task_id")
    agent_id = event.data.get("agent_id")
    resolution = event.data.get("resolution", "Resolved")

    logger.info("Blocker resolved", task_id=task_id, agent=agent_id)

    await _try_resolve_agent_wait(
        agent_id,
        "blocker_resolution",
        {"details": resolution, "task_id": task_id},
    )


async def handle_question_answered(event: Event) -> None:
    """Handle question answered events."""
    question_id = event.data.get("question_id")
    agent_id = event.data.get("asking_agent")
    answer = event.data.get("answer")

    logger.info("Question answered", question_id=question_id, agent=agent_id)

    await _try_resolve_agent_wait(
        agent_id,
        "answer",
        {"answer": answer, "question_id": question_id},
    )


def register_default_handlers(bus: Any = None) -> None:
    """Register all default event handlers."""
    if bus is None:
        bus = get_event_bus()

    # Task status handlers
    task_events = [
        EventType.TASK_BLOCKED,
        EventType.TASK_AWAITING_QA,
        EventType.TASK_QA_FAILED,
        EventType.TASK_AWAITING_DOCS,
    ]
    for event_type in task_events:
        bus.subscribe(event_type, handle_task_status_change)

    # Session handlers
    bus.subscribe(EventType.SESSION_CLOSED, handle_session_boundary)
    bus.subscribe(EventType.SESSION_TIMEOUT, handle_session_boundary)

    # Handoff handlers
    bus.subscribe(EventType.HANDOFF_CREATED, handle_handoff_created)

    # QA result handlers
    bus.subscribe(EventType.TASK_QA_PASSED, handle_qa_result)
    bus.subscribe(EventType.TASK_QA_FAILED, handle_qa_result)

    # Blocker handlers
    bus.subscribe(EventType.BLOCKER_RESOLVED, handle_blocker_resolved)

    # Question handlers
    bus.subscribe(EventType.QUESTION_ANSWERED, handle_question_answered)

    logger.info("Default event handlers registered")
