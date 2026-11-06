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
      Project   = var.repo_name
      ManagedBy = "terraform"
    }
  }
}

resource "aws_ecr_repository" "app" {
  name                 = var.repo_name
  image_tag_mutability = "IMMUTABLE" # a tag like v1 can never be overwritten
  force_delete         = true        # lets `terraform destroy` remove a non-empty repo (assignment only)

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Keep only the most recent images to limit storage cost
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last ${var.max_images} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.max_images
      }
      action = { type = "expire" }
    }]
  })
}
