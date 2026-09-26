# CI/CD setup

The pipeline is two workflows plus one reusable deploy workflow:

| Workflow | Trigger | What it does |
|---|---|---|
| `pr.yml` | pull requests, and called by `deploy.yml` | lint → unit + contract tests → integration against a **fresh LocalStack** → `cdk synth` (dev) → `cdk diff` (dev, only if a read-only role is configured) |
| `deploy.yml` | push to `main` | reruns the full PR pipeline, then deploys `dev` and runs the smoke test |
| `deploy-env.yml` | called with `environment: <name>` | assumes the environment's AWS role via OIDC, `task deploy ENV=<name>`, `task smoke ENV=<name>` |

Every step calls the same `task` targets developers use locally. No AWS credentials exist in the
PR pipeline except the optional read-only diff role.

## 1. Repository secret

| Secret | Used by | Notes |
|---|---|---|
| `LOCALSTACK_AUTH_TOKEN` | integration job | LocalStack auth token. S3 Tables, ECR and container-image Lambdas need a licensed plan (Base or above for commercial use). |

Pull requests from forks cannot read secrets, so the integration job is skipped for them.

## 2. One-time AWS setup per account

1. **Bootstrap CDK** in each account/region you deploy to (once, with admin credentials):

   ```sh
   npx cdk bootstrap aws://<account-id>/<region>
   ```

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

   Permissions policy: CDK deploys through the bootstrap roles, so the deploy role only needs to
   assume them and read the bootstrap version parameter:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "sts:AssumeRole",
         "Resource": "arn:aws:iam::<account-id>:role/cdk-hnb659fds-*-<account-id>-<region>"
       },
       {
         "Effect": "Allow",
         "Action": "ssm:GetParameter",
         "Resource": "arn:aws:ssm:<region>:<account-id>:parameter/cdk-bootstrap/hnb659fds/version"
       }
     ]
   }
   ```

   The smoke test runs with the same role and needs to publish one event and read the archive
   tables. Add to the same policy (bus and table bucket ARNs come from the deployed stacks):

   ```json
   {
     "Effect": "Allow",
     "Action": ["events:PutEvents"],
     "Resource": "arn:aws:events:<region>:<account-id>:event-bus/event-platform-dev-ingress"
   },
   {
     "Effect": "Allow",
     "Action": ["s3tables:GetTableBucket", "s3tables:GetNamespace", "s3tables:GetTable",
                "s3tables:GetTableMetadataLocation", "s3tables:GetTableData"],
     "Resource": ["arn:aws:s3tables:<region>:<account-id>:bucket/event-platform-dev-archive",
                  "arn:aws:s3tables:<region>:<account-id>:bucket/event-platform-dev-archive/table/*"]
   }
   ```

4. **Optional read-only diff role** for pull requests. Same trust policy but with
   `"sub": "repo:<org>/<repo>:pull_request"`, and a permissions policy of the `sts:AssumeRole`
   statement above restricted to the `cdk-hnb659fds-lookup-role-*` role plus the SSM statement.

## 3. GitHub environments and variables

Create a GitHub environment called `dev` (Settings → Environments). Set these **environment
variables** (not secrets; ARNs and regions are not sensitive):

| Variable | Example |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::123456789012:role/event-platform-dev-deploy` |
| `AWS_REGION` | `eu-west-1` |
| `OWNER` (optional) | value of the `owner` tag, defaults to `platform-team` |

Repository-level variables (optional, for the PR diff job):

| Variable | Example |
|---|---|
| `AWS_DIFF_ROLE_ARN` | `arn:aws:iam::123456789012:role/event-platform-pr-diff` |
| `AWS_REGION` | `eu-west-1` |

### Adding staging and prod later

1. Create the GitHub environment (`staging`, `prod`) with **required reviewers** and, if wanted,
   a deployment branch rule limited to `main`.
2. Set its `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION` variables and create the matching IAM role
   with `environment:<name>` in the trust policy.
3. Add the environment to `cdk.json` under `context.environments` (copy `dev`).
4. Uncomment/copy the `deploy-staging` job in `.github/workflows/deploy.yml`. The reusable
   workflow, the Taskfile and the CDK app do not change.

The `concurrency` group on each deploy job (`deploy-<env>`, `cancel-in-progress: false`) makes
GitHub queue a second push to `main` behind the running deploy instead of running both.

## 4. What the deploy does

`task deploy ENV=dev` runs `cdk deploy --all` with `-c env=dev`. The account and region come
from the assumed role and `AWS_REGION`; nothing is hardcoded. The archive writer image is built
on the runner and pushed to the CDK bootstrap ECR repository. `task smoke ENV=dev` then sends one
synthetic `payment.succeeded` (id prefix `evt_synthetic_`, `synthetic: true`) and waits until it
appears in `ingress_events` and both of its domain messages appear in `domain_events`.
