# processor (phase 3)

Kinesis consumer Lambda. Reads batches of tick records, builds 1-minute OHLCV
bars and rolling statistics per symbol, writes the latest state to DynamoDB
and Parquet partitions to S3.
