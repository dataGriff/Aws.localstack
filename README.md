# Event platform on AWS (LocalStack-first)

Event-driven ingestion of third-party webhooks, translation into domain events and commands,
SQS-backed command handlers, and an Apache Iceberg archive in Amazon S3 Tables. Everything runs
and is tested locally and in CI against LocalStack, and deploys to AWS on merge to `main`.

- **Language:** Python 3.12 everywhere (Lambdas, CDK, tests, tools)
- **IaC:** AWS CDK (Python), one stack per concern, `cdklocal` for LocalStack
- **Tooling:** `mise` for tool versions, `Taskfile.yml` for every command (CI uses the same targets)
- **Libraries:** AWS Lambda Powertools, boto3, jsonschema, PyIceberg + PyArrow, DuckDB, pytest

## Architecture

```
tools/generator ──PutEvents──▶ INGRESS BUS ──rule──▶ translator queue (+DLQ) ──▶ translator Lambda
   (fixtures)                       │                                              validate contract
                                    │ catch-all                                    quarantine invalid → S3
                                    ▼                                              dedupe on provider id (DynamoDB)
                             archive queue (+DLQ) ◀──catch-all──┐                  map → envelope → validate → PutEvents
                                    │                           │                              │
                                    │                       DOMAIN BUS ◀───────────────────────┘
                                    │                           │ one rule per command: detail.kind=command, detail.type=X
                                    │                           ▼
                                    │              reconcile-invoice queue (+DLQ) ──▶ reconcile-invoice Lambda ──▶ DynamoDB
                                    ▼
                    archive writer Lambda (container image, batched, MaximumConcurrency 2)
                    parse → group by table → Arrow → one append() per table → retry on commit conflict
                                    ▼
                    Iceberg tables archive.ingress_events / archive.domain_events, partition day(event_time)
                    catalog: S3 Tables Iceberg REST endpoint (AWS) · LocalStack S3 Tables REST endpoint (local/CI)
```

Every message on the domain bus uses the shared envelope (`contracts/schemas/envelope.schema.json`):
`id`, `kind`, `type`, `schemaVersion`, `occurredAt`, `correlationId`, `causationId`, `source`
(the third-party event id), optional `synthetic`, and `data`. EventBridge `detail-type` equals
`type` and `source` is the stable service name `event-platform.translator`, so rules route on them.

### Repository layout

| Path | Purpose |
|---|---|
| `contracts/` | **Source of truth.** AsyncAPI 3.0 document + JSON Schemas (third-party payload, envelope, each event, each command). Never edited to make a test pass. |
| `src/shared/` | Domain-agnostic runtime: envelope, contract validation, PutEvents batching/retry, quarantine, config, logging |
| `src/domain/` | **Swappable.** Registry (third-party type → mapper, command → handler), mappers, command logic. The only place that knows about payments. |
| `src/functions/` | Lambdas: `translator`, `reconcile_invoice` (zip), `archive_writer` (container image; also hosts the table-setup custom resource handler) |
| `src/iceberg/` | Archive table schema and partitioning, env-only REST catalog loader, idempotent `ensure_tables()` |
| `infra/` | CDK app and stacks: `buses`, `translator`, `commands`, `archive`; `cdk.json` holds per-environment settings |
| `tools/` | `generator` (fixture replay), `query` (DuckDB), `smoke` (post-deploy), `build` (zip bundler), `localstack` (readiness) |
| `tests/` | `unit` (no emulator, includes CDK template guard rails), `contracts`, `integration` (LocalStack) |
| `.github/workflows/` | `pr.yml`, `deploy.yml`, reusable `deploy-env.yml` |
| `docs/` | `plan.md` (approved design), `ci-setup.md` (OIDC role, secrets, variables) |

### Swapping the placeholder domain

The payment provider is a placeholder. To replace it: write new schemas under `contracts/schemas/`
and messages in `contracts/asyncapi.yaml`; add mappers under `src/domain/mappers/` and handler
logic under `src/domain/commands/`; add a handler Lambda under `src/functions/<name>/`; register
them in `src/domain/registry.py`. The CDK `CommandsStack` generates a rule, queue, DLQ, result
table and Lambda per registry entry, and the translator picks mappers from the same registry.
`shared/`, `functions/translator`, `functions/archive_writer` and `infra/` need no changes.

## Running locally

Prerequisites: Docker, [mise](https://mise.jdx.dev), a LocalStack auth token (S3 Tables,
ECR and container-image Lambdas need a licensed plan).

