terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Same backend type everywhere. Bucket/key/region come from -backend-config (see Taskfile):
  # AWS uses the state bucket named by TF_STATE_BUCKET; tflocal redirects it to LocalStack S3.
  backend "s3" {}
}

provider "aws" {
  # Region and credentials come from the environment (AWS_REGION + ambient credentials or the
  # OIDC-assumed role in CI). tflocal injects LocalStack endpoints. Nothing is hardcoded here.
  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      owner       = var.owner
    }
  }
}
