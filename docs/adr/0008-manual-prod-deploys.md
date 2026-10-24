# ADR 0008: Prod deploys only by a manual workflow run

**Status:** accepted · **Date:** 2026-10-23

## Context

Prod must not change on every merge: a person should decide when it does.
GitHub's usual tool is an environment with required reviewers, where the job
waits for an approval. The repository is private on GitHub Free, and
deployment protection rules such as required reviewers aren't available for
private repositories on that plan.

Options considered:

| Option | Cost | Gate |
| --- | --- | --- |
| Environment with required reviewers | public repository, or GitHub Pro ($4/month) | an approval in GitHub's UI after the job is queued |
| Apply prod on push to main after dev | free | none: every merge reaches prod |
| Saved plan artifact, then a later approved apply | free | a person, but the plan file holds the API key and would be readable by anyone with repository access |
| **`workflow_dispatch` only, from main** | free | starting the run is the decision; only people with write access can |

## Decision

- **No push trigger.** The prod job runs only on `workflow_dispatch` with
  `environment = prod`, and only when the run is on `refs/heads/main`. It
  plans and applies in one job, so no plan file is uploaded.
- **The job still declares `environment: prod`.** Its OIDC subject is
  `environment:prod`, which the prod deploy role trusts (ADR 0007), and
  required reviewers can be added in the repository settings later with no
  change to the workflow.
- **Dev goes first.** A push to main applies dev and smoke-tests its API.
  Prod is meant to be run only after dev has deployed the same commit.
- **Pull requests show the prod plan** next to dev's, from the read-only plan
  role, so the change is reviewed before it merges.

## Consequences

- **Who can deploy prod.** Anyone with write access can, by starting the
  workflow. That fits a one-person repository; it is weaker than a second
  person's approval.
- **The dev-first rule is a convention.** Nothing forces the manual run to
  follow a green dev deploy. The pull request's prod plan is the review of
  what will change.
- **Upgrading.** Making the repository public, or moving to GitHub Pro or
  Team, allows required reviewers on the `prod` environment. At that point
  prod could also follow dev automatically on push, with the approval as the
  gate.
- **Nothing runs yet.** The job stays skipped until `AWS_PROD_DEPLOY_ROLE_ARN`
  is set, so none of this costs anything until prod is wanted.