```sh
mise install                       # python, node, uv, task, awscli, duckdb, cdk, cdklocal, asyncapi
cp .env.example .env               # put LOCALSTACK_AUTH_TOKEN in .env
task setup                         # uv sync + npm ci

task lint                          # ruff + mypy
task test:unit                     # fast, no emulator
task test:contracts                # AsyncAPI validation + fixtures against schemas

task local:up                      # fresh LocalStack container
task local:deploy                  # cdklocal bootstrap + deploy --all (writes build/outputs.local.json)
task local:seed                    # replay valid, duplicate, out-of-order and invalid fixtures
task test:integration              # end-to-end assertions against LocalStack
task query                         # DuckDB shell over the Iceberg tables (see tools/query/examples/)
task query SQL="select bus, count(*) from ingress_events group by 1"
task query FILE=tools/query/examples/01-volume-per-bus-per-day.sql
task local:down                    # stop and discard state
```

The generator is also a CLI: `uv run python -m tools.generator --env local --sets valid --synthetic`.

## How local and AWS differ

| Concern | AWS | Local / CI (LocalStack) |
|---|---|---|
| Iceberg catalog | PyIceberg `RestCatalog` → S3 Tables Iceberg REST endpoint (`https://s3tables.<region>.amazonaws.com/iceberg`), SigV4 signed, warehouse = table bucket ARN | Same `RestCatalog` code → LocalStack's S3 Tables REST endpoint (`http://glue.localhost.localstack.cloud:4566/iceberg`), no SigV4, warehouse `<account>:s3tablescatalog/<bucket>`, data files via `s3.endpoint` |
| Data-plane credentials | Vended by the catalog per table (`X-Iceberg-Access-Delegation: vended-credentials`); the writer role holds only `s3tables:*Table*` actions on its bucket | Static `test` credentials |
| Compaction / snapshot expiry | Handled by **S3 Tables maintenance** (automatic compaction, snapshot management, unreferenced file removal). **There is no compaction job in this repository.** | None; tables grow until `task local:down` |
| Table creation | CDK custom resource (`Custom::IcebergTables`) runs `ensure_tables()` in the writer image | The same custom resource under `cdklocal` |
| Table bucket | `AWS::S3Tables::TableBucket`, SSE-S3, RETAIN | Same resource, DESTROY |
| Batching | 1000 records / 60 s window, writer timeout 300 s | 200 records / 5 s window so tests finish quickly |
| Log retention, idempotency TTL, retry policy | 14 days, 7 days, 185 attempts / 24 h | 1 day, 1 h, 3 attempts / 5 min |

All of these come from `cdk.json` → `context.environments.<env>` and end up as Lambda environment
variables. The writer, table setup and query tool call one `iceberg.catalog.load()` that reads
`ICEBERG_REST_URI`, `ICEBERG_WAREHOUSE`, `ICEBERG_NAMESPACE`, `ICEBERG_SIGV4`,
`ICEBERG_S3_ENDPOINT`.

## Behavioural notes

- **Invalid payloads** never fail a batch: they go to `s3://<quarantine>/translator/<reason>/YYYY/MM/DD/<id>.json`
  with the validation errors. Unparseable archive records go to `archive-writer/unparseable/`.
- **Duplicates** (same provider event id) produce exactly one domain event and one command;
  handlers are idempotent on the envelope id. Both use Powertools idempotency in DynamoDB with TTL.
- **Out-of-order** events are translated independently; `occurredAt` carries the provider time,
  which also drives the archive partition.
- **Poison messages** on a command queue fail that record only (partial batch response) and reach
  the queue's DLQ after `maxReceiveCount`. Every DLQ and rule-target DLQ has a depth alarm.
- **Archive at-least-once:** SQS can redeliver a batch whose append succeeded but whose
  invocation then failed, so an event can appear twice; dedupe on `event_id` when it matters
  (`tools/query/examples/04-archive-duplicates.sql`).
- **Observability:** JSON logs with correlation ids (Powertools), X-Ray tracing on every function,
  CloudWatch alarms on every DLQ depth and on writer errors, all resources tagged
  `project`/`environment`/`owner`.

## CI/CD

Pull requests run lint → unit + contract tests → integration against a fresh LocalStack container
(logs uploaded on failure) → `cdk synth` (+ `cdk diff` when a read-only role is configured).
Merges to `main` rerun the whole pipeline, then deploy `dev` via an OIDC-assumed role and run
`task smoke ENV=dev`. See [docs/ci-setup.md](docs/ci-setup.md) for the role, secrets and
variables, and how to add `staging`/`prod`.

## Deploying to AWS by hand

```sh
export AWS_REGION=eu-west-1        # plus credentials for the target account
task deploy ENV=dev
task smoke ENV=dev
```
