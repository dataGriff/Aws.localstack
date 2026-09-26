# Implementation plan: event-driven ingestion, commands, and Iceberg archive

Status: **approved** (REST catalog everywhere; defaults for all other open questions).

## 1. Architecture summary

```
 tools/generator ──PutEvents──▶ ingress bus ──rule──▶ translator queue (+DLQ) ──▶ translator Lambda
                                   │                                                │  validate (contract)
                                   │ catch-all rule                                 │  quarantine invalid → S3
                                   ▼                                                │  dedupe (Powertools idempotency, DynamoDB)
                             archive queue (+DLQ)                                   │  map → envelope(s), validate, PutEvents
                                   ▲                                                ▼
                                   │ catch-all rule                          domain bus
                                   │                                          │ rule per command (kind=command, type=X)
                                   │                                          ▼
                                   │                             reconcile-invoice queue (+DLQ, rule-target DLQ)
                                   │                                          ▼
                                   │                             reconcile-invoice Lambda ──▶ DynamoDB reconciliations
                                   ▼
                  archive writer Lambda (container image, batched SQS, MaximumConcurrency=2)
                  parse → group by table → Arrow table → PyIceberg append() (retry on CommitFailed)
                  unparseable → quarantine S3 prefix
                                   ▼
                  Iceberg: archive.ingress_events / archive.domain_events, partition day(event_time)
                  Everywhere: PyIceberg RestCatalog. AWS: S3 Tables Iceberg REST endpoint.
                  Local + CI: LocalStack's S3 Tables Iceberg REST endpoint (same code, different env vars).
```

Catalog configuration is by environment variables only (REST URI, warehouse ARN, SigV4 flag, optional S3 endpoint). The writer code path is identical in every environment.

## 2. Repository layout

```
.
├── .mise.toml                     # python 3.12, node 22, task, awscli, awscli-local, cdklocal, duckdb
├── Taskfile.yml                   # every developer and CI command
├── pyproject.toml                 # single project; uv/pip-installable; ruff, pytest, mypy config
├── uv.lock (or requirements*.txt) # see open question 3
├── README.md                      # architecture, local run, local-vs-AWS differences
├── docs/
│   ├── plan.md                    # this file
│   ├── ci-setup.md                # OIDC role, secrets, variables
│   └── adr/                       # short decision records (catalog choice, container writer, ...)
├── contracts/                     # SOURCE OF TRUTH — never edited to make tests pass
│   ├── asyncapi.yaml              # AsyncAPI 3.0 root: channels = ingress bus, domain bus; messages below
│   └── schemas/
│       ├── envelope.schema.json
│       ├── thirdparty/payment.succeeded.schema.json
│       ├── events/PaymentReceived.schema.json
│       └── commands/ReconcileInvoice.schema.json
├── src/
│   ├── shared/                    # domain-agnostic library (packaged as a Lambda layer / vendored per fn)
│   │   ├── envelope.py            # Envelope model, build/serialize, detail-type + source rules
│   │   ├── contracts.py           # schema loader + jsonschema validators (Draft 2020-12)
│   │   ├── eventbridge.py         # PutEvents batching (10/call), FailedEntryCount retry with backoff
│   │   ├── quarantine.py          # S3 quarantine writer (key = <reason>/<date>/<id>.json)
│   │   ├── logging.py             # Powertools Logger with correlation id injection
│   │   └── config.py              # env-var settings objects (no ARNs/regions hardcoded)
│   ├── domain/                    # SWAPPABLE — the only place that knows about payments
│   │   ├── registry.py            # maps third-party event type → mapper; command type → handler name
│   │   ├── mappers/payment_succeeded.py   # payment.succeeded → [PaymentReceived, ReconcileInvoice]
│   │   └── commands/reconcile_invoice.py  # pure command logic (no AWS calls in the core)
│   ├── functions/
│   │   ├── translator/app.py      # SQS batch → validate → idempotent → map → publish; partial batch response
│   │   ├── reconcile_invoice/app.py   # SQS batch → idempotent on envelope id → DynamoDB write
│   │   ├── archive_writer/        # Dockerfile + app.py + writer.py (parse/group/arrow/append/retry)
│   │   └── table_setup/app.py     # CDK custom resource handler: idempotent namespace + table creation
│   └── iceberg/
│       ├── catalog.py             # load_catalog() from env vars only (REST)
│       ├── schema.py              # the archive table schema + partition spec (single definition)
│       └── setup.py               # ensure_tables(catalog) used by the custom resource
├── infra/                         # AWS CDK (Python)
│   ├── app.py                     # composes stacks per environment from cdk.json context
│   ├── cdk.json
│   ├── config.py                  # per-env settings: batch size, window, retention, tags, owner
│   └── stacks/
│       ├── buses_stack.py         # ingress + domain buses, archive catch-all rules, shared archive queue + DLQ
│       ├── translator_stack.py    # ingress rule → queue/DLQ → Lambda, idempotency table, quarantine bucket
│       ├── commands_stack.py      # per-command rule/queue/DLQ/handler + result table; driven by domain registry
│       ├── archive_stack.py       # ECR image Lambda, ESM config, S3 Tables bucket / local S3 bucket, custom resource
│       └── monitoring.py          # alarm helpers (DLQ depth, writer errors) applied by every stack
├── tools/
│   ├── generator/                 # `python -m tools.generator --bus <name> --fixtures <dir> [--synthetic]`
│   │   └── fixtures/              # valid/, duplicates/, out_of_order/, invalid/ (each also a contract test input)
│   ├── query/                     # DuckDB session over PyIceberg scans + examples/*.sql
│   └── smoke/                     # post-deploy smoke test (send synthetic event, poll domain bus archive)
├── tests/
│   ├── unit/                      # mappers, envelope, validation, PutEvents retry, grouping/Arrow, handler core
│   ├── contracts/                 # AsyncAPI validation + every fixture vs its schema
│   ├── integration/               # against LocalStack after `task local:deploy`; polling helpers with timeouts
│   └── helpers/                   # poll_until(), LocalStack clients, fresh-state fixtures
├── .github/workflows/
│   ├── pr.yml                     # lint → unit+contracts → integration (fresh LocalStack) → synth/diff
│   └── deploy.yml                 # on main: reuse pr.yml (workflow_call) → deploy dev (OIDC) → smoke
└── .localstack/                   # docker-compose.yml for LocalStack, init hooks if needed
```

