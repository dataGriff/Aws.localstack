-- The most recent domain messages with the interesting envelope columns
SELECT event_time, kind, type, correlation_id, json_extract_string(detail, '$.source') AS third_party_id
FROM domain_events
ORDER BY event_time DESC
LIMIT 20;
