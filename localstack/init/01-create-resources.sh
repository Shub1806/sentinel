#!/bin/bash
# Runs automatically when LocalStack is ready (mounted into /etc/localstack/init/ready.d).
# Creates the AWS resources Sentinel needs for local development.
# Later, Terraform will create these same resources in Kubernetes.
set -euo pipefail

awslocal s3 mb s3://sentinel-receipts
echo "Sentinel: created S3 bucket sentinel-receipts"
