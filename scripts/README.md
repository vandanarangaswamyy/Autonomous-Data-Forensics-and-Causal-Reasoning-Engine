# Repository automation

- bootstrap_simulator.py creates the dedicated local database and observer; --grant-reader
  applies explicit observation grants after migrations. Credentials remain in ignored .env.
- test_simulator_integration.py runs PostgreSQL/Kafka integrations using private local
  settings without exposing credentials in shell arguments.

- verify_clean_mcp_setup.py checks the documented fresh-source workflow in a private
  temporary copy and disposable PostgreSQL container, automatically cleaning up afterward.
- test_simulator_integration.py --all runs the full suite with live settings (make test-all).
- export_mcp_schemas.py regenerates the four public versioned MCP descriptor files.

Phase 3 milestone 3.1: `python -m scripts.bootstrap_knowledge` provisions independent
private knowledge storage; `python -m scripts.test_documents_integration --all` runs
all suites with actual knowledge/simulator services enabled. `python -m
scripts.benchmark_ingestion` records actual ingestion throughput, not retrieval quality.
The clean setup verifier now exercises document submit/dedup/work/status as well as MCP.

Phase 3.2a: `python -m scripts.evaluate_source_extraction` evaluates handwritten page/
span/unsupported-status labels using the real isolated PDF parser. Build it first with
`make pdf-parser-build`. `build_pdf_fixtures` authors fixtures from literal text without
running extraction; regeneration changes random encrypted bytes and requires rerunning
evaluation. Entity/relationship/resolution metrics remain unavailable in this increment.
