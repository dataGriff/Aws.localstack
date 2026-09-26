-- Follow one third-party event through the system: ingress row, then every domain message it caused
WITH flows AS (
  SELECT correlation_id, min(event_time) AS started, count(*) AS messages,
         list(type ORDER BY event_time) AS types
  FROM domain_events GROUP BY correlation_id
)
SELECT * FROM flows ORDER BY started DESC LIMIT 20;
