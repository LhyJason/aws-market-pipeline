.PHONY: install lint fmt test tf-fmt tf-validate bootstrap plan apply destroy clean

PY  ?= python
ENV ?= dev
TF_DIR := infra/envs/$(ENV)

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

## ---- Terraform ------------------------------------------------------------

tf-fmt:           ## check HCL formatting
	terraform fmt -recursive -check infra

tf-validate:      ## validate every root module without touching a backend
	cd infra/bootstrap && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd $(TF_DIR) && terraform init -backend=false -input=false >/dev/null && terraform validate

bootstrap:        ## one-time: state bucket + budget (local state)
	cd infra/bootstrap && terraform init -input=false && terraform apply

plan:             ## terraform plan for ENV (default dev)
	cd $(TF_DIR) && terraform init -backend-config=backend.hcl -input=false && terraform plan

apply:            ## terraform apply for ENV (default dev)
	cd $(TF_DIR) && terraform init -backend-config=backend.hcl -input=false && terraform apply

destroy:          ## tear down ENV (default dev)
	cd $(TF_DIR) && terraform destroy

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov infra/modules/*/.build
