# prod environment (phase 5)

Mirror of `../dev` with `environment = "prod"`, its own `backend.hcl` key
(`prod/terraform.tfstate`), a longer Kinesis retention, and deploys gated on a
GitHub environment approval. Not created until the dev stack is stable.
