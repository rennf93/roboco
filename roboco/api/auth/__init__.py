"""Cloud auth (FastAPI Users, default off) — see roboco/config.py Settings.

Layer split:
    manager.py  - UserManager + the get_user_db/get_user_manager DI chain.
    backend.py  - cookie transport + the password-fingerprint-bound JWT
                  strategy + the AuthenticationBackend.
    session.py  - resolve_session_user, the one cookie-validation
                  implementation shared by the HTTP dual-path
                  (api.deps.get_agent_context) and the WS panel-token gate.
    seed.py     - idempotent upsert of the single seeded CEO login user.
    routes.py   - the always-public status probe + the login/logout mount.

Nothing here is imported by roboco.api.deps or roboco.api.websocket except
the small shared surface (resolve_session_user, SESSION_COOKIE_NAME,
get_jwt_strategy, cookie_transport) — this package never imports either of
those modules, keeping the dependency direction one-way.
"""
