.PHONY: install lint fmt test test-integration test-chaos load load-sweep load-report build tf-fmt tf-validate bootstrap plan apply destroy local-up local-down demo diagram clean

PY  ?= python
ENV ?= dev
TF_DIR := infra/envs/$(ENV)
# arm64 for AWS; LocalStack runs Lambdas on the Docker host's architecture.
ARCH ?= arm64
LOCAL_ARCH ?= x86_64
# Load test: ticks per second, seconds per run, symbols to spread over, and the sweep's rates.
RATE ?= 50
DURATION ?= 60
SYMBOLS ?= 5
RATES ?= 10 50 100 200
# D2 0.9.0 renders the architecture diagram; CI pins the same version.
D2 ?= d2

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

test-integration: ## end-to-end against a deployed stack: TF_ENV_DIR, default infra/envs/local (LocalStack)
	RUN_INTEGRATION=1 pytest -m "integration and not chaos" tests/integration -v

test-chaos:       ## inject failures into the LocalStack stack and check it copes (restores everything)
	RUN_INTEGRATION=1 pytest -m chaos tests/integration -v

load:             ## push RATE ticks/s for DURATION s onto the stream (TF_ENV_DIR, default LocalStack) and report
	$(PY) scripts/load_test.py --rate $(RATE) --duration $(DURATION) --symbols $(SYMBOLS)

load-sweep:       ## the load test at each of RATES
	for rate in $(RATES); do 	  $(PY) scripts/load_test.py --rate $$rate --duration $(DURATION) --symbols $(SYMBOLS) || exit 1; 	done

load-report:      ## re-report a saved run (RUN=<id>), or the last MINUTES (default 60) of processor logs
	$(PY) scripts/load_report.py $(if $(RUN),$(RUN),--minutes $(or $(MINUTES),60))

build:            ## package the Lambdas with native wheels (DuckDB, pydantic-core) for ARCH into .build/
	$(PY) scripts/build_lambda.py processor --arch $(ARCH)
	$(PY) scripts/build_lambda.py api --arch $(ARCH)

## ---- Terraform ------------------------------------------------------------

tf-fmt:           ## check HCL formatting
	terraform fmt -recursive -check infra

tf-validate:      ## validate every root module without touching a backend
	cd infra/bootstrap && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd infra/envs/dev && terraform init -backend=false -input=false >/dev/null && terraform validate
	cd infra/envs/prod && terraform init -backend=false -input=false >/dev/null && terraform validate
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
	$(PY) scripts/build_lambda.py api --arch $(LOCAL_ARCH)
	cd infra/envs/local && docker compose up -d && terraform init -input=false && terraform apply

local-down:       ## stop LocalStack and drop the local state it no longer matches
	cd infra/envs/local && docker compose down && rm -f terraform.tfstate terraform.tfstate.backup

demo:             ## the whole pipeline on LocalStack, from build to teardown, timed (scripts/demo.py)
	$(PY) scripts/demo.py

## ---- Docs -----------------------------------------------------------------

diagram:          ## render docs/diagrams/architecture.d2 to docs/architecture.svg
	$(D2) docs/diagrams/architecture.d2 docs/architecture.svg

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov infra/modules/*/.build
