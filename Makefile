.PHONY: install lint fmt test build tf-fmt tf-validate bootstrap plan apply destroy local-up local-down clean

PY  ?= python
ENV ?= dev
TF_DIR := infra/envs/$(ENV)
# arm64 for AWS; LocalStack runs Lambdas on the Docker host's architecture.
ARCH ?= arm64
LOCAL_ARCH ?= x86_64

## ---- Python ---------------------------------------------------------------

install:          ## install dev tooling into the active environment
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

lint:             ## ruff lint + format check
	ruff check .
	ruff format --check .

fmt:              ## auto-fix lint and format
	ruff check --fix .
	ruff format .

test:             ## unit tests with coverage (no AWS access needed)
	pytest --cov=services --cov-report=term-missing

build:            ## package the processor Lambda (DuckDB wheels for ARCH) into .build/
	$(PY) scripts/build_lambda.py processor --arch $(ARCH)

## ---- Terraform ------------------------------------------------------------

tf-fmt:           ## check HCL formatting
	terraform fmt -recursive -check infra

tf-validate:      ## validate every root module without touching a backend
	cd infra/bootstrap && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd $(TF_DIR) && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd infra/envs/local && terraform init -input=false >/dev/null && terraform validate

bootstrap:        ## one-time: state bucket + budget (local state)
	cd infra/bootstrap && terraform init -input=false && terraform apply

plan: build       ## terraform plan for ENV (default dev)
	cd $(TF_DIR) && terraform init -backend-config=backend.hcl -input=false && terraform plan

apply: build      ## terraform apply for ENV (default dev)
	cd $(TF_DIR) && terraform init -backend-config=backend.hcl -input=false && terraform apply

destroy:          ## tear down ENV (default dev)
	cd $(TF_DIR) && terraform destroy

## ---- LocalStack ($0) ---------------------------------------------------

local-up:         ## start LocalStack and apply infra/envs/local
	$(PY) scripts/build_lambda.py processor --arch $(LOCAL_ARCH)
	cd infra/envs/local && docker compose up -d && terraform init -input=false && terraform apply

local-down:       ## stop LocalStack and drop the local state it no longer matches
	cd infra/envs/local && docker compose down && rm -f terraform.tfstate terraform.tfstate.backup

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov infra/modules/*/.build
