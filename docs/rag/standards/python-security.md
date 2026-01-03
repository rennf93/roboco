# Python Security Standards

## No Hardcoded Secrets

NEVER hardcode secrets:

```python
# Bad - NEVER
API_KEY = "sk-abc123xyz789"
DATABASE_URL = "postgresql://user:password@host/db"

# Good - environment variables
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    api_key: str
    database_url: str
    model_config = {"env_prefix": "ROBOCO_"}
```

## SQL Injection Prevention

NEVER use string concatenation for SQL:

```python
# Bad - SQL injection vulnerability
query = f"SELECT * FROM users WHERE id = '{user_id}'"

# Good - parameterized query
result = await session.execute(
    select(User).where(User.id == user_id)
)
```

## Command Injection Prevention

NEVER pass user input directly to shell:

```python
# Bad - command injection
import os
os.system(f"process_file {filename}")

# Good - use subprocess with list
import subprocess
subprocess.run(["process_file", filename], check=True)
```

## No eval() or exec()

NEVER use on untrusted input:

```python
# Bad - code injection
result = eval(user_input)

# Good - safe parsing
import ast
result = ast.literal_eval(user_input)  # Only literals
```

## Non-Security Hashes

When hashing for non-security purposes:

```python
import hashlib

content_hash = hashlib.md5(
    content.encode(),
    usedforsecurity=False  # Required flag
).hexdigest()[:12]
```

## Security Tools

Run before merge:

```bash
# Security scan
uv run bandit -r roboco/ -ll

# Dependency audit
uv run pip-audit
uv run safety scan
```
