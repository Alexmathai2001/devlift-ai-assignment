terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.name
      ManagedBy = "terraform"
    }
  }
}

# Look up (not create) the ECR repository made by terraform/ecr
data "aws_ecr_repository" "app" {
  name = var.name
}

# Availability Zones in this region (we use the first two)
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  image = "${data.aws_ecr_repository.app.repository_url}:${var.image_tag}"
  azs   = slice(data.aws_availability_zones.available.names, 0, 2)
}
