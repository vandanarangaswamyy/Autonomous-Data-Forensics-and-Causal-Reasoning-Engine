# Skeptical review: database abstraction and MCP phase

Verdict: the PostgreSQL/local-MCP phase works, including actual SQL and real SDK
subprocesses. It is not a complete production multi-tenant or Teradata integration.
The original completion summary understated several correctness/security weaknesses;
this review added regressions and fixed the confirmed issues below. No agents or
unrelated application features were introduced.

## Confirmed findings and fixes

| Finding | Severity/context | Resolution and evidence |
|---|---|---|
| Blocking preparation could consume the deadline and still execute/return success | High timeout-boundary defect | Measure from invocation start; check remaining time before admission, after reservation and before success. A sleeping reservation regression proves no handler executes after expiry. |
| Expired leases freed concurrency slots while owner work could still be cancelling | High concurrency-boundary defect | Record owner PID and keep the slot until completion or owner death. Dead work retains worst-case budget cost. Live-owner/dead-owner regressions cover both paths. |
| Audit flock acquisition could block the event loop indefinitely | High availability defect | Nonblocking acquisition with bounded 50 ms contention wait, then fail closed. Actual contended-file regression completes without executing a tool. |
| Remote errors reflected arbitrary peer messages and error codes into responses/audits | Medium sensitive-data risk | Preserve only allowlisted error codes and replace peer messages with a fixed safe message; unknown codes become remote_error. Regression checks both response and audit content. |
| Unknown tool names were copied directly into audit records | Low audit-data hygiene | Store unregistered plus a requested-name digest, rather than arbitrary raw input. |
| Handler output-validation failures were reported as invalid_input | Medium diagnosability defect | Distinguish invalid_output; bad request schemas remain invalid_input. |
| PostgreSQL role checks missed column-level write grants | Medium defense-in-depth defect | Check has_any_column_privilege in addition to table grants. Live fixture proves table UPDATE false / column UPDATE true and rejects that login. SQL AST and READ ONLY transactions already prevented write execution; this was not a demonstrated SQL write bypass. |
| Composite primary keys followed physical column order and included covering columns | Medium metadata correctness | Read index-key ordinality and indnkeyatts. Live PK(value,id) INCLUDE(auxiliary) fixture returns only value,id in the correct order. |
| Catalog fetch limits could silently return incomplete metadata | Medium correctness | Fetch all supported PostgreSQL columns (tested with a 1004-column table); fail explicitly on truncated key/index/dependency metadata. Teradata also refuses truncated column metadata. |
| Repeated cancellation could abandon native Teradata cleanup | High lifecycle risk, live unverified | Shield and drain cleanup even after repeated cancellation; do not release the local slot before native close. Driver-double regression verifies coordination, not real Vantage cancellation. |
| Teradata connection inherited a TLS fallback default | Medium remote-transport risk | Explicit VERIFY-FULL and encrypted-data options; driver-wiring test checks them. Certificates/connectivity remain live unverified. |
| Bootstrap wrote generated credentials before chmod on an existing public .env | Medium local disclosure window | Atomic replacement from a mode-0600 temporary file, symlink rejection, private temporary names ignored by Git. Tests inspect permissions before replacement and preserve linked targets. |
| Teradata imported PostgreSQL implementation for common result utilities | Low architectural coupling | Move serialization/capability names into vendor-neutral analytics/common.py. Core protocol continues to have no vendor imports. |
| Driver tests patched global importlib.import_module | Test-quality defect | Use an isolated driver-loader seam, avoiding dialect-import-cache/order dependence. Production still imports the official driver; no fake driver fallback exists. |

The first four targeted regressions failed before the corresponding fixes. Additional
fixture errors (missing test-role CONNECT grant, setup-test module imports, global import
mocking) were corrected rather than counted as product failures. Final results below
supersede intermediate failed runs. One later default run exposed a too-short (5s)
initialization assumption in the new EOF test. Its startup allowance now matches the
production SDK client's 35s, while EOF shutdown still must exit zero within 5s. The
raw-process test now uses the same filtered environment as the SDK launcher. Three
consecutive focused runs passed before the latest full-suite run.

