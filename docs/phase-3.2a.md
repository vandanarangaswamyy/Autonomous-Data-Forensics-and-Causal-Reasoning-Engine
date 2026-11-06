# Phase 3.2a: bounded source extraction and normalization

Phase 3.2 is **partial**. This verified increment delivers PDF/text source extraction,
normalization maps, immutable successful artifacts and integration with the existing
job lifecycle. Stop here. Technical NLP/entity resolution/relationships and their
labeled evaluation remain 3.2b; this increment does not authorize Phase 3.3.

Baseline inspected: 835666a. Existing MCP implementations, database analytics policy,
permissions, budgets, audit and shutdown behavior were not changed.

## Architecture and provenance

- `atlas_contracts.extraction_config` owns the immutable processor configuration;
  `atlas_contracts.extraction` owns page ranges, compact offset runs and artifacts.
- `atlas_core.extraction.DocumentExtractor` is a typed port. `SourceExtractor` is the
  functioning implementation. NLPProcessor/EntityExtractor/RelationshipExtractor/
  EntityResolver/DocumentClassifier and semantic ExtractionResult are **not implemented**.
- Text and PDF extraction, normalization, page-aware segmentation, durable orchestration,
  persistence and evaluation remain distinct modules. No statistical model or LLM runs.
- Original source bytes and document/version identity remain unchanged. An artifact
  references the source SHA-256, parser/revision, exact requested configuration,
  processing timestamp, document/version and an immutable chunk manifest.
- PDF offsets are zero-based Unicode character ranges in extracted text, **not PDF
  byte offsets, glyph coordinates or a proven original reading order**. One form-feed
  separates pages. Numbered page ranges exclude the separator. PDF chunks remain within
  a page plus its trailing separator and carry a page section label.
- UTF-8 artifacts retain the original decoded source. Existing 3.1 text chunk IDs and
  ranges are preserved on explicit extraction/reprocessing. Normalization is a separate
  field; chunk segmentation continues over the extracted frame to preserve identity.
- `line-endings-v1` converts CRLF/CR to LF and NBSP to space. `identity-v1` preserves all
  text. Neither guesses spelling, changes case, removes BOMs, merges words or performs
  entity canonicalization. Compact mapping runs map every normalized span back to its
  extracted frame; CRLF is represented as a 1-to-2 range. Mapping complexity is bounded
  to 4096 runs. Unknown revisions are rejected.

