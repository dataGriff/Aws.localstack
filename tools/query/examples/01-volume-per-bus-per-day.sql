-- How many events per bus per day (partition column is day(event_time))
SELECT 'ingress' AS bus, date_trunc('day', event_time) AS day, count(*) AS events
FROM ingress_events GROUP BY 1, 2
UNION ALL
SELECT 'domain', date_trunc('day', event_time), count(*)
FROM domain_events GROUP BY 1, 2
ORDER BY bus, day;
