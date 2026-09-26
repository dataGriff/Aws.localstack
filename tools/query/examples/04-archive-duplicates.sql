-- SQS is at-least-once: a batch that partially failed can archive the same event twice.
-- Dedupe on event_id when it matters (or use this to see whether it ever happened).
SELECT bus, event_id, count(*) AS copies
FROM (SELECT bus, event_id FROM ingress_events UNION ALL SELECT bus, event_id FROM domain_events)
GROUP BY 1, 2 HAVING count(*) > 1;
