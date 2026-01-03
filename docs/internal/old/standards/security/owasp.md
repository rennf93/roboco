# Security Standards (OWASP Top 10)

Security standards based on OWASP Top 10 for the RoboCo system.

## SEC-001: Injection Prevention

### SQL Injection
NEVER construct SQL queries with string concatenation.

```python
# NEVER DO THIS
query = f"SELECT * FROM users WHERE id = '{user_id}'"

# ALWAYS USE parameterized queries
result = await db.execute(
    "SELECT * FROM users WHERE id = :id",
    {"id": user_id}
)
```

### Command Injection
NEVER pass user input directly to shell commands.

```python
# NEVER DO THIS
os.system(f"process_file {filename}")

# ALWAYS validate and sanitize
if not SAFE_FILENAME_PATTERN.match(filename):
    raise ValidationError("Invalid filename")
subprocess.run(["process_file", filename], check=True)
```

## SEC-002: Authentication

### Password Storage
NEVER store passwords in plain text. Use bcrypt or argon2.

### Token Security
- JWT tokens MUST have short expiration (15-60 minutes)
- Refresh tokens MUST be stored securely (httpOnly cookies)
- Always validate token signatures

### Session Management
- Generate new session IDs after login
- Implement session timeout
- Invalidate sessions on logout

## SEC-003: Sensitive Data Exposure

### Environment Variables
Store secrets in environment variables, NEVER in code.

```python
# NEVER DO THIS
API_KEY = "sk-abc123xyz789"

# ALWAYS load from environment
API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise ConfigurationError("API_KEY not set")
```

### Logging
NEVER log sensitive data (passwords, tokens, PII).

```python
# NEVER log credentials
logger.info(f"User login: {username}, password: {password}")

# Log safely
logger.info("User login", username=username, masked_password="***")
```

## SEC-004: Access Control

### Authorization Checks
Verify permissions on EVERY request, not just at entry points.

```python
async def update_task(task_id: str, agent: Agent):
    task = await get_task(task_id)

    # ALWAYS check permissions
    if not await can_modify_task(agent, task):
        raise PermissionDenied("Cannot modify this task")

    # Proceed with update
```

### Principle of Least Privilege
Agents should have minimum permissions needed for their role.

## SEC-005: Security Misconfiguration

### Headers
Set security headers on all responses:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Content-Security-Policy`
- `Strict-Transport-Security`

### CORS
Configure CORS strictly. Never use `*` in production.

```python
# Development only
origins = ["http://localhost:3000"]

# Production
origins = ["https://app.roboco.ai"]
```

## SEC-006: Cross-Site Scripting (XSS)

### Output Encoding
Always encode user input before rendering in HTML.

### Content Security Policy
Implement strict CSP to prevent inline scripts.

### React/JSX
Never use `dangerouslySetInnerHTML` with user input.

## SEC-007: Insecure Deserialization

### Pickle/Eval
NEVER use `pickle.loads()` or `eval()` on untrusted data.

### JSON Validation
Always validate JSON structure with Pydantic before processing.

## SEC-008: Vulnerable Dependencies

### Dependency Scanning
Run `pip-audit` and `npm audit` in CI pipeline.

### Updates
Keep dependencies updated. Review security advisories weekly.

## SEC-009: Logging & Monitoring

### Audit Trail
Log all security-relevant events:
- Authentication attempts
- Authorization failures
- Data access
- Configuration changes

### Alerting
Set up alerts for:
- Multiple failed login attempts
- Permission denied spikes
- Unusual access patterns

## SEC-010: API Security

### Rate Limiting
Implement rate limiting on all endpoints.

### Input Validation
Validate all input parameters:
- Type checking
- Length limits
- Format validation
- Range checks

```python
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    priority: int = Field(..., ge=1, le=5)
```
