# Design Principles

Foundational design principles for building maintainable, scalable software in the RoboCo system.

---

## Table of Contents

1. [SOLID Principles](#solid-principles)
2. [DRY - Don't Repeat Yourself](#dry---dont-repeat-yourself)
3. [KISS - Keep It Simple, Stupid](#kiss---keep-it-simple-stupid)
4. [YAGNI - You Aren't Gonna Need It](#yagni---you-arent-gonna-need-it)
5. [Separation of Concerns](#separation-of-concerns)
6. [Composition Over Inheritance](#composition-over-inheritance)
7. [Fail Fast](#fail-fast)
8. [Law of Demeter](#law-of-demeter)
9. [Dependency Injection](#dependency-injection)
10. [Immutability](#immutability)

---

## SOLID Principles

### ARCH-001: Single Responsibility Principle (SRP)

**Severity:** WARNING
**Principle:** A class should have one, and only one, reason to change.

Each module, class, or function should do one thing well.

```python
# Bad - Multiple responsibilities
class TaskManager:
    def create_task(self, data: TaskCreate) -> Task:
        # Creates task
        ...

    def send_notification(self, task: Task, recipient: str) -> None:
        # Sends notification
        ...

    def generate_report(self, tasks: list[Task]) -> Report:
        # Generates report
        ...

# Good - Single responsibility per class
class TaskService:
    def create(self, data: TaskCreate) -> Task:
        ...

class NotificationService:
    def send(self, notification: Notification) -> None:
        ...

class ReportService:
    def generate(self, tasks: list[Task]) -> Report:
        ...
```

### ARCH-002: Open/Closed Principle (OCP)

**Severity:** WARNING
**Principle:** Software entities should be open for extension, but closed for modification.

Design systems that can be extended without modifying existing code.

```python
# Bad - Need to modify class for new types
class TaskProcessor:
    def process(self, task: Task) -> None:
        if task.type == "bug":
            self._process_bug(task)
        elif task.type == "feature":
            self._process_feature(task)
        elif task.type == "refactor":  # Added later - modifies existing code
            self._process_refactor(task)

# Good - Extend via new classes
from abc import ABC, abstractmethod

class TaskProcessor(ABC):
    @abstractmethod
    def process(self, task: Task) -> None:
        ...

class BugProcessor(TaskProcessor):
    def process(self, task: Task) -> None:
        ...

class FeatureProcessor(TaskProcessor):
    def process(self, task: Task) -> None:
        ...

class RefactorProcessor(TaskProcessor):  # Added without modifying existing code
    def process(self, task: Task) -> None:
        ...

# Registry pattern for extension
PROCESSORS: dict[str, type[TaskProcessor]] = {
    "bug": BugProcessor,
    "feature": FeatureProcessor,
    "refactor": RefactorProcessor,
}

def get_processor(task_type: str) -> TaskProcessor:
    return PROCESSORS[task_type]()
```

### ARCH-003: Liskov Substitution Principle (LSP)

**Severity:** ERROR
**Principle:** Objects of a superclass should be replaceable with objects of its subclasses without breaking the application.

Derived classes must be substitutable for their base classes.

```python
# Bad - Subclass violates base class contract
class Bird:
    def fly(self) -> None:
        print("Flying")

class Penguin(Bird):
    def fly(self) -> None:
        raise NotImplementedError("Penguins can't fly!")  # Violates LSP

# Good - Proper abstraction
from abc import ABC, abstractmethod

class Bird(ABC):
    @abstractmethod
    def move(self) -> None:
        ...

class FlyingBird(Bird):
    def move(self) -> None:
        self.fly()

    def fly(self) -> None:
        print("Flying")

class SwimmingBird(Bird):
    def move(self) -> None:
        self.swim()

    def swim(self) -> None:
        print("Swimming")
```

### ARCH-004: Interface Segregation Principle (ISP)

**Severity:** WARNING
**Principle:** Many client-specific interfaces are better than one general-purpose interface.

Don't force clients to depend on methods they don't use.

```python
# Bad - Fat interface
class Worker(ABC):
    @abstractmethod
    def work(self) -> None: ...

    @abstractmethod
    def eat(self) -> None: ...

    @abstractmethod
    def sleep(self) -> None: ...

class Robot(Worker):
    def work(self) -> None:
        ...

    def eat(self) -> None:
        raise NotImplementedError()  # Robots don't eat

    def sleep(self) -> None:
        raise NotImplementedError()  # Robots don't sleep

# Good - Segregated interfaces
class Workable(ABC):
    @abstractmethod
    def work(self) -> None: ...

class Feedable(ABC):
    @abstractmethod
    def eat(self) -> None: ...

class Sleepable(ABC):
    @abstractmethod
    def sleep(self) -> None: ...

class Human(Workable, Feedable, Sleepable):
    def work(self) -> None: ...
    def eat(self) -> None: ...
    def sleep(self) -> None: ...

class Robot(Workable):
    def work(self) -> None: ...
```

### ARCH-005: Dependency Inversion Principle (DIP)

**Severity:** ERROR
**Principle:** Depend on abstractions, not concretions.

High-level modules should not depend on low-level modules.

```python
# Bad - High-level depends on low-level
class PostgreSQLDatabase:
    def query(self, sql: str) -> list[dict]:
        ...

class TaskRepository:
    def __init__(self) -> None:
        self.db = PostgreSQLDatabase()  # Tight coupling

    def get_task(self, task_id: str) -> Task:
        return self.db.query(f"SELECT * FROM tasks WHERE id = '{task_id}'")

# Good - Depend on abstraction
from abc import ABC, abstractmethod

class Database(ABC):
    @abstractmethod
    async def query(self, sql: str, params: dict) -> list[dict]:
        ...

class PostgreSQLDatabase(Database):
    async def query(self, sql: str, params: dict) -> list[dict]:
        ...

class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db  # Depends on abstraction

    async def get_task(self, task_id: str) -> Task:
        result = await self.db.query(
            "SELECT * FROM tasks WHERE id = :id",
            {"id": task_id}
        )
        return Task(**result[0])
```

---

## DRY - Don't Repeat Yourself

### ARCH-010: No Code Duplication

**Severity:** WARNING
**Principle:** Every piece of knowledge must have a single, unambiguous, authoritative representation.

Eliminate duplication of logic, data, and knowledge.

```python
# Bad - Duplicated validation logic
class UserService:
    def create_user(self, email: str) -> User:
        if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email):
            raise ValueError("Invalid email")
        ...

class InviteService:
    def send_invite(self, email: str) -> None:
        if not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email):  # Duplicated!
            raise ValueError("Invalid email")
        ...

# Good - Single source of truth
EMAIL_PATTERN = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")

def validate_email(email: str) -> str:
    """Validate and return email or raise ValueError."""
    if not EMAIL_PATTERN.match(email):
        raise ValueError(f"Invalid email: {email}")
    return email.lower()

class UserService:
    def create_user(self, email: str) -> User:
        validated_email = validate_email(email)
        ...

class InviteService:
    def send_invite(self, email: str) -> None:
        validated_email = validate_email(email)
        ...
```

### ARCH-011: Extract Common Patterns

**Severity:** WARNING

When you see the same pattern three times, extract it.

```python
# Bad - Repeated error handling pattern
async def get_user(user_id: str) -> User:
    try:
        result = await db.query("SELECT * FROM users WHERE id = :id", {"id": user_id})
        if not result:
            raise NotFoundError(f"User {user_id} not found")
        return User(**result[0])
    except DatabaseError as e:
        logger.error("Database error", error=str(e))
        raise

async def get_task(task_id: str) -> Task:
    try:
        result = await db.query("SELECT * FROM tasks WHERE id = :id", {"id": task_id})
        if not result:
            raise NotFoundError(f"Task {task_id} not found")
        return Task(**result[0])
    except DatabaseError as e:
        logger.error("Database error", error=str(e))
        raise

# Good - Extract common pattern
T = TypeVar('T')

async def get_by_id(
    table: str,
    id_value: str,
    model: type[T],
    entity_name: str,
) -> T:
    """Generic get-by-id with error handling."""
    try:
        result = await db.query(
            f"SELECT * FROM {table} WHERE id = :id",
            {"id": id_value}
        )
        if not result:
            raise NotFoundError(f"{entity_name} {id_value} not found")
        return model(**result[0])
    except DatabaseError as e:
        logger.error("Database error", table=table, error=str(e))
        raise

async def get_user(user_id: str) -> User:
    return await get_by_id("users", user_id, User, "User")

async def get_task(task_id: str) -> Task:
    return await get_by_id("tasks", task_id, Task, "Task")
```

### ARCH-012: But Avoid False DRY

**Severity:** INFO

Not all similar code is duplicate. Don't abstract too early.

```python
# False DRY - These look similar but serve different purposes
def format_user_name(user: User) -> str:
    return f"{user.first_name} {user.last_name}"

def format_agent_name(agent: Agent) -> str:
    return f"{agent.role}: {agent.slug}"

# Don't force these into a single function just because they both "format names"
# They have different semantics and will evolve independently
```

---

## KISS - Keep It Simple, Stupid

### ARCH-020: Prefer Simple Solutions

**Severity:** WARNING
**Principle:** The simplest solution that works is often the best.

Avoid unnecessary complexity.

```python
# Bad - Over-engineered solution
class TaskStatusStrategyFactory:
    _strategies: dict[str, type[TaskStatusStrategy]] = {}

    @classmethod
    def register(cls, status: str) -> Callable:
        def decorator(strategy_class: type[TaskStatusStrategy]) -> type[TaskStatusStrategy]:
            cls._strategies[status] = strategy_class
            return strategy_class
        return decorator

    @classmethod
    def create(cls, task: Task) -> TaskStatusStrategy:
        return cls._strategies[task.status]()

@TaskStatusStrategyFactory.register("pending")
class PendingStatusStrategy(TaskStatusStrategy):
    def can_transition_to(self, new_status: str) -> bool:
        return new_status in ["claimed", "cancelled"]

# Good - Simple and clear
VALID_TRANSITIONS = {
    "pending": {"claimed", "cancelled"},
    "claimed": {"in_progress", "pending"},
    "in_progress": {"completed", "blocked", "paused"},
    # ... etc
}

def can_transition(current: str, new: str) -> bool:
    return new in VALID_TRANSITIONS.get(current, set())
```

### ARCH-021: Avoid Premature Abstraction

**Severity:** WARNING

Don't abstract before you have concrete requirements.

```python
# Bad - Premature abstraction
class AbstractDataProcessor(ABC):
    @abstractmethod
    def preprocess(self, data: Any) -> Any: ...

    @abstractmethod
    def process(self, data: Any) -> Any: ...

    @abstractmethod
    def postprocess(self, data: Any) -> Any: ...

    def run(self, data: Any) -> Any:
        data = self.preprocess(data)
        data = self.process(data)
        return self.postprocess(data)

# When you only have one implementation!
class TaskDataProcessor(AbstractDataProcessor):
    def preprocess(self, data: Any) -> Any:
        return data  # Does nothing

    def process(self, data: Any) -> Any:
        return transform_task(data)

    def postprocess(self, data: Any) -> Any:
        return data  # Does nothing

# Good - Start simple, abstract when needed
def process_task_data(data: dict) -> Task:
    return transform_task(data)

# Later, when you ACTUALLY need abstraction:
# Then create the base class with proven patterns
```

### ARCH-022: Readable Over Clever

**Severity:** WARNING

Code is read more often than written. Optimize for readability.

```python
# Bad - Clever but unreadable
result = reduce(
    lambda acc, x: {**acc, x[0]: x[1]},
    filter(lambda t: t[1] > 0, map(lambda k: (k, data.get(k, 0)), keys)),
    {}
)

# Good - Clear and readable
result = {}
for key in keys:
    value = data.get(key, 0)
    if value > 0:
        result[key] = value
```

---

## YAGNI - You Aren't Gonna Need It

### ARCH-030: Don't Build Speculatively

**Severity:** WARNING
**Principle:** Only implement features when you actually need them.

```python
# Bad - Building for hypothetical future
class TaskService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        queue: MessageQueue,
        analytics: AnalyticsService,
        audit_log: AuditLogService,
        rate_limiter: RateLimiter,
        circuit_breaker: CircuitBreaker,
        feature_flags: FeatureFlagService,
    ) -> None:
        # Most of these aren't used yet
        ...

    def create_task(self, data: TaskCreate) -> Task:
        # Just creates a task in the database
        return self.db.create_task(data)

# Good - Only what you need now
class TaskService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create_task(self, data: TaskCreate) -> Task:
        return await self.db.create_task(data)

# Add cache, queue, etc. when you actually need them
```

### ARCH-031: Delete Unused Code

**Severity:** ERROR
**Tools:** vulture

Remove dead code. It's not "just in case" - it's noise.

```python
# Bad - Keeping "just in case" code
class TaskService:
    def create_task(self, data: TaskCreate) -> Task:
        ...

    # def create_task_v2(self, data: TaskCreateV2) -> Task:
    #     """New version - not sure if we'll use this"""
    #     ...

    # def _experimental_feature(self) -> None:
    #     """Might need this later"""
    #     pass

# Good - Clean codebase
class TaskService:
    def create_task(self, data: TaskCreate) -> Task:
        ...

# Use version control for history, not comments
```

---

## Separation of Concerns

### ARCH-040: Layer Architecture

**Severity:** ERROR

Organize code into distinct layers with clear responsibilities.

```
┌─────────────────────────────────────────┐
│           API Layer (Routes)             │  ← HTTP handling, validation
├─────────────────────────────────────────┤
│         Service Layer (Business)         │  ← Business logic, orchestration
├─────────────────────────────────────────┤
│       Repository Layer (Data Access)     │  ← Database queries, caching
├─────────────────────────────────────────┤
│           Model Layer (Domain)           │  ← Data structures, entities
└─────────────────────────────────────────┘
```

```python
# Good - Clear layer separation

# models/task.py - Domain entities
class Task(BaseModel):
    id: UUID
    title: str
    status: TaskStatus

# repositories/task.py - Data access
class TaskRepository:
    async def get_by_id(self, task_id: UUID) -> Task | None:
        result = await self.db.query(...)
        return Task(**result) if result else None

# services/task.py - Business logic
class TaskService:
    def __init__(self, repo: TaskRepository, notifier: NotificationService) -> None:
        self.repo = repo
        self.notifier = notifier

    async def complete_task(self, task_id: UUID) -> Task:
        task = await self.repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        task.status = TaskStatus.COMPLETED
        await self.repo.update(task)
        await self.notifier.notify_completion(task)
        return task

# api/routes/tasks.py - HTTP handling
@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: UUID, service: TaskService = Depends()) -> TaskResponse:
    task = await service.complete_task(task_id)
    return TaskResponse.from_orm(task)
```

### ARCH-041: No Business Logic in Routes

**Severity:** ERROR

API routes should only handle HTTP concerns.

```python
# Bad - Business logic in route
@router.post("/tasks")
async def create_task(request: TaskCreate, db: AsyncSession = Depends()) -> TaskResponse:
    # Validation
    if len(request.title) < 3:
        raise HTTPException(400, "Title too short")

    # Business logic (should be in service)
    task = Task(**request.dict())
    task.created_at = datetime.now(UTC)
    task.status = TaskStatus.PENDING

    if request.assigned_to:
        agent = await db.query(Agent).filter_by(id=request.assigned_to).first()
        if agent is None:
            raise HTTPException(400, "Agent not found")
        task.assigned_to = agent.id

    db.add(task)
    await db.commit()

    # Send notification (should be in service)
    await send_notification(task.assigned_to, f"New task: {task.title}")

    return TaskResponse.from_orm(task)

# Good - Route delegates to service
@router.post("/tasks")
async def create_task(
    request: TaskCreate,
    service: TaskService = Depends(),
) -> TaskResponse:
    task = await service.create(request)
    return TaskResponse.from_orm(task)
```

### ARCH-042: No Database Queries in Routes

**Severity:** ERROR

Database access should be in repository or service layer.

```python
# Bad - Direct DB access in route
@router.get("/tasks")
async def list_tasks(
    status: TaskStatus | None = None,
    db: AsyncSession = Depends(),
) -> list[TaskResponse]:
    query = select(Task)
    if status:
        query = query.where(Task.status == status)
    result = await db.execute(query)
    return [TaskResponse.from_orm(t) for t in result.scalars()]

# Good - Delegate to service/repository
@router.get("/tasks")
async def list_tasks(
    status: TaskStatus | None = None,
    service: TaskService = Depends(),
) -> list[TaskResponse]:
    tasks = await service.list(status=status)
    return [TaskResponse.from_orm(t) for t in tasks]
```

---

## Composition Over Inheritance

### ARCH-050: Prefer Composition

**Severity:** WARNING
**Principle:** Favor object composition over class inheritance.

```python
# Bad - Deep inheritance hierarchy
class BaseProcessor:
    def process(self, data: Any) -> Any:
        ...

class ValidatingProcessor(BaseProcessor):
    def process(self, data: Any) -> Any:
        self.validate(data)
        return super().process(data)

class LoggingValidatingProcessor(ValidatingProcessor):
    def process(self, data: Any) -> Any:
        self.log_start(data)
        result = super().process(data)
        self.log_end(result)
        return result

class CachingLoggingValidatingProcessor(LoggingValidatingProcessor):
    ...  # Getting ridiculous

# Good - Composition with mixins or decorators
class TaskProcessor:
    def __init__(
        self,
        validator: Validator | None = None,
        logger: Logger | None = None,
        cache: Cache | None = None,
    ) -> None:
        self.validator = validator
        self.logger = logger
        self.cache = cache

    def process(self, data: Any) -> Any:
        if self.validator:
            self.validator.validate(data)
        if self.logger:
            self.logger.log_start(data)

        result = self._do_process(data)

        if self.cache:
            self.cache.set(data.id, result)
        if self.logger:
            self.logger.log_end(result)

        return result
```

### ARCH-051: Use Protocols Over ABC

**Severity:** INFO
**Python:** Use Protocol for structural typing when possible.

```python
# Good - Protocol-based typing
from typing import Protocol

class Repository(Protocol):
    async def get(self, id: str) -> dict | None: ...
    async def save(self, entity: dict) -> None: ...

class TaskService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

# Any class with get/save methods works, no inheritance needed
class InMemoryRepo:
    async def get(self, id: str) -> dict | None:
        return self.data.get(id)

    async def save(self, entity: dict) -> None:
        self.data[entity["id"]] = entity

# Works with TaskService without explicit inheritance!
```

---

## Fail Fast

### ARCH-060: Validate Early

**Severity:** ERROR
**Principle:** Detect and report errors as early as possible.

```python
# Bad - Late failure
def process_order(order: dict) -> Receipt:
    # ... lots of processing ...

    # Fails late after doing work
    if order.get("customer_id") is None:
        raise ValueError("Missing customer_id")

    # More processing that depends on customer_id
    ...

# Good - Fail fast
def process_order(order: dict) -> Receipt:
    # Validate immediately
    if order.get("customer_id") is None:
        raise ValueError("Missing customer_id")
    if order.get("items") is None or len(order["items"]) == 0:
        raise ValueError("Order must have items")

    # Now process with confidence
    ...
```

### ARCH-061: Use Guard Clauses

**Severity:** WARNING

Return early for invalid states instead of deep nesting.

```python
# Bad - Deep nesting
def process_task(task: Task | None, agent: Agent | None) -> Result:
    if task is not None:
        if task.status == TaskStatus.PENDING:
            if agent is not None:
                if agent.can_claim(task):
                    return do_process(task, agent)
                else:
                    return Result(error="Agent cannot claim")
            else:
                return Result(error="No agent")
        else:
            return Result(error="Task not pending")
    else:
        return Result(error="No task")

# Good - Guard clauses
def process_task(task: Task | None, agent: Agent | None) -> Result:
    if task is None:
        return Result(error="No task")
    if task.status != TaskStatus.PENDING:
        return Result(error="Task not pending")
    if agent is None:
        return Result(error="No agent")
    if not agent.can_claim(task):
        return Result(error="Agent cannot claim")

    return do_process(task, agent)
```

---

## Law of Demeter

### ARCH-070: Don't Talk to Strangers

**Severity:** WARNING
**Principle:** Only talk to immediate friends, not friends of friends.

```python
# Bad - Chained method calls
def get_customer_city(order: Order) -> str:
    return order.customer.address.city.name

# If any of these are None, it fails
# Also tightly coupled to internal structure

# Good - Ask, don't dig
class Order:
    def get_customer_city(self) -> str:
        return self.customer.get_city_name()

class Customer:
    def get_city_name(self) -> str:
        if self.address and self.address.city:
            return self.address.city.name
        return "Unknown"
```

---

## Dependency Injection

### ARCH-080: Inject Dependencies

**Severity:** ERROR
**Principle:** Dependencies should be provided, not created internally.

```python
# Bad - Creates own dependencies
class TaskService:
    def __init__(self) -> None:
        self.db = PostgresDatabase()  # Hard to test
        self.cache = RedisCache()     # Hard to swap
        self.notifier = EmailNotifier()

# Good - Inject dependencies
class TaskService:
    def __init__(
        self,
        db: Database,
        cache: Cache,
        notifier: Notifier,
    ) -> None:
        self.db = db
        self.cache = cache
        self.notifier = notifier

# FastAPI dependency injection
def get_task_service(
    db: Database = Depends(get_database),
    cache: Cache = Depends(get_cache),
    notifier: Notifier = Depends(get_notifier),
) -> TaskService:
    return TaskService(db, cache, notifier)
```

---

## Immutability

### ARCH-090: Prefer Immutable Data

**Severity:** WARNING
**Principle:** Immutable objects are easier to reason about and safer in concurrent code.

```python
# Bad - Mutable state
class Task:
    def __init__(self, title: str) -> None:
        self.title = title
        self.tags = []  # Mutable!

task = Task("Fix bug")
task.tags.append("urgent")
task.title = "Changed!"  # Mutation

# Good - Immutable with dataclasses
from dataclasses import dataclass

@dataclass(frozen=True)
class Task:
    title: str
    tags: tuple[str, ...] = ()

    def with_tag(self, tag: str) -> "Task":
        """Return new Task with additional tag."""
        return Task(
            title=self.title,
            tags=self.tags + (tag,)
        )

task = Task("Fix bug")
task_with_tag = task.with_tag("urgent")  # Returns new instance
```

### ARCH-091: Avoid Side Effects in Functions

**Severity:** WARNING

Pure functions are easier to test and reason about.

```python
# Bad - Side effects
def process_tasks(tasks: list[Task]) -> None:
    for task in tasks:
        task.processed = True  # Mutates input!
        global_counter += 1    # Global state!
        send_notification()     # Side effect!

# Good - Pure function
def process_tasks(tasks: list[Task]) -> list[ProcessedTask]:
    return [
        ProcessedTask(
            task=task,
            processed_at=datetime.now(UTC)
        )
        for task in tasks
    ]

# Handle side effects separately
processed = process_tasks(tasks)
for p in processed:
    await notifier.send(p)
```

---

## Quick Reference

### Principle Severity

| Principle | Severity | Impact |
|-----------|----------|--------|
| Liskov Substitution | ERROR | Breaks polymorphism |
| Dependency Inversion | ERROR | Prevents testing |
| No DB in Routes | ERROR | Violates layering |
| Validate Early | ERROR | Wastes resources |
| Inject Dependencies | ERROR | Untestable code |
| Single Responsibility | WARNING | Maintenance burden |
| Open/Closed | WARNING | Modification risk |
| DRY | WARNING | Bug propagation |
| KISS | WARNING | Complexity cost |
| YAGNI | WARNING | Wasted effort |
| Composition | WARNING | Rigid hierarchy |
| Immutability | WARNING | Concurrency bugs |

### Anti-Pattern Detection

| Anti-Pattern | Signs | Fix |
|--------------|-------|-----|
| God Class | Class > 500 lines | Split by responsibility |
| Feature Envy | Uses other class's data heavily | Move method to that class |
| Shotgun Surgery | One change requires many edits | Consolidate related code |
| Primitive Obsession | Many primitives for one concept | Create value object |
| Long Parameter List | > 5 parameters | Use parameter object |
| Data Clumps | Same data together often | Create class for data |
| Speculative Generality | "We might need this" | Delete until needed |
