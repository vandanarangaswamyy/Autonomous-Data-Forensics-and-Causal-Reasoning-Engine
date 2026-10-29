# Repository automation

- bootstrap_simulator.py creates the dedicated local database and observer; --grant-reader
  applies explicit observation grants after migrations. Credentials remain in ignored .env.
- test_simulator_integration.py runs PostgreSQL/Kafka integrations using private local
  settings without exposing credentials in shell arguments.