For pypdf, layout whitespace can preserve simple headings and table rows, as verified
on the authored corpus. No reliable heading hierarchy or structured table cells are
claimed. The parser's documented limitations and content-stream memory risks informed
isolation: [official pypdf extraction documentation](https://pypdf.readthedocs.io/en/latest/user/extract-text.html).

## Parser execution and security limits

pypdf is pinned to **6.21.4** in uv.lock and in a dedicated Linux image. Its Python base
image is pinned by manifest digest. This is a per-request parsing sandbox process,
not an additional job system or persistent microservice. PDF runtime requires Docker
and an explicitly built local image; invocation uses `--pull never`. No runtime
network/model download occurs. UTF-8 processing does not require the parser image.

Each PDF gets a random named container with no network, no host mounts, read-only
filesystem, non-root user, dropped capabilities, no-new-privileges, 32-PID ceiling,
512 MiB memory/swap ceiling, one CPU and disabled container logs. The child also applies
Linux address-space and CPU limits (5 seconds soft / 6 hard). Only PDF bytes travel on
stdin. The Docker client environment contains HOME/PATH; database/MCP/evaluator settings
and owner credentials are not passed. Parser errors/stdout are bounded and sanitized;
stderr is discarded. Container and attach client are terminated/reaped during cleanup,
including repeated cancellation. Removal is checked; unconfirmed cleanup fails closed.

Source limit remains 1 MB. Defaults: 50 pages, 200,000 extracted characters and a
10-second runtime deadline. Supported configuration bounds: 1–100 pages, 1–200,000
characters and 0.05–15 seconds. Maximum child response is 2 MB. Cleanup commands have
separate 5-second bounds and may extend the response deadline. No heartbeat was added:
the existing 60-second lease exceeds supported parser plus cleanup budgets in normal
operation. Host/Docker stalls or lost leases must be recovered by the existing fencing
policy, not treated as successful extraction.

Malformed inputs, encrypted PDFs (including ones with an empty password), text limits,
page limits, resource failures, timeouts and unsupported formats have fixed error codes.
Image XObjects/forms or inline images on a page without extractable text produce
`ocr_required`. A mixed readable/scanned document is conservatively rejected rather
than silently publishing incomplete text. Empty non-image documents produce empty_pdf.
OCR, visual verification, complex layout/table reconstruction and Windows deployment
are outside this increment. Inherited OCR text is not independently verified.

An initial native-child implementation failed because this macOS host rejects memory
resource-limit calls. It was replaced by the tested Linux container boundary; there is
no permissive native fallback. The Docker daemon and the local operator/image publisher
remain trusted. Container limits are not a multi-tenant security certification. No
arbitrary document instructions, SQL, JavaScript or Cypher are executed.

## Persistence and lifecycle

Additive knowledge migrations:

- `knowledge_0002`: nullable per-job extraction configuration, successful
  source_extractions history, and a nullable composite foreign key from chunks to their
  artifact/document/version. Original source/version/chunk identifiers are unchanged.
- `knowledge_0003`: append-only trigger rejects UPDATE/DELETE of successful artifact
  history. The schema owner can administer triggers; this is accidental-mutation
  protection, not adversarial protection against the database owner.

A successful artifact is unique by document/version/configuration. Repeated identical
processing reuses it, preserving its first timestamp. A different result for the same
identity is rejected. An explicit normalizer/config revision produces new history;
old results remain inspectable. Artifact JSON is revalidated even when a caller used
Pydantic model_copy to bypass construction validation. Source/config hashes, page/map
structure, contiguous chunk coverage, IDs/ranges/text and the current lease/generation
are checked before publication. This validates structural provenance and consistency;
it does not cryptographically prove that parser output is a correct visual rendering.

Artifact insertion, chunk replacement and successful job acknowledgement share one
transaction. Tests inject a storage failure after artifact insertion: all effects roll
back, old chunks survive, retry state is safe, and the next attempt publishes atomically.
Pending/retried/failed superseding jobs have no active extraction projection. Deletion
removes chunk pointers and immediately removes active visibility; history remains.
Stale workers cannot insert history or publish chunks after update/delete/reclaim.

PDF submissions automatically use the default extraction configuration. Existing UTF-8
jobs retain 3.1 behavior; opt in using `reprocess --extract`. Reprocessing preserves the
previous requested configuration unless explicitly changed. There is no learned model
upgrade path yet; only real implemented processor/normalizer revisions are exercised.
Failed attempts are observable through existing job status/config/error codes, not
immutable per-attempt NLP run records. History inspection is bounded to ten rows / 2 MB;
exceeding that bound fails explicitly. Pagination and retention/purge are future work.

## Runnable commands

With existing private local infrastructure settings:

```sh
make setup
make infra-up
make pdf-parser-build
make documents-bootstrap
make documents-migrate
uv run --locked atlas-documents submit evaluation/nlp/source-v1/retail.pdf \
  --collection retail --source-uri atlas://docs/retail-pdf --source-version v1 \
  --observed-at 2025-01-01T00:00:00+00:00 --kind technical --media-type application/pdf
make documents-work
uv run --locked atlas-documents status JOB_UUID
uv run --locked atlas-documents extractions DOCUMENT_UUID --active-only
uv run --locked atlas-documents reprocess DOCUMENT_UUID --extract --normalization identity-v1
make documents-work
uv run --locked atlas-documents delete DOCUMENT_UUID
uv run --locked atlas-documents extractions DOCUMENT_UUID
```

The CLI lifecycle was actually exercised against the dedicated knowledge database,
including PDF submit/work/status/inspection, explicit rule revision reprocessing and
deletion. It retained two history records and returned no active artifact after delete.
Do not expose these mutation/inspection commands or owner credentials to MCP/agents.

Verification commands actually run:

```sh
make format check test-all
make documents-integration
uv run --locked alembic -c knowledge-alembic.ini check
uv build
uv run --locked python -m scripts.evaluate_source_extraction
uv run --locked python scripts/verify_clean_mcp_setup.py
```

The runners enable ATLAS_TEST_PDF_PARSER=1; first build the image. Default pytest skips
explicit service cases and never downloads a model or silently pulls a parser image.
The fresh-source harness verifies existing setup/MCP and knowledge bootstrap/migrations/
text processing. The additional CLI lifecycle and live integrations verify PDF behavior.

## Actual verification and evaluation

| Check | Result |
|---|---|
| Complete suite with actual services | 169 passed, 0 skipped, 1 warning, 36.57s |
| Focused PostgreSQL/PDF/runtime integrations | 21 passed, 0 skipped, 8.02s |
| Default suite | 130 passed, 39 skipped, 1 warning, 9.71s |
| Ruff formatting/lint | Passed; 122 Python files |
| Strict mypy | Passed; 91 source files |
| Knowledge migration drift / source+wheel builds | Passed |
| Fresh checkout, temporary PostgreSQL | Existing setup/demo and new migrations passed; disposable container/volume removed |

The 39 default skips are 10 existing analytics/MCP, 17 PostgreSQL document, 4 PDF runtime,
1 Kafka and 7 simulator cases. Every one passed in test-all. The warning is the existing
upstream Starlette/httpx TestClient deprecation. Phase 2/3.1 regressions are included.
No PostgreSQL driver/store mock is used. The cancellation runtime test explicitly
substitutes a sleep entrypoint in a real restricted container to hold it open; rollback
uses an explicit application/database-event failure seam, and lease-expiry tests change
DB timestamps. These do not claim actual process-crash or memory-exhaustion injection.

Corpus `source-v1` contains **seven controlled PDFs**: three readable documents/six
labeled pages and four unsupported examples (external/inline scanned image, encrypted,
malformed). Six separately authored normalization cases specify exact source spans.
Labels were handwritten from fixture authorship, never copied from extractor output.
Fixture builder, PDF bytes and labels are version-controlled. Encrypted fixture
regeneration changes random PDF encryption bytes; the exact evaluated byte/label hash
is recorded in the result artifact, not assumed from a name.

Measured results in [source-extraction-v1.json](../evaluation/results/source-extraction-v1.json):

| Metric | Actual result |
|---|---|
| Page text accuracy after whitespace normalization | 6/6 (100%) |
| Source-span mapping accuracy | 6/6 (100%) |
| Unsupported-status accuracy | 4/4 (100%) |
| Extraction latency | p50 353.72 ms; maximum 375.31 ms (three readable PDFs) |

Exact dataset hash, parser image ID, package/config versions, timestamp and host runtime
are recorded. This tiny authored corpus measures source extraction only. It does not
establish real enterprise NLP/general layout accuracy. An earlier inline-image regression
scored 3/4 statuses; it caught false truthiness on an empty PDF content-stream dictionary.
That defect was fixed, image rebuilt and all final checks rerun. Native-memory, annotation
and lint errors from intermediate runs were also fixed rather than counted as passes.

## Remaining Phase 3.2 requirements

| Requirement | Status at this stopping point |
|---|---|
| PDF extraction, explicit unsupported statuses, page/source mapping and limits | Verified for the documented parser subset |
| Typed extractor, normalization and successful extraction provenance | Verified |
| Durable job integration, stale fencing, source ID compatibility, append-only history | Verified |
| spaCy/Hugging Face NLP processing, pinned statistical models | Missing; 3.2b |
| All technical entities, SQLGlot spans/dialects/aliases/dependencies | Missing; 3.2b |
| Namespace-aware canonicalization/resolution/candidates/ambiguity | Missing; 3.2b |
| Explicit/parser/model-inferred relationships and classification | Missing; 3.2b |
| Entity/candidate/decision/relationship/model registry/provenance tables | Missing; 3.2b |
| Entity/relationship labeled corpus and per-type metrics | Missing; 3.2b |
| Entity and relationship precision/recall/F1 | Unavailable, not fabricated |
| Resolution and ambiguity accuracy | Unavailable, not fabricated |

Complete 3.2b on immutable source artifacts before Phase 3.3 embeddings/hybrid retrieval.
No Neo4j, vector store, LangGraph, investigation engine or bitemporal agent memory was
introduced. This increment is recorded in the Phase 3.2a source-extraction commit.

## Files changed in this increment

Modified:

```text
AGENTS.md
DECISIONS.md
Makefile
PROJECT_STATE.md
README.md
docs/architecture.md
docs/file-inventory.md
docs/phase-3.md
evaluation/README.md
packages/contracts/src/atlas_contracts/documents.py
packages/core/src/atlas_core/documents.py
packages/database/src/atlas_database/documents.py
packages/database/src/atlas_database/documents_schema.py
pyproject.toml
scripts/README.md
scripts/test_documents_integration.py
scripts/test_simulator_integration.py
services/documents/README.md
services/documents/src/atlas_documents/cli.py
services/documents/src/atlas_documents/worker.py
tests/documents/test_chunking.py
tests/integration/test_documents.py
uv.lock
```

Created:

```text
.dockerignore
docs/phase-3.2a.md
evaluation/nlp/source-v1/encrypted.pdf
evaluation/nlp/source-v1/incident.pdf
evaluation/nlp/source-v1/inline-scanned.pdf
evaluation/nlp/source-v1/labels.json
evaluation/nlp/source-v1/malformed.pdf
evaluation/nlp/source-v1/retail.pdf
evaluation/nlp/source-v1/scanned.pdf
evaluation/nlp/source-v1/table.pdf
evaluation/results/source-extraction-v1.json
infrastructure/docker/pdf-parser/Dockerfile
packages/contracts/src/atlas_contracts/extraction.py
packages/contracts/src/atlas_contracts/extraction_config.py
packages/core/src/atlas_core/extraction.py
packages/database/knowledge_migrations/versions/0002_source_extraction.py
packages/database/knowledge_migrations/versions/0003_immutable_extraction.py
scripts/build_pdf_fixtures.py
scripts/evaluate_source_extraction.py
services/documents/src/atlas_documents/extraction.py
services/documents/src/atlas_documents/pdf_worker.py
tests/documents/test_extraction.py
tests/integration/test_pdf_runtime.py
```
