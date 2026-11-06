# Document ingestion and source extraction

Phase 3.1 provides durable source ingestion and provenance chunks. Increment 3.2a adds
bounded PDF extraction, mapped normalization and immutable successful artifacts through
the same jobs. See [3.2a setup, verification and limits](../../docs/phase-3.2a.md).

Build the isolated parser with make pdf-parser-build. Use atlas-documents for operator
submission/work/reprocessing and extraction inspection. spaCy/Transformers, technical
entities, resolution and relationships remain 3.2b; full Phase 3.2 is incomplete.
