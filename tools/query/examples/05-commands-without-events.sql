-- Sanity check: every ReconcileInvoice command should share a correlation id with a PaymentReceived
SELECT c.correlation_id
FROM domain_events c
LEFT JOIN domain_events e
  ON e.correlation_id = c.correlation_id AND e.kind = 'event'
WHERE c.kind = 'command' AND e.event_id IS NULL;
