#!/usr/bin/env bash
# Tears down the EKS cluster between sessions (Phase D).
#
# The WireGuard tunnel-server now lives INSIDE the EKS VPC (see
# PHASE_D_RUNBOOK.md's "Why the tunnel-server lives inside the EKS VPC" —
# VPC peering can't do edge-to-edge routing through a NAT instance, so the
# earlier peered-VPC design doesn't work), which means this deletes the
# tunnel-server too. BEFORE running this, save the values the runbook's
# Step 2 calls out: both WireGuard private keys, both public keys, and the
# Elastic IP's AllocationId — otherwise you're regenerating everything next
# session instead of just relaunching and pasting the same keys back in.
#
# Usage: AWS_REGION=us-east-1 EKS_CLUSTER_NAME=codereview ./teardown.sh
set -euo pipefail

: "${AWS_REGION:?set AWS_REGION}"
: "${EKS_CLUSTER_NAME:?set EKS_CLUSTER_NAME}"

echo "Deleting EKS cluster $EKS_CLUSTER_NAME (this takes a while, and also removes the tunnel-server instance)..."
eksctl delete cluster --name "$EKS_CLUSTER_NAME" --region "$AWS_REGION"

echo "Done. ECR repo is still there (storage-only cost, no compute)."
echo "Next session: re-run PHASE_D_RUNBOOK.md from Step 1 (cluster create) onward,"
echo "then Step 2 (tunnel-server) using the saved WireGuard keys + Elastic IP."
