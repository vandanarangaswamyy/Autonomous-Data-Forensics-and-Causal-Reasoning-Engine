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
