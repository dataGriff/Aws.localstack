# Generator fixtures

Each file is one third-party payload replayed onto the ingress bus in file-name order.

| Folder | Purpose | Expected outcome |
|---|---|---|
| `valid/` | Well-formed `payment.succeeded` events | One `PaymentReceived` and one `ReconcileInvoice` each |
| `duplicates/` | The same provider event delivered three times | Exactly one domain event and one command execution |
| `out_of_order/` | Delivery order differs from `created` order | Each translated independently; `occurredAt` preserves provider time |
| `invalid/` | Payloads that violate the contract | Quarantined to S3 with the reason; batch not failed |

Contract tests assert that every file outside `invalid/` validates against
`contracts/schemas/thirdparty/payment.succeeded.schema.json` and every file inside it does not.
