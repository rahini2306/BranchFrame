# Deploying the cloud demo

The included Terraform describes a restricted demonstration deployment. Its Terraform configuration is validated by GitHub Actions; local execution also checks formatting. No AWS resources were created during local development. Account-specific IAM, DNS, service quotas, networking, and operational behavior still require a real deployment test.

## Resources

- Private, encrypted, versioned S3 media bucket with public access blocked and TLS required.
- Immutable-tag ECR repository.
- One EC2 `t3.small` API/coordinator host with encrypted 30 GiB EBS-backed metadata storage.
- HTTPS Application Load Balancer with an existing ACM certificate; HTTP redirects to HTTPS.
- ECS Fargate rendering service with configurable worker count, task IAM roles, and private API connectivity.
- CloudWatch logs with seven-day retention.

The default VPC must exist and have public subnets in at least two availability zones. Workers have public IPs for outbound ECR/S3 access, but their security group has no inbound rules. The API accepts application traffic only from the load balancer and workers; no SSH port is opened. SSM is used for host administration.

This is a paid, continuously running architecture. EC2, Fargate, ALB, public IPv4, EBS, S3 storage/version history, requests, and data transfer can incur charges. Use a short demo window, one worker, short clips, an AWS Budget alert, and the official AWS Pricing Calculator for your chosen region. It is not guaranteed free-tier eligible.

## Prerequisites

1. AWS account access through the AWS CLI (prefer an SSO session), Docker, and Terraform 1.9+.
2. A domain you control and a validated ACM certificate in the deployment region.
3. An existing SSM SecureString parameter `/branchframe/worker-token` containing a long random secret. Create it through the AWS console or your secret-management process with the default AWS SSM KMS key. Do not put its value in source, Terraform variables, user data, or GitHub variables.
4. Copy `terraform.tfvars.example` to `terraform.tfvars` and replace every placeholder. `allowed_cidr` should be your public IP with `/32` for a restricted demo. The input expects IPv4.

```sh
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
```

Terraform state and variable files are ignored by Git. Keep state private; use an encrypted, access-controlled remote state backend for team use.

## Build and publish the first image

The image must exist before the API and workers can start. Bootstrap only ECR first, then publish, then create the rest. The ECR repository name is `branchframe`; use a separate AWS account/region or adjust the name if it already exists.

```sh
terraform apply -target=aws_ecr_repository.app
terraform output -raw repository_url
```

Set `image_tag` in your local `.tfvars` to your source commit SHA, then build from the repository root. Replace the sample account/region/repository values with the output from your account:

```sh
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com
docker buildx build --platform linux/amd64 -t ACCOUNT_ID.dkr.ecr.ap-south-1.amazonaws.com/branchframe:COMMIT_SHA --push .
```

Now run `terraform plan` and review the concrete resources and costs before `terraform apply`. `-target` is only for this bootstrap dependency, not normal ongoing infrastructure management.

Point your certificate's domain to the load balancer DNS output. Use that domain over HTTPS rather than visiting the load balancer hostname with a mismatched certificate.

## GitHub Actions image publishing

The optional **Publish AWS image** workflow publishes `branchframe:<source commit SHA>` to ECR; it does not create infrastructure or restart the API.

Create a GitHub environment named `aws`, with repository/environment variables:

- `AWS_DEPLOY_ROLE_ARN`: an existing narrowly scoped AWS role trusted through GitHub OIDC.
- `AWS_REGION`: the ECR region.

The role needs `ecr:GetAuthorizationToken` plus layer upload, image publish, and image-read permissions restricted to this repository. Configure its OIDC trust to the **actual subject claim** for this repository and environment; newer GitHub repositories can use immutable IDs in that claim. Follow the official [GitHub OIDC guide](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws). Do not put permanent AWS access keys into the repository.

## Updating the API image

EC2 user data runs at first boot. Changing `image_tag` updates the ECS worker definition but does not rerun the existing API host's bootstrap. Through AWS Systems Manager, update both image references in `/etc/systemd/system/branchframe.service`, then run `systemctl daemon-reload` and `systemctl restart branchframe`. The service pulls the image using the instance role before starting. Back up metadata first and retain the prior image tag for rollback.

This deliberate manual API rollout avoids replacing a host and silently losing its active metadata database. A future release can automate an SSM rollout with health checks and rollback.

## Backups and restoration

S3 stores media; it does not automatically back up the metadata database. The API's `/srv/branchframe/data/metadata.sqlite3` contains accounts, branch history, and queue state.

Use SQLite's online backup API or pause the app and take an EBS snapshot. Do not copy only a live SQLite file while ignoring its WAL. Record the image tag and worker secret parameter ARN alongside recovery instructions.

The API root volume has `delete_on_termination=false`. Instance replacement or Terraform destroy retains that volume, but a replacement instance does not automatically reattach or restore it. Restore the metadata into its data directory before reopening access. A retained volume is a recovery aid, not automatic failover.

## Verify a deployment

- HTTPS login works and the cookie has `Secure` and `HttpOnly` flags.
- An uninvited account cannot load another project's API or media.
- Upload, commit, branch, merge, export, and playback all succeed.
- With two workers and several clips, different tasks can execute simultaneously.
- A worker stopped mid-job is replaced, its lease expires, and another worker reclaims the segment.
- Repeating an export reuses cached segments.
- CloudWatch receives API and worker logs without tokens or credentials.
- Restore a metadata backup before describing recovery as tested.

## Cleanup

Take backups first, then review `terraform destroy`. It removes the compute/load-balancer resources but intentionally cannot delete a nonempty media bucket or an ECR repository with images by default. Review and delete unwanted objects, object versions, delete markers, and images through normal AWS controls only when they are no longer needed. The retained EBS root volume, SSM secret, DNS records, certificate, and backups need separate review; retained storage can continue to incur charges.

Do not use blanket bucket deletion or `force_destroy=true` as a shortcut for a repository containing real footage.
