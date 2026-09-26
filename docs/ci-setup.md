# CI/CD setup

The pipeline is two workflows plus one reusable deploy workflow:

| Workflow | Trigger | What it does |
|---|---|---|
| `pr.yml` | pull requests, and called by `deploy.yml` | lint (incl. `terraform fmt`) → unit + contract tests → integration against a **fresh LocalStack** (`tflocal` plan + policy checks + apply) → `terraform validate` → `terraform plan` for dev (only if a read-only role is configured), with the plan policy checks |
| `deploy.yml` | push to `main` | reruns the full PR pipeline, then deploys `dev` and runs the smoke test |
| `deploy-env.yml` | called with `environment: <name>` | assumes the environment's AWS role via OIDC, `task deploy ENV=<name>`, `task smoke ENV=<name>` |

Every step calls the same `task` targets developers use locally. No AWS credentials exist in the
PR pipeline except the optional read-only plan role.

## 1. Repository secret

| Secret | Used by | Notes |
|---|---|---|
| `LOCALSTACK_AUTH_TOKEN` | integration job | LocalStack auth token. S3 Tables, ECR and container-image Lambdas need a licensed plan (Base or above for commercial use). |

Pull requests from forks cannot read secrets, so the integration job is skipped for them.

## 2. One-time AWS setup per account

1. **Create the Terraform state bucket** (once per account; one bucket can hold every
   environment's state under its own key). Versioned, encrypted, private:

   ```sh
   aws s3api create-bucket --bucket <state-bucket> --region <region> \
     --create-bucket-configuration LocationConstraint=<region>
   aws s3api put-bucket-versioning --bucket <state-bucket> --versioning-configuration Status=Enabled
   aws s3api put-public-access-block --bucket <state-bucket> --public-access-block-configuration \
     BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```

   State locking uses S3 conditional writes (`use_lockfile=true`), so no DynamoDB table is needed.
   The state key is `event-platform/<env>.tfstate`.

2. **Create the GitHub OIDC identity provider** (once per account). In IAM → Identity providers
   → Add provider: OpenID Connect, URL `https://token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`. Or with the CLI:

   ```sh
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com
   ```

3. **Create the deploy role** (one per environment is recommended). Trust policy, replacing the
   account id, org/repo and environment name:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com" },
       "Action": "sts:AssumeRoleWithWebIdentity",
       "Condition": {
         "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
         "StringLike": { "token.actions.githubusercontent.com:sub": "repo:<org>/<repo>:environment:dev" }
       }
     }]
   }
   ```

   The `sub` condition pins the role to the `dev` GitHub environment, so only jobs that passed
   that environment's protection rules can assume it. Use `environment:staging` / `environment:prod`
   for the other roles.

   Permissions: Terraform creates the resources directly, so the deploy role needs create/update/
   delete rights on every service used, plus read/write on the state bucket. Scope it with a
   resource-name condition where the service supports it (everything is prefixed
   `event-platform-<env>-`). Services: EventBridge, SQS, SNS, Lambda, IAM (roles and inline
   policies for the Lambda roles only, e.g. `arn:aws:iam::<account-id>:role/event-platform-<env>-*`),
   CloudWatch Logs and Alarms, DynamoDB, S3, S3 Tables, ECR, X-Ray. A permissions boundary on the
   Lambda roles is a good addition if your account requires one.

   The smoke test runs with the same role and additionally needs `events:PutEvents` on the ingress
   bus and `s3tables:Get*`/`GetTableData` on the table bucket and its tables.

4. **Optional read-only plan role** for pull requests. Same trust policy but with
   `"sub": "repo:<org>/<repo>:pull_request"`, and read-only permissions (`ReadOnlyAccess` or a
   scoped equivalent) plus `s3:GetObject`/`s3:ListBucket` on the state bucket. `terraform plan`
   also needs `lambda:GetFunction` etc., all covered by read-only access.

## 3. GitHub environments and variables

Create a GitHub environment called `dev` (Settings → Environments). Set these **environment
variables** (not secrets; ARNs and regions are not sensitive):

| Variable | Example |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::123456789012:role/event-platform-dev-deploy` |
| `AWS_REGION` | `eu-west-1` |
| `TF_STATE_BUCKET` | `my-org-terraform-state` |
| `OWNER` (optional) | value of the `owner` tag, defaults to `platform-team` |

Repository-level variables (optional, for the PR plan job):

| Variable | Example |
|---|---|
| `AWS_PLAN_ROLE_ARN` | `arn:aws:iam::123456789012:role/event-platform-pr-plan` |
| `AWS_REGION` | `eu-west-1` |
| `TF_STATE_BUCKET` | `my-org-terraform-state` |

### Adding staging and prod later

1. Create the GitHub environment (`staging`, `prod`) with **required reviewers** and, if wanted,
   a deployment branch rule limited to `main`.
2. Set its `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and `TF_STATE_BUCKET` variables and create the
   matching IAM role with `environment:<name>` in the trust policy.
3. Add `infra/envs/<name>.tfvars` (copy `dev.tfvars` and change `environment`).
4. Uncomment/copy the `deploy-staging` job in `.github/workflows/deploy.yml`. The reusable
   workflow, the Taskfile and the Terraform code do not change.

The `concurrency` group on each deploy job (`deploy-<env>`, `cancel-in-progress: false`) makes
GitHub queue a second push to `main` behind the running deploy instead of running both.

## 4. What the deploy does

`task deploy ENV=dev` runs `terraform init` against the state bucket, `terraform plan` with
`infra/envs/dev.tfvars`, the plan policy checks (`tools/build/policy_check.py`: no wildcard IAM,
every queue encrypted with a DLQ and alarm, every EventBridge target with retry policy and DLQ,
TLS-only buckets and queues, tags), then `terraform apply` of that exact plan and writes
`build/outputs.dev.json`. The account and region come from the assumed role and `AWS_REGION`;
nothing is hardcoded. The archive writer image is built on the runner by a Terraform provisioner
and pushed to the ECR repository Terraform creates. `task smoke ENV=dev` then sends one
synthetic `payment.succeeded` (id prefix `evt_synthetic_`, `synthetic: true`) and waits until it
appears in `ingress_events` and both of its domain messages appear in `domain_events`.
