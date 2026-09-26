# Event platform on AWS (LocalStack-first)

Event-driven ingestion of third-party webhooks, translation into domain events and commands,
SQS-backed command handlers, and an Apache Iceberg archive in Amazon S3 Tables. Everything runs
and is tested locally and in CI against LocalStack, and deploys to AWS on merge to `main`.

- **Language:** Python 3.12 for all Lambdas, tests and tools; Terraform (HCL) for infrastructure
- **IaC:** Terraform, one module per concern, `tflocal` (terraform-local) for LocalStack
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
| `infra/` | Terraform root module composing `modules/{buses,translator,commands,archive}` (plus `queue_pair`, `rule_dlq`, `lambda_function` helpers); `envs/<env>.tfvars` holds per-environment settings |
| `tools/` | `generator` (fixture replay), `query` (DuckDB), `smoke` (post-deploy), `build` (zip bundler, registry export, image build, plan policy checks), `localstack` (readiness) |
| `tests/` | `unit` (no emulator), `contracts`, `integration` (LocalStack) |
| `.github/workflows/` | `pr.yml`, `deploy.yml`, reusable `deploy-env.yml` |
| `docs/` | `plan.md` (approved design), `ci-setup.md` (OIDC role, secrets, variables) |

### Swapping the placeholder domain

The payment provider is a placeholder. To replace it: write new schemas under `contracts/schemas/`
and messages in `contracts/asyncapi.yaml`; add mappers under `src/domain/mappers/` and handler
logic under `src/domain/commands/`; add a handler Lambda under `src/functions/<name>/`; register
them in `src/domain/registry.py`. `task build` exports the registry to `build/registry.json`; the
Terraform `commands` module generates a rule, queue, DLQ, result table and Lambda per entry, and
the translator picks mappers from the same registry.
`shared/`, `functions/translator`, `functions/archive_writer` and `infra/` need no changes.

## Running locally

Prerequisites: Docker, [mise](https://mise.jdx.dev), a LocalStack auth token (S3 Tables,
ECR and container-image Lambdas need a licensed plan).

```sh
mise install                       # python, node, uv, task, terraform, tflocal, awscli, duckdb, asyncapi
cp .env.example .env               # put LOCALSTACK_AUTH_TOKEN in .env
task setup                         # uv sync + npm ci

task lint                          # ruff + mypy + terraform fmt
task validate                      # terraform init -backend=false + validate
task test:unit                     # fast, no emulator
task test:contracts                # AsyncAPI validation + fixtures against schemas

task local:up                      # fresh LocalStack container
task local:deploy                  # tflocal plan + policy checks + apply (writes build/outputs.local.json)
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
| Table creation | `aws_lambda_invocation` runs the table-setup handler (writer image) on every apply; `ensure_tables()` is idempotent | Same resource under `tflocal` |
| Table bucket | `aws_s3tables_table_bucket`, SSE-S3 | Same resource |
| Terraform state | S3 backend in `TF_STATE_BUCKET`, key `event-platform/<env>.tfstate`, lockfile locking | Same S3 backend, redirected by `tflocal` to a bucket in LocalStack (discarded with `task local:down`) |
| Writer image | Built by a `terraform_data` provisioner (`tools/build/image.sh`) and pushed to the ECR repo Terraform creates | Same, pushed to LocalStack ECR |
| Batching | 1000 records / 60 s window, writer timeout 300 s | 200 records / 5 s window so tests finish quickly |
| Log retention, idempotency TTL, retry policy | 14 days, 7 days, 185 attempts / 24 h | 1 day, 1 h, 3 attempts / 5 min |

All of these come from `infra/envs/<env>.tfvars` and end up as Lambda environment variables. The writer, table setup and query tool call one `iceberg.catalog.load()` that reads
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
(logs uploaded on failure) → `terraform validate` (+ `terraform plan` for dev with policy checks
when a read-only role is configured).
Merges to `main` rerun the whole pipeline, then deploy `dev` via an OIDC-assumed role and run
`task smoke ENV=dev`. See [docs/ci-setup.md](docs/ci-setup.md) for the role, secrets and
variables, and how to add `staging`/`prod`.

## Deploying to AWS by hand

```sh
export AWS_REGION=eu-west-1        # plus credentials for the target account
export TF_STATE_BUCKET=<state-bucket>
task plan ENV=dev                  # plan + policy checks only
task deploy ENV=dev                # plan + policy checks + apply
task smoke ENV=dev
```

## Infrastructure guard rails

`task plan` (and therefore every `local:deploy` and `deploy`) runs `tools/build/policy_check.py`
over the plan JSON and fails on: wildcard IAM actions, resource `*` (except the two X-Ray write
actions the service requires), unencrypted or non-TLS queues/buckets, work queues without a DLQ,
DLQs without a depth alarm, EventBridge targets without retry policy and DLQ, EventBridge queue
policies without an `aws:SourceArn` condition, Lambdas without active tracing, log groups without
retention, and resources missing the `project`/`environment`/`owner` tags.
