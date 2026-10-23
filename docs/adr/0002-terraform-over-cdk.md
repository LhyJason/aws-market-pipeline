# ADR 0002: Terraform for infrastructure, not CDK or SAM

**Status:** accepted · **Date:** 2026-10-23

## Context

Options considered: Terraform, AWS CDK (Python), AWS SAM, raw CloudFormation.
The application code is Python, so CDK in Python would keep one language.

## Decision

Terraform, with reusable modules under `infra/modules` and one root module per
environment under `infra/envs`.

Reasons:

- Terraform is the most widely requested IaC tool in cloud and platform job
  postings and is not tied to AWS.
- Declarative HCL plans are easy to review in a pull request; CDK synth output
  is verbose CloudFormation.
- Remote state in S3 with native lockfile support (Terraform >= 1.10) removes
  the need for a DynamoDB lock table.

## Consequences

- Lambda packaging is done with the `archive` provider from the service's
  `src` directory. Services with third-party dependencies will need a build
  step that vendors them into the zip (phase 4 for the API).
- Two languages in the repo (Python, HCL). Acceptable.
