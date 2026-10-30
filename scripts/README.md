# Repository automation

- bootstrap_simulator.py creates the dedicated local database and observer; --grant-reader
  applies explicit observation grants after migrations. Credentials remain in ignored .env.
- test_simulator_integration.py runs PostgreSQL/Kafka integrations using private local
  settings without exposing credentials in shell arguments.

- verify_clean_mcp_setup.py checks the documented fresh-source workflow in a private
  temporary copy and disposable PostgreSQL container, automatically cleaning up afterward.
- test_simulator_integration.py --all runs the full suite with live settings (make test-all).
- export_mcp_schemas.py regenerates the four public versioned MCP descriptor files.
