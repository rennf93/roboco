# RoboCo SaaS Launch Plan

## Overview

**Goal:** Prepare RoboCo backend for SaaS launch with developer preview

**Repos:**
- `roboco` (this repo) - Backend API - THE PRODUCT (closed)
- `roboco-panel` (separate) - Dashboard UI (OSS later)
- `codepanion` (separate) - CLI tool (OSS later)

**Hosting:** VPS initially, scale from there

---

## Phase 1: Multi-Tenancy Foundation (3-5 days)

Add tenant isolation at data layer.

**Tasks:**
1. Create `tenants` table with: id, name, slug, plan, api_key_hash, usage_limits, stripe_customer_id
2. Add `tenant_id` column to all major tables (TaskTable, SessionTable, MessageTable, etc.)
3. Create Alembic migration for tenant columns
4. Create TenantContext middleware to extract tenant from request
5. Update all services to filter by tenant_id

**Files to modify:**
- `roboco/db/tables.py` - Add TenantTable, add tenant_id to existing tables
- `roboco/api/middleware.py` - Add TenantContextMiddleware
- `roboco/services/*.py` - Add tenant filtering to all queries
- New migration in `alembic/versions/`

---

## Phase 2: API Key Authentication (2-3 days)

Secure external access with API keys.

**Tasks:**
1. Create `api_keys` table (id, tenant_id, key_hash, scopes, rate_limit, created_at, last_used_at)
2. Create APIKeyAuth dependency that extracts Bearer token
3. Create `/api/v1/auth/keys` routes (create, list, revoke)
4. Update deps.py to support both API key and internal X-Agent-ID auth

**Auth Flow:**
```
External (panel, CLI): Authorization: Bearer roco_sk_xxxxx
Internal (agents):     X-Agent-ID + X-Agent-Role headers (keep existing)
```

**Files to modify:**
- `roboco/db/tables.py` - Add APIKeyTable
- `roboco/api/deps.py` - Add get_current_tenant, APIKeyAuth
- `roboco/api/routes/auth.py` - NEW: API key management endpoints
- `roboco/services/auth.py` - NEW: API key hashing, verification

---

## Phase 3: Usage Tracking & Billing (3-4 days)

Track usage and integrate Stripe.

**Tasks:**
1. Create `usage_records` table (tenant_id, metric, value, recorded_at, billing_period)
2. Create UsageTrackingMiddleware to log API calls
3. Integrate Stripe: customer creation on signup, subscription management
4. Create webhook handler for Stripe events
5. Create `/api/v1/billing` routes (usage, invoices, upgrade)

**Files to modify:**
- `roboco/db/tables.py` - Add UsageRecordTable
- `roboco/api/middleware.py` - Add UsageTrackingMiddleware
- `roboco/services/billing.py` - NEW: Stripe integration
- `roboco/api/routes/billing.py` - NEW: Billing endpoints
- `roboco/config.py` - Add STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET

---

## Phase 4: Rate Limiting (1-2 days)

Protect the API with per-tenant rate limits.

**Tasks:**
1. Add Redis-based sliding window rate limiter
2. Create RateLimitMiddleware
3. Add rate limit headers to responses (X-RateLimit-*)
4. Configure per-plan rate limits

**Files to modify:**
- `roboco/api/middleware.py` - Add RateLimitMiddleware
- `roboco/services/rate_limit.py` - NEW: Redis rate limiter
- `roboco/config.py` - Add rate limit settings per plan

---

## Phase 5: Production Infrastructure (2-3 days)

VPS deployment ready.

**Tasks:**
1. Create `docker-compose.prod.yml` with:
   - roboco-api (2+ replicas)
   - postgres (with pgvector)
   - redis
   - nginx/caddy (SSL termination)
2. Create production Dockerfile (multi-stage build)
3. Setup SSL with Let's Encrypt
4. Add Sentry for error tracking
5. Add Prometheus metrics endpoint
6. Create deployment script

**Files to create:**
- `docker/docker-compose.prod.yml`
- `docker/Dockerfile.prod`
- `docker/nginx.conf`
- `scripts/deploy.sh`
- `roboco/api/routes/metrics.py` - NEW: Prometheus metrics

---

## Phase 6: Documentation (parallel with Phase 4-5)

Developer onboarding ready.

**Tasks:**
1. Setup docs site (MkDocs or similar)
2. Write Quick Start guide (5 min setup)
3. Write Authentication guide
4. Enhance OpenAPI descriptions
5. Write codepanion CLI guide
6. Create example projects

**Files to create:**
- `docs/` directory with MkDocs config
- `docs/quickstart.md`
- `docs/authentication.md`
- `docs/api-reference.md`

---

## MVP Scope (What to Ship)

**Include:**
- Task Management API (existing)
- Session/Messaging API (existing)
- Optimal (RAG) API (existing)
- API key authentication
- Basic usage tracking
- Stripe billing (usage-based)
- Rate limiting
- VPS deployment

**Exclude (Post-MVP):**
- OAuth2/SSO
- Complex RBAC (scopes are enough)
- Multi-region deployment
- Official SDKs (raw HTTP first)
- Auto-scaling (single VPS is fine for launch)

---

## Timeline

| Week | Work |
|------|------|
| 1 | Phase 1 (Multi-tenancy) + Phase 2 (API Keys) |
| 2 | Phase 3 (Billing) + Phase 6 (Docs start) |
| 3 | Phase 4 (Rate Limiting) + Phase 5 (Infrastructure) + Phase 6 (Docs finish) |
| 4 | Testing, QA, soft launch |

**Total: ~3-4 weeks**

---

## Pricing Decisions

- **Model:** Flat monthly tiers
- **Free tier:** No - paid only (trial period)
- **Tiers:** TBD (e.g., Starter $29/mo, Pro $99/mo, Enterprise custom)

## Open Questions

- VPS provider: DigitalOcean? Hetzner? Vultr?
- Domain: What domain for the API?
- Trial period length: 7 days? 14 days?
