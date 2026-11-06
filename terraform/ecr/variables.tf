variable "aws_region" {
  description = "AWS region for the repository"
  type        = string
  default     = "eu-north-1"
}

variable "repo_name" {
  description = "ECR repository name"
  type        = string
  default     = "health-api"
}

variable "max_images" {
  description = "Number of images to retain"
  type        = number
  default     = 5
}
