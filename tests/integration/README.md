# Live integrations

`make sim-integration` tests real PostgreSQL fault/reset/replay, observer permissions and
Kafka delivery/replay with persistent deduplication. Temporary schemas/topics are cleaned
up. Tests skip without explicit ATLAS_TEST_POSTGRES_URL, ATLAS_TEST_READER_URL and
ATLAS_TEST_KAFKA_BOOTSTRAP settings; the Make target loads them from private .env.
