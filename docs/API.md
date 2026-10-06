# API contract
For the .NET API, administration routes require an access token in
`Authorization: Bearer <JWT>`. The JWT is validated against `JWT_AUTHORITY` and
`JWT_AUDIENCE`; it must contain the configured admin role (`JWT_ADMIN_ROLE`,
default `urlshortener.admin`). Set `JWT_ROLE_CLAIM` if the identity provider uses a
different role-claim name. The API validates tokens but does not issue them. Missing
or invalid credentials return 401; valid credentials without the required role
return 403.

| Method | Route | Body / result |
|---|---|---|
| POST | /api/links | `{ "url": "https://example.com", "alias": "demo", "ttl_seconds": 604800 }` → 201 code and short_url; alias and TTL optional |
| GET | /r/{code} | 302 Location; increments UTC day aggregate atomically |
| GET | /api/links/{code}/stats | 200 code, total_clicks, daily object |
| DELETE | /api/links/{code} | 204 soft disable, retained analytics |
| GET | /health | 200 database availability |

Errors: JSON `{"error":"message"}`; 400 invalid input, 401 invalid token,
403 insufficient role, 404 missing resource, 409 duplicate alias, 410 expired/disabled link,
413 oversized body, 429 admin throttle, 503 database unavailable.
Redirect codes are case sensitive; no permanent redirects to avoid stale browser caching.
