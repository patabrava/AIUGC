-- Refresh PostgREST after production RPC contract changes.
-- The queue probe repairs stale notification state without restarting Postgres.

SELECT pg_catalog.pg_notification_queue_usage();
NOTIFY pgrst, 'reload schema';
