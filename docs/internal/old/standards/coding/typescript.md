# TypeScript Coding Standards

Comprehensive standards for TypeScript/React development in the RoboCo system.

---

## Table of Contents

1. [Code Style](#code-style)
2. [Type Safety](#type-safety)
3. [React Patterns](#react-patterns)
4. [State Management](#state-management)
5. [Error Handling](#error-handling)
6. [Testing](#testing)
7. [Build & Tools](#build--tools)
8. [Performance](#performance)
9. [Security](#security)

---

## Code Style

### TS-001: Strict Mode

**Severity:** ERROR
**Tools:** TypeScript compiler

All TypeScript files MUST use strict mode. No `any` types unless absolutely necessary.

```typescript
// tsconfig.json
{
  "compilerOptions": {
    "strict": true,
    "noImplicitAny": true,
    "strictNullChecks": true,
    "strictFunctionTypes": true,
    "strictBindCallApply": true,
    "noImplicitThis": true,
    "useUnknownInCatchVariables": true,
    "alwaysStrict": true
  }
}
```

### TS-002: Explicit Return Types

**Severity:** ERROR
**Tools:** ESLint (@typescript-eslint/explicit-function-return-type)

Functions MUST have explicit return type annotations.

```typescript
// Good
function processTask(taskId: string): Promise<TaskResult> {
  // ...
}

async function fetchUser(userId: string): Promise<User | null> {
  // ...
}

// Bad - Inferred return type
function processTask(taskId: string) {
  // ...
}
```

### TS-003: Interface Over Type

**Severity:** WARNING
**Tools:** ESLint

Prefer `interface` for object shapes, `type` for unions/intersections.

```typescript
// Good - Object shape with interface
interface User {
  id: string;
  name: string;
  email: string;
}

// Good - Interface for extending
interface AdminUser extends User {
  permissions: Permission[];
}

// Good - Union type
type TaskStatus = 'pending' | 'in_progress' | 'completed';

// Good - Intersection type
type WithTimestamps<T> = T & {
  createdAt: Date;
  updatedAt: Date;
};
```

### TS-004: Naming Conventions

**Severity:** ERROR
**Tools:** ESLint

| Type | Convention | Example |
|------|------------|---------|
| Interfaces | PascalCase | `User`, `TaskService` |
| Types | PascalCase | `TaskStatus`, `ApiResponse` |
| Classes | PascalCase | `TaskManager`, `UserStore` |
| Functions | camelCase | `fetchUser`, `processTask` |
| Variables | camelCase | `userId`, `taskCount` |
| Constants | SCREAMING_SNAKE | `MAX_RETRIES`, `API_BASE_URL` |
| Enums | PascalCase | `TaskStatus`, `UserRole` |
| Components | PascalCase | `TaskCard`, `UserProfile` |
| Hooks | camelCase with use prefix | `useTask`, `useAuth` |

### TS-005: Import Organization

**Severity:** WARNING
**Tools:** ESLint (import/order)

Organize imports in this order: external, internal, relative.

```typescript
// External dependencies
import React, { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';

// Internal aliases (configured in tsconfig)
import { TaskService } from '@/services/task';
import { Button } from '@/components/ui';

// Relative imports
import { TaskCard } from './TaskCard';
import type { TaskProps } from './types';
```

---

## Type Safety

### TS-010: No `any` Types

**Severity:** ERROR
**Tools:** ESLint (@typescript-eslint/no-explicit-any)

NEVER use `any`. Use `unknown`, generics, or proper types.

```typescript
// Bad
function processData(data: any): any {
  return data.value;
}

// Good - Use unknown and narrow
function processData(data: unknown): string {
  if (typeof data === 'object' && data !== null && 'value' in data) {
    return String(data.value);
  }
  throw new Error('Invalid data format');
}

// Good - Use generics
function processData<T extends { value: string }>(data: T): string {
  return data.value;
}
```

### TS-011: Use Type Guards

**Severity:** WARNING
**Tools:** Code review

Create type guards for runtime type checking.

```typescript
// Type guard function
function isUser(value: unknown): value is User {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    'name' in value &&
    typeof (value as User).id === 'string'
  );
}

// Usage
function processResponse(data: unknown): void {
  if (isUser(data)) {
    console.log(data.name); // TypeScript knows data is User
  }
}
```

### TS-012: Discriminated Unions

**Severity:** WARNING
**Tools:** Code review

Use discriminated unions for state variants.

```typescript
// Good - Discriminated union for API states
type ApiState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: Error };

// Usage with exhaustiveness checking
function renderState<T>(state: ApiState<T>): React.ReactNode {
  switch (state.status) {
    case 'idle':
      return null;
    case 'loading':
      return <Spinner />;
    case 'success':
      return <DataView data={state.data} />;
    case 'error':
      return <ErrorMessage error={state.error} />;
    default:
      // Exhaustiveness check
      const _exhaustive: never = state;
      return _exhaustive;
  }
}
```

### TS-013: Const Assertions

**Severity:** WARNING
**Tools:** Code review

Use `as const` for immutable literal types.

```typescript
// Good - Const assertion for immutable config
const ROUTES = {
  home: '/',
  tasks: '/tasks',
  settings: '/settings',
} as const;

type Route = typeof ROUTES[keyof typeof ROUTES];
// Type: '/' | '/tasks' | '/settings'

// Good - Const assertion for tuples
const tuple = [1, 'hello'] as const;
// Type: readonly [1, 'hello']
```

### TS-014: Avoid Type Assertions

**Severity:** WARNING
**Tools:** ESLint

Avoid type assertions (`as`). Use type guards or proper typing instead.

```typescript
// Bad - Type assertion
const user = response.data as User;

// Good - Validate with type guard
function isUser(data: unknown): data is User {
  // ... validation
}

const user = isUser(response.data) ? response.data : null;

// Good - Zod schema validation
import { z } from 'zod';

const UserSchema = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string().email(),
});

const user = UserSchema.parse(response.data);
```

---

## React Patterns

### TS-020: Functional Components

**Severity:** ERROR
**Tools:** ESLint

Use functional components with hooks. No class components.

```typescript
// Good - Functional component with typed props
interface TaskCardProps {
  task: Task;
  onComplete: (taskId: string) => void;
  className?: string;
}

const TaskCard: React.FC<TaskCardProps> = ({ task, onComplete, className }) => {
  const [isLoading, setIsLoading] = useState(false);

  const handleComplete = async (): Promise<void> => {
    setIsLoading(true);
    await completeTask(task.id);
    onComplete(task.id);
    setIsLoading(false);
  };

  return (
    <div className={className}>
      <h3>{task.title}</h3>
      <Button onClick={handleComplete} disabled={isLoading}>
        Complete
      </Button>
    </div>
  );
};
```

### TS-021: Custom Hooks

**Severity:** WARNING
**Tools:** Code review

Extract reusable logic into custom hooks with `use` prefix.

```typescript
// Good - Custom hook with proper types
interface UseTaskResult {
  task: Task | null;
  isLoading: boolean;
  error: Error | null;
  refetch: () => Promise<void>;
}

function useTask(taskId: string): UseTaskResult {
  const [task, setTask] = useState<Task | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchTask = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await taskService.get(taskId);
      setTask(data);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Unknown error'));
    } finally {
      setIsLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    void fetchTask();
  }, [fetchTask]);

  return { task, isLoading, error, refetch: fetchTask };
}
```

### TS-022: Memoization

**Severity:** WARNING
**Tools:** ESLint (react-hooks/exhaustive-deps)

Use `useMemo` and `useCallback` appropriately.

```typescript
// Good - Memoize expensive computation
const sortedTasks = useMemo(() => {
  return tasks.slice().sort((a, b) => b.priority - a.priority);
}, [tasks]);

// Good - Memoize callback for child components
const handleSelect = useCallback((taskId: string) => {
  setSelectedId(taskId);
  onTaskSelect?.(taskId);
}, [onTaskSelect]);

// Bad - Over-memoization (simple computation)
const fullName = useMemo(() => `${first} ${last}`, [first, last]);
// Just use: const fullName = `${first} ${last}`;
```

### TS-023: Event Handlers

**Severity:** WARNING
**Tools:** Code review

Type event handlers properly.

```typescript
// Good - Properly typed event handlers
const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
  event.preventDefault();
  // ...
};

const handleChange = (event: React.ChangeEvent<HTMLInputElement>): void => {
  setValue(event.target.value);
};

const handleClick = (event: React.MouseEvent<HTMLButtonElement>): void => {
  event.stopPropagation();
  // ...
};
```

### TS-024: Children Props

**Severity:** WARNING
**Tools:** Code review

Use `React.ReactNode` for children props.

```typescript
// Good - Typed children
interface LayoutProps {
  children: React.ReactNode;
  sidebar?: React.ReactNode;
}

const Layout: React.FC<LayoutProps> = ({ children, sidebar }) => (
  <div className="layout">
    {sidebar && <aside>{sidebar}</aside>}
    <main>{children}</main>
  </div>
);
```

---

## State Management

### TS-030: Local State First

**Severity:** WARNING
**Tools:** Code review

Prefer local component state. Only lift state when necessary.

```typescript
// Good - Local state for component-specific data
function TaskEditor(): React.ReactElement {
  const [draft, setDraft] = useState('');
  const [isValid, setIsValid] = useState(false);

  // This state doesn't need to be global
  return <textarea value={draft} onChange={(e) => setDraft(e.target.value)} />;
}
```

### TS-031: Server State with React Query

**Severity:** ERROR
**Tools:** Code review

Use React Query (TanStack Query) for server state management.

```typescript
// Good - React Query for API data
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

function TaskList(): React.ReactElement {
  const queryClient = useQueryClient();

  const { data: tasks, isLoading, error } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => taskService.list(),
  });

  const createTask = useMutation({
    mutationFn: taskService.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  if (isLoading) return <Spinner />;
  if (error) return <ErrorMessage error={error} />;

  return (
    <ul>
      {tasks.map((task) => (
        <TaskItem key={task.id} task={task} />
      ))}
    </ul>
  );
}
```

### TS-032: Zustand for Client State

**Severity:** WARNING
**Tools:** Code review

Use Zustand for global client state when needed.

```typescript
// Good - Zustand store with proper types
import { create } from 'zustand';

interface UIStore {
  sidebarOpen: boolean;
  theme: 'light' | 'dark';
  toggleSidebar: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
}

const useUIStore = create<UIStore>((set) => ({
  sidebarOpen: true,
  theme: 'light',
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setTheme: (theme) => set({ theme }),
}));
```

---

## Error Handling

### TS-040: Error Boundaries

**Severity:** ERROR
**Tools:** Code review

Wrap major UI sections in error boundaries.

```typescript
// Error boundary component
interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback: React.ReactNode;
}

class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('Error boundary caught:', error, info);
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

// Usage
<ErrorBoundary fallback={<ErrorFallback />}>
  <TaskDashboard />
</ErrorBoundary>
```

### TS-041: Type-Safe Error Handling

**Severity:** WARNING
**Tools:** Code review

Use discriminated unions for error states.

```typescript
// Result type for operations that can fail
type Result<T, E = Error> =
  | { success: true; data: T }
  | { success: false; error: E };

// Usage
async function fetchTask(id: string): Promise<Result<Task>> {
  try {
    const task = await api.get(`/tasks/${id}`);
    return { success: true, data: task };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err : new Error('Unknown error'),
    };
  }
}

// Consumer
const result = await fetchTask(id);
if (result.success) {
  console.log(result.data.title);
} else {
  console.error(result.error.message);
}
```

### TS-042: Catch Block Typing

**Severity:** ERROR
**Tools:** TypeScript (useUnknownInCatchVariables)

Handle `unknown` type in catch blocks.

```typescript
// Good - Handle unknown error type
try {
  await riskyOperation();
} catch (error: unknown) {
  if (error instanceof ApiError) {
    handleApiError(error);
  } else if (error instanceof Error) {
    handleGenericError(error);
  } else {
    handleUnknownError(String(error));
  }
}
```

---

## Testing

### TS-050: Testing Library

**Severity:** ERROR
**Tools:** Jest, Testing Library

Use React Testing Library. Test behavior, not implementation.

```typescript
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('TaskCard', () => {
  it('shows loading state while completing task', async () => {
    const user = userEvent.setup();
    const onComplete = vi.fn();

    render(<TaskCard task={mockTask} onComplete={onComplete} />);

    await user.click(screen.getByRole('button', { name: /complete/i }));

    expect(screen.getByRole('button')).toBeDisabled();
    await waitFor(() => {
      expect(onComplete).toHaveBeenCalledWith(mockTask.id);
    });
  });

  it('displays error message when task fails to load', async () => {
    render(<TaskView taskId="invalid" />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed/i);
  });
});
```

### TS-051: Mock External Dependencies

**Severity:** WARNING
**Tools:** Jest/Vitest

Mock API calls and external services.

```typescript
import { vi } from 'vitest';
import { taskService } from '@/services/task';

vi.mock('@/services/task');

describe('useTask', () => {
  it('returns task data on success', async () => {
    vi.mocked(taskService.get).mockResolvedValue(mockTask);

    const { result } = renderHook(() => useTask('task-123'));

    await waitFor(() => {
      expect(result.current.task).toEqual(mockTask);
      expect(result.current.isLoading).toBe(false);
    });
  });
});
```

### TS-052: Test Coverage

**Severity:** WARNING
**Tools:** c8/istanbul

Maintain minimum 80% test coverage.

```bash
# Run tests with coverage
pnpm test:coverage
```

---

## Build & Tools

### TS-060: Use PNPM

**Severity:** ERROR
**Tools:** pnpm

Use `pnpm` as the package manager.

```bash
# Install dependencies
pnpm install

# Add dependency
pnpm add package-name

# Add dev dependency
pnpm add -D package-name
```

### TS-061: ESLint Configuration

**Severity:** ERROR
**Tools:** ESLint

Use ESLint with TypeScript parser.

```javascript
// eslint.config.js
import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactPlugin from 'eslint-plugin-react';
import reactHooksPlugin from 'eslint-plugin-react-hooks';

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooksPlugin,
    },
    rules: {
      '@typescript-eslint/explicit-function-return-type': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/strict-boolean-expressions': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  }
);
```

### TS-062: Prettier Configuration

**Severity:** WARNING
**Tools:** Prettier

Use Prettier for consistent formatting.

```json
{
  "semi": true,
  "singleQuote": true,
  "tabWidth": 2,
  "trailingComma": "es5",
  "printWidth": 100,
  "bracketSpacing": true
}
```

### TS-063: TypeScript Configuration

**Severity:** ERROR
**Tools:** TypeScript

Use strict TypeScript configuration.

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "jsx": "react-jsx",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src"],
  "exclude": ["node_modules"]
}
```

---

## Performance

### TS-070: Lazy Loading

**Severity:** WARNING
**Tools:** React, Webpack/Vite

Use lazy loading for code splitting.

```typescript
import { lazy, Suspense } from 'react';

// Lazy load route components
const TaskDashboard = lazy(() => import('./pages/TaskDashboard'));
const Settings = lazy(() => import('./pages/Settings'));

function App(): React.ReactElement {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route path="/tasks" element={<TaskDashboard />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Suspense>
  );
}
```

### TS-071: Virtual Lists

**Severity:** WARNING
**Tools:** react-virtual, react-window

Use virtualization for long lists.

```typescript
import { useVirtualizer } from '@tanstack/react-virtual';

function TaskList({ tasks }: { tasks: Task[] }): React.ReactElement {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: tasks.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 50,
  });

  return (
    <div ref={parentRef} className="overflow-auto h-screen">
      <div style={{ height: `${virtualizer.getTotalSize()}px` }}>
        {virtualizer.getVirtualItems().map((item) => (
          <TaskItem key={tasks[item.index].id} task={tasks[item.index]} />
        ))}
      </div>
    </div>
  );
}
```

---

## Security

### TS-080: XSS Prevention

**Severity:** BLOCKER
**Tools:** ESLint (react/no-danger)

NEVER use `dangerouslySetInnerHTML` with user input.

```typescript
// Bad - XSS vulnerability
<div dangerouslySetInnerHTML={{ __html: userContent }} />

// Good - Use a sanitization library if HTML is required
import DOMPurify from 'dompurify';

<div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(userContent) }} />

// Good - Prefer text content
<div>{userContent}</div>
```

### TS-081: No `eval`

**Severity:** BLOCKER
**Tools:** ESLint (no-eval)

NEVER use `eval()` or `Function()` constructor.

```typescript
// Bad - Code injection vulnerability
eval(userInput);
new Function(userInput)();

// Good - Use proper parsing
JSON.parse(userInput);
```

### TS-082: Secure HTTP Calls

**Severity:** ERROR
**Tools:** Code review

Always use HTTPS for API calls.

```typescript
// Good - Secure configuration
const api = axios.create({
  baseURL: process.env.API_URL, // Should be https://
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});
```

### TS-083: Environment Variables

**Severity:** ERROR
**Tools:** Code review

Never expose secrets in frontend code.

```typescript
// Good - Only public env vars exposed
const config = {
  apiUrl: import.meta.env.VITE_API_URL,
  publicKey: import.meta.env.VITE_PUBLIC_KEY,
};

// Bad - Never do this (even though it won't work)
const secret = import.meta.env.VITE_SECRET_KEY; // Exposed in browser!
```

---

## Quick Reference

### Before Committing

```bash
# Format
pnpm format

# Lint
pnpm lint

# Type check
pnpm typecheck

# Test
pnpm test

# All checks
pnpm check
```

### Severity Levels

| Level | Action | Blocks PR |
|-------|--------|-----------|
| BLOCKER | Must fix immediately | Yes |
| ERROR | Must fix before merge | Yes |
| WARNING | Should fix | No |
| INFO | Consider improving | No |

### Rule ID Reference

| Prefix | Category |
|--------|----------|
| TS-00X | Code style |
| TS-01X | Type safety |
| TS-02X | React patterns |
| TS-03X | State management |
| TS-04X | Error handling |
| TS-05X | Testing |
| TS-06X | Build & tools |
| TS-07X | Performance |
| TS-08X | Security |
