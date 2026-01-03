# TypeScript Coding Standards

## Package Manager

Use `pnpm` for all TypeScript/JavaScript operations.

```bash
# Install dependencies
pnpm install

# Add dependency
pnpm add package-name

# Add dev dependency
pnpm add -D package-name
```

## Before Every Commit

```bash
pnpm format       # Format code
pnpm lint         # Lint
pnpm typecheck    # Type check
pnpm test         # Tests
```

## Type Safety

Enable strict mode in tsconfig:

```json
{
  "compilerOptions": {
    "strict": true,
    "noUncheckedIndexedAccess": true
  }
}
```

## Avoid `any`

Never use `any`. Use `unknown` or generics:

```typescript
// Bad
function process(data: any): any { ... }

// Good
function process<T>(data: T): ProcessedData<T> { ... }
```

## Null Checks

Use optional chaining and nullish coalescing:

```typescript
// Good
const name = user?.profile?.name ?? "Anonymous";

// Bad
const name = user && user.profile && user.profile.name || "Anonymous";
```

## Async/Await

Use async/await over raw Promises:

```typescript
// Good
async function fetchUser(id: string): Promise<User> {
  const response = await api.get(`/users/${id}`);
  return response.data;
}

// Bad
function fetchUser(id: string): Promise<User> {
  return api.get(`/users/${id}`).then(r => r.data);
}
```

## Error Handling

Use typed errors:

```typescript
class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number
  ) {
    super(message);
  }
}

try {
  await api.call();
} catch (error) {
  if (error instanceof ApiError) {
    // Handle API error
  }
  throw error;
}
```

## Component Props

Define explicit prop types:

```typescript
interface ButtonProps {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export function Button({ label, onClick, disabled }: ButtonProps) {
  // ...
}
```