The official driver documents its default TLS fallback behavior and the VERIFY-FULL
option: [Teradata Python driver](https://github.com/Teradata/python-driver).

## Requirement-by-requirement comparison

### Part A: database abstraction

| Required protocol operation | PostgreSQL status | Teradata status |
|---|---|---|
| execute_readonly_query | Live tested: bindings, safety, row/byte limits, timeout/recovery | Official-driver path implemented; live unverified |
| get_schema | Live tested over authorized catalog | Iterates explicit table allowlist; ColumnsV path live unverified |
| get_table_metadata | Live columns/keys/indexes, composite and wide-table regressions | ColumnsV fields only; primary keys/indexes unavailable |
| get_column_statistics | Live count/null/distinct/min/max/numeric mean | Aggregate path implemented; numeric mean unavailable; live unverified |
| get_query_plan | Live EXPLAIN JSON without ANALYZE | EXPLAIN driver path live unverified |
| get_table_dependencies | Live foreign-key edges | Not implemented: structured unsupported_capability |
| get_supported_capabilities | Per-operation runtime success tracked and tested | All live_verified flags false; unsupported dependencies explicitly declared |

Both concrete classes structurally satisfy the typed async protocol. The official
optional teradatasql package installs/imports, but that does not verify Vantage integration.
Teradata account attestation is not effective-role inspection. No connectivity, grant,
catalog, timeout, cancellation or certificate claim is made without a live environment.

| Required safety control | Verified completion |
|---|---|
| Dialect-aware validation, not regex alone | SQLGlot AST allowlist and scope analysis; attack tests in both dialects |
| Read-only roles | PostgreSQL live enforcement including column grants; Teradata administrator attestation only, incomplete live validation |
| Query timeouts | PostgreSQL actual cancellation/recovery, MCP timeout errors; Teradata options/cleanup wiring only |
| Row limits | AST bounds, bounded fetch and truncation tested; catalog truncation no longer silently succeeds |
| Parameter binding | Native PostgreSQL values, literal-percent handling, Teradata positional ordering including CTEs tested |

### Part B: four MCP servers and every requested tool

All servers use the official Python MCP SDK. None is a handcrafted JSON-RPC mock.
Each row below has typed input/output schemas, safe structured replies, required
request/investigation IDs, trace IDs, authorization, deadlines and audit records.
Schema artifacts are checked against runtime discovery descriptors.

| Server | Required tool | Permission | Verification |
|---|---|---|---|
| Analytics | get_table_schema | analytics:schema | Real PostgreSQL over MCP |
| Analytics | execute_readonly_sql | analytics:query | Real results, SQL denial and database timeout over MCP |
| Analytics | profile_column | analytics:profile | Real aggregate profile over MCP |
| Analytics | get_query_plan | analytics:plan | Real non-ANALYZE plan over MCP |
| Lineage | trace_upstream_lineage | lineage:read | Actual persisted transformation edges |
| Lineage | trace_downstream_lineage | lineage:read | Actual persisted transformation edges |
| Telemetry | get_pipeline_execution | telemetry:read | Actual execution history |
| Telemetry | search_incident_history | telemetry:read | Actual operational events, bound literal matching |
| Knowledge | search_technical_documents | knowledge:read | Real keyword search over three public documents |

The lineage/telemetry services target the simulator catalog; they do not discover arbitrary
enterprise pipeline systems. Knowledge search is deliberately limited, not a hidden
vector/RAG mock. Oracle files and mutation commands are not exposed as tools. A malformed
request with no usable investigation ID is rejected before execution and audited with
null investigation ID, not attached to a fabricated investigation.

### Part C: gateway

| Requirement | Verified completion / limit |
|---|---|
| Discover tools | Official SDK list_tools through local transports, plus schema/name validation |
| Maintain registry | Unique registered names, validated versioned schemas, explicit mappings |
| Enforce permissions | Application token + trusted principal permissions inside every server and proxy gateway; MCP hints do not grant rights |
| Apply budgets | Durable per-principal/investigation call/time/row reservations, replay rejection and restart tests |
| Enforce concurrency | Semaphore plus locked live-owner reservations, cancellation/cleanup regressions |
| Record traces | Durable started/finished/rejected records, hashes/correlation/counts/errors; failures deny successful responses |
| Prevent unauthorized invocation | Token, permission and SQL/table-policy denial tests; direct server calls cannot bypass gateway checks |
| Local MCP transport | Four real stdio processes, working client, EOF exit-code-zero test |
| Extensible remote interface | Typed ToolTransport/SessionTransport, discovery and correlation tests; no live HTTP/TLS/OAuth transport implemented |
| Do not rely on MCP for application authorization | Separate Authorizer, default-empty permissions, token in request metadata |

Authorization is a trusted local-operator profile, not production multi-user admission.
Investigation IDs are caller-provided: without control-plane admission, new IDs can avoid
per-investigation quota exhaustion. That is a remaining security boundary, not a claim
that the current file budget implements tenant/global admission. Do not expose this
profile as a multi-tenant endpoint or give agents the trusted launcher/owner environment.

### Part D: tests, client, documentation and scope

| Required test/deliverable | Evidence |
|---|---|
| Tool discovery and execution | Actual SDK stdio tests and all-nine-tool PostgreSQL client |
| Invalid inputs | Typed/schema rejection and safe invalid_input; output failures separately classified |
| Permission denial | Missing/invalid token, missing permission, forged permission argument and direct-server tests |
| Query timeout behavior | Actual PostgreSQL statement timeout plus MCP errors and gateway deadline regressions |
| SQL injection resistance | Two-dialect AST attack tests, scope tricks, bound-value attacks, metadata identifiers |
| Backend contract compliance | Both classes satisfy protocol; PostgreSQL methods exercised live; Teradata unavailable operations fail explicitly |
| Audit generation | Correlation, no raw argument/token bodies, remote-error sanitization, lock/budget/dead-owner tests |
| Graceful shutdown | Gateway active-work cancellation, repeated native-cleanup coordination and real server EOF exit |
| Working client | Clean-checkout run invokes every tool against generated PostgreSQL data |
| Documentation/state updates | Runbook, this review, PROJECT_STATE.md and DECISIONS.md updated |
| Stop after phase | No agents, RAG implementation, graph memory or unrelated business feature added |

## Exact verification results

- Baseline before review: make check **85 passed, 14 skipped, 1 warning**; live
  integration target **14 passed**.
- Final `make test-all`: **115 passed, 0 failed, 0 skipped, 1 warning**; includes all
  **16** PostgreSQL/Kafka/MCP live integration cases. Latest recorded run: 25.77 seconds.
- Final `make check`: **99 passed, 16 skipped, 1 warning** in 8.53 seconds. The 16
  live cases all passed under `make test-all`. Ruff lint/format and strict mypy
  (62 source files) passed. Source/wheel builds and pre-commit configuration passed.
- Remaining warning: upstream StarletteDeprecationWarning about the httpx TestClient
  transport. It predates this phase and does not represent a failing integration.
- Schema export, source/wheel build, pre-commit configuration, Compose validation and
  migration drift check passed. All four actual development containers are healthy.
- Documented `make dev` served /health and /ready with HTTP 200, then was stopped.
- Existing-checkout setup/bootstrap/migrate/MCP demo passed. Clean-source verification
  also passed: setup, sim-bootstrap, sim-migrate, sim-generate, all-nine-tool mcp-demo,
  sim-reset against a disposable PostgreSQL container; container/volume removed afterward.
  Fresh source verification is reproducible with
  `uv run --locked python scripts/verify_clean_mcp_setup.py`.

No hidden production mocks were found. Teradata driver doubles and a fake remote
endpoint exist only in explicitly named unit tests. They do not substitute for real
PostgreSQL/MCP/Kafka integrations, nor establish live Teradata or remote HTTP success.

## What remains incomplete / what next

1. Bind authenticated principals to admitted investigations, add a principal/tenant
   quota and token lifecycle before accepting untrusted multi-user clients.
2. Replace/operate the bounded local audit ledger with durable retention and shared
   quota semantics. It is not tamper-proof, distributed or compliance-grade. PID reuse
   and legacy unfinished records conservatively retain slots pending operator recovery.
3. Obtain a real Vantage environment; verify grants, certificates, dictionary mappings,
   request timeout and cancellation. Implement dependency mapping only with live tests.
4. Add remote transport only with endpoint authentication/TLS/OAuth and a separate
   threat review. Operator-trusted discovery is not protection from hostile schemas/peers.

Synchronous filesystem fsync/JSON validation and a misbehaving native driver cannot be
hard-preempted by asyncio. Deadline checks prevent expired work from starting or being
reported successful; cleanup may exceed the response deadline. Large database cells
are decoded before the response-byte cap. Same-OS-user filesystem access is not sandboxed.
Those limits must stay explicit; do not call this a hardened autonomous execution platform.

## Phase 2 re-verification (2026-10-30)

Reviewed commit `21711a9` against the original Parts A–D. Initial `git status --short`
and `git diff --stat` were empty: the fixes were committed, not missing from an
uncommitted diff. Read the complete gateway/audit/adapter paths and regression tests;
confirmed all seven requested fixes in source, then exercised the composed boundaries.
No additional production defect was confirmed. Changes in this pass strengthen
verification; they do not add Phase 3 functionality or alter production authorization.

### Additional verification

- Native-cleanup test composes the real gateway, shared audit ledger and Teradata
  adapter with an explicitly controlled driver double. Repeated cancellation plus
  gateway shutdown leave the semaphore locked and the shared slot occupied after the
  request deadline, during both blocked execute and blocked connection close. A second
  gateway is denied; only after close terminates is a finished cancellation audited
  and another request admitted. This proves Python coordination with a controlled
  native-worker boundary; actual Teradata native behavior is still UNVERIFIED.
- Time and row budgets persist across gateway restart, alongside existing call-budget,
  duplicate-ID, permission, audit-failure, deadline and shutdown regressions. A new
  synchronously blocking handler cannot return success after its deadline.
- Remote failures with both recognized and unrecognized codes discard sensitive text.
  Real PostgreSQL undefined-column and authentication errors traverse actual MCP stdio
  servers. Both structured and text responses contain the same safe database_error;
  responses/audits omit canary identifiers, passwords, URLs, SQL and driver diagnostics.
  This is tested coverage of these error paths, not a proof about every future plugin.
- Demo validation now examines every tool body, correlation/unique request/trace IDs,
  schema/PK/indexes, aggregate rows, numeric profile, nonexecuting plan, expected lineage
  edges, execution/incident fields and public-document provenance. Live integration
  additionally compares SQL results and profiles to direct read-only backend calls.
- Actual demo: schema 6 columns; 4 currency groups; profile 605 orders / 0 null totals;
  upstream 15 edges; downstream 10 edges; 1 pipeline execution; 1 source incident;
  3 document matches. All nine statuses were ok and all content assertions passed.
- Clean temporary source copy with a fresh virtualenv, generated credentials and
  disposable PostgreSQL passed setup/bootstrap/migrate/generate/demo/reset again.
  The demo used the strengthened content checks. Only its container/volume were removed.
  This is a fresh PostgreSQL fixture, not a fresh host or new four-service Compose cluster.

### Complete requirement classification

VERIFIED means source inspection plus passing local tests for the stated boundary.
PARTIAL means implemented with material restrictions. UNVERIFIED means implementation
exists but the required real environment was unavailable. MISSING means no implementation.
These labels supersede any implication of universal completion in earlier tables.

| Part A requirement | PostgreSQL | Teradata |
|---|---|---|
| Typed AnalyticsBackend protocol with all seven operations | VERIFIED | VERIFIED (structural contract only) |
| execute_readonly_query | VERIFIED | UNVERIFIED (official-driver path) |
| get_schema | VERIFIED | UNVERIFIED (allowlisted tables only) |
| get_table_metadata | VERIFIED (base tables, wide/composite cases) | PARTIAL (ColumnsV path unverified; keys/indexes missing) |
| get_column_statistics | VERIFIED | PARTIAL (aggregate path unverified; numeric mean missing) |
| get_query_plan | VERIFIED | UNVERIFIED |
| get_table_dependencies | VERIFIED (foreign keys) | MISSING (explicit unsupported_capability) |
| get_supported_capabilities | VERIFIED | VERIFIED (accurately reports no live verification) |
| Working backend end to end | VERIFIED | UNVERIFIED |
| Official supported driver, honest unavailable features | VERIFIED (psycopg) | VERIFIED (teradatasql import path; no fake fallback) |
| Dialect-aware AST safety; not regex alone | VERIFIED | VERIFIED (parser/policy tests, no live SQL proof) |
| Read-only roles | VERIFIED (including column grants) | PARTIAL (administrator attestation only) |
| Query timeouts | VERIFIED (actual timeout/recovery) | UNVERIFIED (options/coordination tested with doubles) |
| Row limits | VERIFIED | UNVERIFIED (bounded-fetch wiring tested with doubles) |
| Parameter binding | VERIFIED (live injection/literal-percent cases) | UNVERIFIED (generated positional order tested with doubles) |

| Part B requirement | Classification | Evidence / limit |
|---|---|---|
| Official Python MCP SDK | VERIFIED | Actual SDK sessions and subprocesses |
| Independent Analytics server | VERIFIED | Direct stdio and gateway client |
| Independent Lineage server | VERIFIED | Persisted simulator lineage |
| Independent Telemetry server | VERIFIED | Persisted simulator execution/events |
| Independent Knowledge Retrieval server | VERIFIED | Three real public documents; lexical search only |
| get_table_schema | VERIFIED | Schema/keys/indexes checked live |
| execute_readonly_sql | VERIFIED | Bound aggregates, denial, timeout and safe failures live |
| profile_column | VERIFIED | Live numeric aggregate compared directly |
| get_query_plan | VERIFIED | Real EXPLAIN JSON, executes_query=false |
| trace_upstream_lineage | VERIFIED | Expected source/destination and column edges |
| trace_downstream_lineage | VERIFIED | Expected source/destination and column edges |
| get_pipeline_execution | VERIFIED | Real ingest run, status/counts/quality |
| search_incident_history | VERIFIED | Real source event/code matching |
| search_technical_documents | VERIFIED | Real excerpts, scores and public URIs |
| Typed input for every tool | VERIFIED | Pydantic/JSON Schema plus invalid-input tests |
| Typed output for every tool | VERIFIED | Runtime/export agreement; invalid_output regression |
| Structured errors for every tool | VERIFIED | Correlated safe ToolReply, including actual driver failures |
| Timeouts for every tool | VERIFIED | Outer gateway deadline plus database bounds; cleanup may overrun |
| Correlation IDs for every tool | VERIFIED | Investigation/request/trace and remote mismatch tests |
| Audit logging for every tool | VERIFIED | Started/finished/rejected records; fail-closed persistence |
| Explicit permissions for every tool | VERIFIED | Trusted static mapping, server-side enforcement |

| Part C requirement | Classification | Evidence / limit |
|---|---|---|
| Discover available tools | VERIFIED | Nine tools through four real SDK servers |
| Maintain registry | VERIFIED | Typed descriptors, unique names, schema checks |
| Enforce tool permissions | VERIFIED | Token + principal mapping; direct-server denial |
| Apply request budgets | VERIFIED | Local call/time/row reservations and restart tests |
| Enforce concurrency | VERIFIED | Semaphore + shared-ledger admission; composed cleanup test |
| Record execution traces | VERIFIED | Durable correlated/hash-only local audit |
| Prevent unauthorized invocation | VERIFIED | Missing/wrong token, missing/forged permissions denied |
| Local MCP transport | VERIFIED | Working stdio demo, EOF zero-exit test |
| Extensible remote transport interface | VERIFIED | Typed transport and schema/correlation tests |
| Remote HTTP/TLS/OAuth deployment | MISSING | Not required by the interface-only Phase 2 deliverable |
| Authorization independent of MCP | VERIFIED | Explicit Authorizer, default-empty permissions |
| Production multi-tenant admission / distributed quotas | MISSING | Not implemented; local operator boundary only |

| Part D requirement | Classification | Evidence / limit |
|---|---|---|
| MCP discovery tests | VERIFIED | Actual stdio plus registry tests |
| Tool execution tests | VERIFIED | All nine tools against actual PostgreSQL fixtures |
| Invalid-input tests | VERIFIED | Schema/Pydantic errors and safe replies |
| Permission-denial tests | VERIFIED | Direct server plus proxy paths |
| Query-timeout tests | VERIFIED | PostgreSQL statement timeout and gateway deadlines |
| SQL-injection tests | VERIFIED | Both AST dialects; live PostgreSQL binding |
| Backend contract compliance | PARTIAL | PostgreSQL live; Teradata structural/wiring only |
| Audit-generation tests | VERIFIED | Records, privacy, locks, failures, leases, budgets |
| Graceful-shutdown tests | VERIFIED | Real stdio EOF and composed gateway cancellation |
| Working local MCP client | VERIFIED | All nine response bodies checked |
| Run tests / update documentation | VERIFIED | Exact results and commands below |
| Stop after Phase 2 | VERIFIED | No agents, RAG or temporal memory added |

### Exact commands and results for this pass

Run from the repository root. The successful final quality/full/live invocation was:

```sh
make format check test-all mcp-integration
```

It executes exactly:

```sh
uv run --locked ruff format .
uv run --locked ruff check .
uv run --locked mypy
uv run --locked pytest
uv run --locked ruff format --check .
uv run --locked python scripts/test_simulator_integration.py --all
uv run --locked python scripts/test_simulator_integration.py
```

| Final check | Exact outcome |
|---|---|
| Ruff format / format check | 92 files unchanged / already formatted |
| Ruff lint | All checks passed |
| Strict mypy | Success: 62 source files |
| Default pytest | 104 passed, 18 skipped, 1 warning, 8.54s |
| Complete suite with actual services | 122 passed, 0 skipped, 1 warning, 28.04s |
| Separate live integration rerun | 18 passed, 0 skipped, 0 warnings, 19.46s |

The default skips are exactly all 18 opt-in integration cases: 10 in
`test_analytics_backend.py` (including two key/grant parameters and two privacy
parameters), 1 Kafka replay/dedup case and 7 PostgreSQL simulator/observer cases.
The reasons are unset ATLAS_TEST_READER_URL / ATLAS_TEST_POSTGRES_URL in the default
invocation. All run in test-all; there are no skipped Teradata tests masquerading as
live verification. The one warning is the existing upstream Starlette/httpx TestClient
deprecation. No final test failures remain.

Other successful verification commands:

```sh
git status --short
git diff --stat
git show --stat --oneline HEAD
docker compose --env-file .env -f infrastructure/docker/compose.yaml ps
make compose-validate
uv run --locked pytest tests/test_teradata_backend.py -q
uv run --locked python scripts/verify_clean_mcp_setup.py
make mcp-schemas
uv build
uv run --locked alembic check
git diff --check
```

The focused driver run was 4 passed in 0.63s; it preceded and does not replace the
complete-suite run. Builds produced source and wheel; Alembic reported no new upgrade
operations. All four actual Compose services were healthy. Schema exports were unchanged.
The clean harness executed these exact targets in its temporary source copy:

```sh
make setup
make sim-bootstrap
make sim-migrate
make sim-generate
make mcp-demo
make sim-reset
```

Standalone documented demo execution/content check (stdout captured to avoid dumping
raw data into this report) was performed with:

```python
import json
import subprocess
from scripts.verify_clean_mcp_setup import verify_demo_contents
result = subprocess.run(['make', 'mcp-demo'], capture_output=True, text=True, check=True)
payload = json.loads(result.stdout[result.stdout.index('{'):])
verify_demo_contents(payload)
```

The Python block ran via `uv run --locked python -`. It then printed only counts/status
summaries listed above. No credential-bearing environment values were shell-expanded.

Initial verification before new tests passed 115 full / 16 live and 99 default with
16 skips. During test development, combined checks stopped at two Ruff errors (long
SQL literal, intentionally blocking test handler), then strict typing errors (generic
dict, an Any-return helper and MCP union narrowing). These verification-code defects
were corrected before the successful final run; no failing run was counted as a pass.

### Closure decision and remaining risks

The PostgreSQL and trusted local-MCP security verification is ready to close. The entire
Phase 2 must remain PARTIAL if closure requires live Teradata or all backend operations:
Teradata dependencies are MISSING, metadata/statistics are PARTIAL, and actual Vantage
connectivity, effective grants, catalogs, binding, TLS certificates, timeout and native
cancellation/resource cleanup are UNVERIFIED. No real Teradata connection was made.
Accept these exceptions explicitly before treating the whole phase as complete.

Existing limits remain: caller-created investigation IDs can avoid per-investigation
quotas without admission; no token issuer/expiry or tenant/global budgets; local audit
rotation, PID recovery, single-host locks and same-user filesystem trust; no remote
authenticated deployment; synchronous fsync and stuck native work can outlast deadlines;
byte limits apply after cell decoding. Tests do not convert those design limits into
production guarantees. Next work is scoped principal/investigation admission and durable
budget/audit operations, or a live Vantage verification environment, under a separate
authorized task. Phase 3 has not started. This pass is recorded in the Phase 2 re-verification commit.

Exact default-skipped test IDs (all passed in the full/live runs):

```text
tests/integration/test_analytics_backend.py::test_postgres_backend_contract
tests/integration/test_analytics_backend.py::test_binding_injection_and_literal_percent
tests/integration/test_analytics_backend.py::test_real_query_timeout_and_recovery
tests/integration/test_analytics_backend.py::test_owner_role_rejected_and_response_byte_budget
tests/integration/test_analytics_backend.py::test_all_four_real_mcp_servers
tests/integration/test_analytics_backend.py::test_live_mcp_timeout_denial_and_sql_safety
tests/integration/test_analytics_backend.py::test_column_write_grant_and_composite_key_order[False]
tests/integration/test_analytics_backend.py::test_column_write_grant_and_composite_key_order[True]
tests/integration/test_analytics_backend.py::test_live_mcp_errors_do_not_disclose_driver_or_connection_details[query]
tests/integration/test_analytics_backend.py::test_live_mcp_errors_do_not_disclose_driver_or_connection_details[authentication]
tests/integration/test_simulator_kafka.py::test_real_kafka_replay_and_durable_dedup
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[schema_drift]
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[delayed_ingestion]
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[duplicate_transactions]
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[incorrect_join]
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[missing_partitions]
tests/integration/test_simulator_postgres.py::test_real_postgres_faults[currency_conversion]
tests/integration/test_simulator_postgres.py::test_observer_privileges
```

Files modified by this re-verification only: PROJECT_STATE.md, DECISIONS.md,
docs/reviews/database-mcp-audit.md, scripts/verify_clean_mcp_setup.py,
tests/integration/test_analytics_backend.py, tests/mcp/test_review_security.py,
tests/test_teradata_backend.py. Production modules and schema artifacts are unchanged.
