"""Fixtures shared by the processor tests: a mocked market table and lake bucket."""

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "market"
LAKE_BUCKET = "lake"


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def market_table(aws):
    """Same key schema as the Terraform table: PK symbol, SK sk, both strings."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "symbol", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "symbol", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def s3(aws):
    return boto3.client("s3", region_name="us-east-1")


@pytest.fixture
def lake_bucket(s3):
    s3.create_bucket(Bucket=LAKE_BUCKET)
    return LAKE_BUCKET
