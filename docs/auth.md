# Authentication and CSRF

## Goals

The auth model is intentionally minimal but practical for homelab/local deployments:

- prevent anonymous write access
- protect browser sessions against CSRF
- keep automation possible through API key

## Login Model

- endpoint: `POST /api/auth/login`
- storage: `users` table (`username`, `password_hash`, `role`)
- session keys: `user_id`, `role`, `username`, `csrf_token`

`GET /api/auth/me` is used by frontend bootstrapping to discover:

- whether auth is enabled
- current authenticated user
- CSRF token to attach on write requests

## Route Protection

`before_request` middleware behavior when `AUTH_ENABLED=1`:

- public: static assets, login page, auth endpoints
- protected API: returns `401` JSON when unauthenticated
- protected UI page: redirects to `/login`

## Write Authorization Contract

`require_write_auth()` enforces:

1. if auth enabled: authenticated session required
2. if auth enabled: CSRF token in `X-CSRF-Token` required for writes
3. if API key configured: `X-API-Key` accepted for automation
4. if auth disabled and API key configured: API key required

## CSRF Details

- generation: random URL-safe token per session login
- storage: session cookie
- transport: `X-CSRF-Token` request header
- comparison: constant-time `hmac.compare_digest`

## Bootstrap Admin Behavior

Environment options:

- `ADMIN_USERNAME` (default `admin`)
- `ADMIN_PASSWORD_HASH` (recommended)
- `ADMIN_PASSWORD` (fallback if hash not provided)

If both password env vars are absent:

- one-time random bootstrap password is generated
- password is logged server-side for first access

## Operational Notes

- Session cookie uses `HttpOnly` and `SameSite=Lax`
- `SESSION_COOKIE_SECURE` defaults to false (local HTTP); enable TLS/reverse proxy as needed
- For internet exposure, put service behind HTTPS and do not rely on default settings
