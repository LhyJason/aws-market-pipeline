# ADR 0007: GitHub OIDC with per-environment roles and a permissions boundary

**Status:** accepted · **Date:** 2026-10-23

## Context

Phase 1's `deploy.yml` assumed an AWS role through GitHub's OIDC token, but
the role didn't exist, and the workflow built only the processor package, so
a dev deploy would have failed on the missing API package. Phase 5 has to
create the identity provider and roles, plan on pull requests, deploy dev and
add prod. It must store no long-lived AWS keys in GitHub, and grant no
wildcard actions on resources the project owns.

A Terraform deploy role is powerful by nature: it creates the IAM roles the
workloads run as. Any role allowed to call `iam:CreateRole` and
`iam:PutRolePolicy` freely can make itself an administrator.

Options considered:

| Option | Why not |
| --- | --- |
| Access keys in GitHub secrets | long-lived, must be rotated, and leak with the repository's secrets |
| One deploy role with `AdministratorAccess`, or `service:*` actions | simple, but a compromised workflow owns the account |
| One role shared by dev, prod and pull requests | a pull request, or a dev deploy, could change prod |
| **Per-purpose roles, named actions, name and tag scoping, a permissions boundary** | more policy to maintain; chosen |

## Decision

All of this lives in `infra/bootstrap`, which is applied once by hand:

- **The GitHub OIDC identity provider**, with audience `sts.amazonaws.com`
  and no thumbprint, since AWS validates GitHub's certificate itself.
- **Three roles:**

  | Role | Trusted subject | Access |
  | --- | --- | --- |
  | `<project>-github-plan` | `repo:<owner>/<name>:pull_request` | Read-only for every environment; plans with `-lock=false` |
  | `<project>-github-deploy-dev` | `…:environment:dev` | Create, update and delete dev's resources |
  | `<project>-github-deploy-prod` | `…:environment:prod` | The same for prod |

- **Named actions only.** Each service's read and write actions are listed
  once (`local.services`). The plan role gets the read half; the deploy role
  gets both, scoped to `<project>-<env>-*` ARNs. API Gateway's IDs are
  random, so it is scoped by the `Environment` tag instead: `aws:RequestTag`
  on create, `aws:ResourceTag` after that. `"*"` is used only for
  `cloudwatch:DescribeAlarms` and `logs:DescribeLogGroups`, which take no
  ARN, and for `lambda:CreateEventSourceMapping`, which is limited by
  `lambda:FunctionArn` instead.
- **A permissions boundary per environment.** The deploy role may call
  `CreateRole`, `PutRolePolicy`, `DeleteRolePolicy` and
  `PutRolePermissionsBoundary` only when the role carries that environment's
  boundary (`iam:PermissionsBoundary`). It has no permission to remove a
  boundary or to edit policies. `PassRole` is limited to Lambda, Firehose and
  API Gateway. Every module takes `permissions_boundary` and sets it on its
  roles.
- **Policy limits.** Each deploy role has two customer-managed policies:
  together they exceed the 10,240-character inline limit, and each stays
  under the 6,144-character managed limit.
- **The workflow.** `deploy.yml` builds both Lambda packages for arm64 through
  a composite action. It plans dev and prod on pull requests, applies dev on
  main with an API smoke test, and applies prod only on a manual run (ADR
  0008).

## Consequences

- **No AWS keys in GitHub.** A workflow on another repository, a fork's pull
  request, or a job outside the named environment can't assume any role.
- **Containment.** A compromised dev deploy can't touch prod, can't read
  other buckets or tables, and can't escalate. Any role it creates stays
  inside the dev boundary, which is limited to dev's stream, table, bucket,
  queue and log groups.
- **The plan role can read the state.** The state holds the API keys, which
  is accepted because only collaborators can open pull requests that get the
  role.
- **Maintenance.** New resource types need their actions added to
  `local.services`. A missing action shows up as an `AccessDenied` on the
  first deploy, not in `terraform validate`.
- **Not proven yet.** LocalStack doesn't enforce IAM, so the action lists and
  the tag conditions (including the assumption that a REST API's stages and
  methods are authorized against the API's tags) are unverified until the
  first real deploy. Running IAM Access Analyzer's policy validation first is
  the cheap check.
- **The environment subject may not arrive.** If GitHub doesn't send
  `environment:<env>` subjects for this private repository,
  `deploy_subjects` can trust `ref:refs/heads/main` instead, at the cost of
  any job on main being able to assume either role.
