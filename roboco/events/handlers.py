"""
Event Handlers

Workflow trigger handlers that respond to system events.
"""

import structlog

from roboco.events.bus import Event, EventType, get_event_bus

logger = structlog.get_logger()


async def handle_task_status_change(event: Event) -> None:
    """
    Handle task status change events.

    Triggers:
    - Notify PM when task blocked
    - Notify QA when task ready for review
    - Notify Documenter when task ready for docs
    - Notify developer when QA fails
    """
    task_id = event.data.get("task_id")
    agent_id = event.source_agent
    event_type = event.type

    logger.info(
        "Task status changed",
        task_id=task_id,
        event_type=event_type.value,
        agent=agent_id,
    )

    # Import here to avoid circular imports
    from roboco.services.notification import NotificationService

    notification_service = NotificationService()

    if event_type == EventType.TASK_BLOCKED:
        # Notify the cell PM
        blocker_reason = event.data.get("reason", "Unknown blocker")
        team = event.data.get("team")

        if team:
            pm_id = f"{team[:2]}-pm"  # e.g., "backend" -> "be-pm"
            await notification_service.send_blocker_notification(
                task_id=task_id,
                blocker_reason=blocker_reason,
                from_agent=agent_id,
                to_pm=pm_id,
            )

    elif event_type == EventType.TASK_AWAITING_QA:
        # Notify the QA agent
        team = event.data.get("team")
        if team:
            qa_id = f"{team[:2]}-qa"
            await notification_service.send_qa_ready_notification(
                task_id=task_id,
                from_agent=agent_id,
                to_qa=qa_id,
            )

    elif event_type == EventType.TASK_QA_FAILED:
        # Notify the original developer
        developer_id = event.data.get("assigned_to")
        qa_notes = event.data.get("qa_notes", "See task for details")

        if developer_id:
            await notification_service.send_qa_failed_notification(
                task_id=task_id,
                qa_notes=qa_notes,
                to_developer=developer_id,
            )

    elif event_type == EventType.TASK_AWAITING_DOCS:
        # Notify the documenter
        team = event.data.get("team")
        if team:
            doc_id = f"{team[:2]}-doc"
            await notification_service.send_docs_ready_notification(
                task_id=task_id,
                from_agent=agent_id,
                to_documenter=doc_id,
            )


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
    task_id = event.data.get("task_id")
    handoff_id = event.data.get("handoff_id")
    from_agent = event.source_agent
    team = event.data.get("team")

    logger.info(
        "Handoff created",
        task_id=task_id,
        handoff_id=handoff_id,
        from_agent=from_agent,
    )

    from roboco.services.notification import NotificationService

    notification_service = NotificationService()

    if team:
        doc_id = f"{team[:2]}-doc"
        await notification_service.send_handoff_notification(
            task_id=task_id,
            handoff_id=handoff_id,
            from_agent=from_agent,
            to_documenter=doc_id,
        )


async def handle_qa_result(event: Event) -> None:
    """
    Handle QA result events.

    Triggers:
    - Resume developer agent if waiting on QA result
    - Notify appropriate parties
    """
    task_id = event.data.get("task_id")
    passed = event.type == EventType.TASK_QA_PASSED
    developer_id = event.data.get("assigned_to")
    qa_notes = event.data.get("qa_notes")

    logger.info(
        "QA result",
        task_id=task_id,
        passed=passed,
        developer=developer_id,
    )

    # Check if developer agent is in WAITING_LONG state

    # Get orchestrator instance (if running)
    try:
        from roboco.bootstrap import _orchestrator

        if _orchestrator and developer_id:
            waiting = _orchestrator.get_waiting_agents()
            if developer_id in waiting:
                record = waiting[developer_id]
                if record.waiting_for == "qa_result":
                    # Resume the agent
                    await _orchestrator.resolve_wait(
                        agent_id=developer_id,
                        resolution={
                            "passed": passed,
                            "notes": qa_notes,
                            "task_id": task_id,
                        },
                    )
    except (ImportError, AttributeError):
        pass  # Orchestrator not running


async def handle_blocker_resolved(event: Event) -> None:
    """
    Handle blocker resolution events.

    Triggers:
    - Resume blocked agent
    - Update task status
    """
    task_id = event.data.get("task_id")
    agent_id = event.data.get("agent_id")
    resolution = event.data.get("resolution", "Resolved")

    logger.info(
        "Blocker resolved",
        task_id=task_id,
        agent=agent_id,
    )

    # Resume agent if waiting
    try:
        from roboco.bootstrap import _orchestrator

        if _orchestrator and agent_id:
            waiting = _orchestrator.get_waiting_agents()
            if agent_id in waiting:
                record = waiting[agent_id]
                if record.waiting_for == "blocker_resolution":
                    await _orchestrator.resolve_wait(
                        agent_id=agent_id,
                        resolution={
                            "details": resolution,
                            "task_id": task_id,
                        },
                    )
    except (ImportError, AttributeError):
        pass


async def handle_question_answered(event: Event) -> None:
    """
    Handle question answered events.

    Triggers:
    - Resume agent waiting for answer
    """
    question_id = event.data.get("question_id")
    agent_id = event.data.get("asking_agent")
    answer = event.data.get("answer")

    logger.info(
        "Question answered",
        question_id=question_id,
        agent=agent_id,
    )

    # Resume agent if waiting
    try:
        from roboco.bootstrap import _orchestrator

        if _orchestrator and agent_id:
            waiting = _orchestrator.get_waiting_agents()
            if agent_id in waiting:
                record = waiting[agent_id]
                if record.waiting_for == "answer":
                    await _orchestrator.resolve_wait(
                        agent_id=agent_id,
                        resolution={
                            "answer": answer,
                            "question_id": question_id,
                        },
                    )
    except (ImportError, AttributeError):
        pass


def register_default_handlers(bus=None) -> None:
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
