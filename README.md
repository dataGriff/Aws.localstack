# Event platform on AWS (LocalStack-first)

Event-driven ingestion of third-party webhooks, translation into domain events and commands,
SQS-backed command handlers, and an Apache Iceberg archive in S3 Tables. Built with Python 3.12,
AWS CDK (Python), LocalStack, and GitHub Actions.

See [docs/plan.md](docs/plan.md) for the approved design and milestones. This README is completed
in the final milestone.

## Quick start

```sh
mise install            # tool versions from .mise.toml
task setup              # uv sync + npm ci
task lint test:unit test:contracts
```