## 3. Key design decisions

| Topic | Decision |
|---|---|
| Envelope | `id, kind, type, schemaVersion, occurredAt, correlationId, causationId, source, data`. `detail-type = type`, `source = "<project>.translator"` (stable service name). Ingress bus keeps the raw payload; `source = "<project>.thirdparty.<provider>"`, `detail-type = provider event type`. |
| Idempotency | Translator keyed on third-party `id`; handler keyed on envelope `id`. Both via Powertools `DynamoDBPersistenceLayer` with TTL. A duplicate returns the cached result and publishes nothing. |
| Out-of-order events | Translator does not reorder. Each event maps independently; `occurredAt` is the provider `created` timestamp so ordering is recoverable downstream and in the archive. |
| Command routing | One EventBridge rule per command from the domain registry: pattern `{"detail": {"kind": ["command"], "type": ["ReconcileInvoice"]}}`. Target = the handler's SQS queue, with rule-level retry policy (24h / 185 attempts by default, configurable) and a rule-target DLQ. Queue policy grants `events.amazonaws.com` with `aws:SourceArn = rule ARN`. |
| Archive ESM | Batch size and batching window from CDK context (defaults 500 / 60s), `MaximumConcurrency=2`, `ReportBatchItemFailures`, queue visibility timeout = 6× Lambda timeout. |
| Archive writer | Container image (`public.ecr.aws/lambda/python:3.12` base) with PyIceberg + PyArrow. Groups records by bus → one Arrow table → one `append()` per table per batch. `CommitFailedException` retried with exponential backoff and jitter; after exhaustion the whole batch is reported failed (safe: append is atomic per table). Records that fail to parse go to quarantine and are acknowledged. |
| Table schema | `event_id string, bus string, source string, detail_type string, kind string, type string, correlation_id string, schema_version string, event_time timestamptz, ingested_at timestamptz, detail string`. Partition spec `day(event_time)`. Two tables: `archive.ingress_events`, `archive.domain_events`. |
| Table creation | `ensure_tables()` runs inside a CDK custom resource (Provider framework, a second handler in the writer's container image). The same custom resource runs under `cdklocal`, so there is one code path everywhere. |
| Catalog | PyIceberg `RestCatalog` everywhere. AWS: S3 Tables' Iceberg REST endpoint with SigV4 (signing name `s3tables`). Local/CI: LocalStack's S3 Tables Iceberg REST endpoint, plus an `s3.endpoint` override so data files go to LocalStack S3. Compaction / snapshot expiry: S3 Tables maintenance in AWS, none locally (documented). |
| IAM | Per-function roles with explicit actions on explicit ARNs. Only unavoidable wildcards: `xray:PutTraceSegments` / `PutTelemetryRecords` (resource `*` required by the service) and S3 object-level `arn:.../*` under a specific bucket prefix. |
| Security | SSE-KMS or SSE-S3 (open question 5) on buckets; SQS SSE; DynamoDB encryption; bucket policy denying non-TLS; block public access. |
| Observability | Powertools Logger (JSON, correlation id from envelope), Tracer on every function, log retention from config, alarms on every DLQ `ApproximateNumberOfMessagesVisible > 0` and on writer `Errors`. |
| Tags | `project`, `environment`, `owner` applied at app level via `Tags.of(app)`. |

## 4. Milestones

Each milestone ends with `task lint test:unit test:contracts` green (and integration where applicable) and one commit.

1. **Scaffold** — mise, Taskfile, pyproject, ruff/mypy, AsyncAPI 3.0 contract + JSON Schemas, envelope + validation library, generator fixtures, unit + contract test harness, CI-independent tooling. Deliverable: `task setup lint test:unit test:contracts` pass.
2. **Ingress → translator** — buses stack, translator stack, translator Lambda (validate, quarantine, idempotency, map, PutEvents retry, partial batch), generator CLI, `task local:up/deploy/seed/down`, first integration test (valid event reaches domain bus; duplicates deduped; invalid quarantined).
3. **Commands** — commands stack driven by the domain registry, reconcile-invoice handler, DynamoDB result table, integration tests for end-to-end result, single execution for duplicates, poison message to DLQ.
4. **Archive** — Iceberg schema/catalog/setup, container writer, archive stack (ESM, alarms, custom resource), `task query` with DuckDB and example SQL, integration test polling both tables via PyIceberg.
5. **CI/CD and docs** — `pr.yml`, `deploy.yml` (workflow_call reuse, OIDC, environments, concurrency), smoke tool and `task smoke`, `cdk synth`/`diff` job, `docs/ci-setup.md`, final README.

## 5. Open questions

Approved with defaults on 2026-09-26. Kept for the record.

1. **No Docker in this session.** The cloud container I am working in has no Docker daemon, so I cannot run LocalStack or build the writer image here. I will write and unit-test everything, and write the integration tests and Taskfile targets carefully, but the first real LocalStack run will be in your GitHub Actions PR pipeline or on your machine. Are you OK with that, and does the repo have `LOCALSTACK_AUTH_TOKEN` set as a secret already?
2. **S3 Tables in LocalStack.** Resolved: LocalStack emulates S3 Tables including an Iceberg REST endpoint, so the REST catalog is used everywhere and `SqlCatalog` is dropped. Decision: switch to REST everywhere (approved).
3. **Dependency manager.** Default: `uv` (managed by mise) with `pyproject.toml` + `uv.lock`; Lambda zips built from a locked export. Alternative: plain pip + `requirements.txt`.
4. **Packaging of shared code.** Default: CDK `PythonFunction`-style bundling via Docker is unavailable in CI-less-Docker contexts, so I will bundle zips with a Taskfile step (`uv pip install --target`) and point CDK at the built directories. The archive writer is the only container image. Say if you prefer `aws-cdk.aws-lambda-python-alpha` instead (it needs Docker for every function).
5. **Encryption keys.** Default: AWS-managed keys (SSE-S3, SQS-managed SSE, DynamoDB default). Customer-managed KMS keys add cost and IAM surface; say if you want them.
6. **S3 Tables catalog auth.** Default: PyIceberg `RestCatalog` with `rest.sigv4-enabled=true`, signing name `s3tables`, endpoint `https://s3tables.<region>.amazonaws.com/iceberg`, warehouse = the table bucket ARN. Region comes from the Lambda's `AWS_REGION` at runtime. Confirm you want a *dedicated* S3 table bucket per environment (default) rather than reusing an existing one.
7. **Deployed environments and account.** Default: single `dev` GitHub environment now, CDK env from `AWS_REGION` and `CDK_DEFAULT_ACCOUNT` (resolved from the OIDC session, never in code). Bootstrapping (`cdk bootstrap`) is assumed done by you once per account; documented in `docs/ci-setup.md`.
8. **Command handler retries.** Default: SQS `maxReceiveCount=3` before the queue DLQ; Lambda partial batch failures re-drive only failed messages. Say if you want a different count.
9. **Rule-level retry for the archive target.** Default: same retry policy and a separate rule DLQ for the catch-all rules on each bus, mirroring command rules.
10. **Domain registry shape.** Default: a Python module mapping provider event type → mapper function and command type → handler metadata (queue name, Lambda entry point). CDK reads the same registry to generate rules/queues, so adding a command means one mapper + one handler + one registry entry. Confirm this coupling between `src/domain` and `infra/` is what you want.
11. **Fixture realism.** Default: Stripe-like `payment.succeeded` shape (`id`, `type`, `created` epoch seconds, `data.object` with `amount`, `currency`, `invoice`, `customer`). Placeholder only; not claiming Stripe compatibility.

## 6. Things I will stop and report on rather than work around

- Any LocalStack gap for EventBridge rule-target DLQs, SQS partial batch responses, ESM `MaximumConcurrency`, or container-image Lambdas (the last requires LocalStack Base or above; the brief already assumes it).
- Any contract I believe is wrong.
- Any need for a wildcard IAM action that is not service-mandated.
