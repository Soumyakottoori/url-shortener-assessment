# URL shortener and governed workflow prototype

The runnable URL-shortener API is implemented only in .NET 8 / ASP.NET Core with
EF Core SQL Server persistence. The Python code in this repository is limited
to the approval-gated engineering workflow and reproducible scenarios.

## Run the .NET API

For local development, ASP.NET Core's `dotnet user-jwts` tool creates a signed JWT
for this project. No external identity provider is needed locally. Create the token
before starting the API:

```powershell
dotnet user-jwts create --project src/UrlShortener.Api `
  --issuer dotnet-user-jwts --audience url-shortener-api `
  --role urlshortener.admin --output token
```

Copy the printed token, then start the API from this directory:

```powershell
dotnet run --project src/UrlShortener.Api
```

The Development launch profile opens Swagger UI at `http://127.0.0.1:8080/swagger`.
Use the Swagger **Authorize** button and paste the generated JWT (without adding
the `Bearer ` prefix) to call the protected administration endpoints.

The API uses SQL Server LocalDB by default in Development on Windows:
`Server=(localdb)\\MSSQLLocalDB;Database=UrlShortener;Trusted_Connection=True;TrustServerCertificate=True`.
Set `URLSHORTENER_CONNECTION_STRING` to use a managed SQL Server instance in production;
the application fails fast if it is missing outside Development. EF Core migrations are
applied at startup and transient SQL failures use bounded retry handling. The database
is durable across restarts; production still needs backups, TLS, secrets management,
and operational monitoring.

Use the copied token for admin requests:

```powershell
$headers = @{ Authorization = 'Bearer <paste-token-here>' }
$link = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/links `
  -Headers $headers -ContentType 'application/json' `
  -Body '{"url":"https://example.com","alias":"demo"}'
$link
```

`dotnet run` uses the checked-in Development launch profile. Outside Development,
the API does not accept development JWTs: configure `JWT_AUTHORITY` and
`JWT_AUDIENCE` for your real OIDC provider. Set `JWT_ADMIN_ROLE` and
`JWT_ROLE_CLAIM` if your provider uses different values.

The API listens on `http://127.0.0.1:8080`. `PUBLIC_BASE_URL` can set the externally
visible base URL when running behind a proxy. Admin routes require a valid JWT with
the configured admin role and are limited to 60 requests per minute. Missing/invalid
tokens receive `401`; valid tokens without the admin role receive `403`.
`/health` and redirects remain public. The service rejects non-public IP literals,
local hostnames, credential-bearing URLs, invalid aliases, and invalid TTLs. It never
fetches destination URLs.

The API contract is unchanged: `POST /api/links`, `GET /r/{code}`,
`GET /api/links/{code}/stats`, `DELETE /api/links/{code}`, and `GET /health`.

## How it is developed

The .NET API uses a layered flow: MVC controllers map HTTP requests and responses,
application services enforce URL and expiry rules, and `ILinkDataProvider` isolates
persistence behind an EF Core in-memory implementation. Basic request validation
uses data annotations; middleware maps service and provider exceptions to API errors.
A process-wide write lock serializes link creation, disable operations, and click
increments. The checked-in workflow prototype (`orchestrator.py`) models
requirements, design, security, tests, readiness, and human approval with retries,
audit history, and safe-stop rules. It supports an external coding-agent command
that works in an isolated copy, generates a reviewable patch, and must pass a .NET
build before the implementation stage passes. Without `--agent-command`, the
deterministic evidence worker remains the safe default. See
`docs/AGENT_PROTOCOL.md` for the contract. The configured agent applies only the
validated text-file changes after security checks and implementation approval; release
still pauses for a separate human approval.

## Run the workflow tests

Python 3.12+, standard library only. Run commands from this directory.

```bash
python -m unittest discover -s tests -v
dotnet test tests/UrlShortener.Api.Tests/UrlShortener.Api.Tests.csproj
```

## Review the SDLC runs
```bash
python demo.py greenfield
python demo.py brownfield
python demo.py ambiguous
```
To run an external coding agent for implementation generation, append its command:
```powershell
python demo.py greenfield --agent-command python path\to\agent.py
```
To connect Gemini directly:
```powershell
# Or edit the local .env file with these values.
$env:GEMINI_API_KEY = 'set-this-in-your-shell'
$env:GEMINI_MODEL = 'gemini-2.0-flash'
python demo.py greenfield --gemini-agent
```
Inspect `runs/*.json` for decomposition, artifacts, timestamps, decisions, test output,
and hash-linked audit records. Runs pause before release. After reviewing:
```bash
python demo.py greenfield --approve 'your-name'
python demo.py ambiguous --revise 'Use seven-day expiry and aggregate daily counts; retain counts for 90 days'
python demo.py brownfield --stop
```
Revision is a proposed requirement and invalidates evidence; it does not magically
implement a new retention policy. Ambiguous requirements cannot be approved until a
revision resolves them. Brownfield runs carry source baselines and API compatibility
contracts. Code changes require human/agent implementation and new tests before
approval; failed or revised runs restore the prior implementation files.
Tests use injected workers to avoid recursive execution when workflow tests run.

## Deliverables
- `orchestrator.py`: persistent DAG, parallel paths, retries, approval, replan, stop.
- `demo.py`: three reproducible review scenarios.
- `tests/test_system.py`: workflow, failure-control and isolated-agent tests.
- `tests/UrlShortener.Api.Tests`: .NET integration tests for API behavior and concurrency.
- `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/AGENT_PROTOCOL.md`: decisions, contract and agent adapter.

## Risks and limitations
The .NET service is runnable but not production certified. SQLite persists links and
analytics across restarts, but multi-instance production needs a managed database and
distributed coordination. Production also needs TLS, ingress limits, abuse protection, RBAC,
managed secrets, backups, and retention policy. Clicks are aggregated without
idempotency or bot filtering. URL checks do not prevent phishing or DNS changes after link creation.
The workflow includes an OpenAI-compatible model adapter, but model credentials and
provider availability remain external. It has no cloud release; approval identity is
asserted locally, and each run file assumes one writer.

## Suggested 2–3 day continuation
Day 1: confirm ambiguous semantics, service contracts and acceptance tests.
Day 2: connect a production model provider through `docs/AGENT_PROTOCOL.md`, validate
patches and implement scoped authentication, retention and richer policy checks.
Day 3: add authenticated review UI, trusted audit sink, load/fault testing and gated
staging deployment with tested infrastructure rollback.

The .NET API is the sole application implementation. The Python code is retained only
for the approval-gated workflow and its deterministic review scenarios.
