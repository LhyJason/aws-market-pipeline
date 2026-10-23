# Integration tests (phase 4)

Run against a deployed dev stack, marked `@pytest.mark.integration` and
skipped unless `RUN_INTEGRATION=1`. They invoke the producer once, read the
stream with a `TRIM_HORIZON` iterator, and assert records arrive with the
expected schema; later they also hit the API endpoint.
