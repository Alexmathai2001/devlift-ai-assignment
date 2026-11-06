variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-north-1"
}

variable "name" {
  description = "Base name for resources; also the ECR repository name"
  type        = string
  default     = "health-api"
}

variable "image_tag" {
  description = "Image tag in ECR to deploy"
  type        = string
  default     = "v1"
}

variable "container_port" {
  description = "Port the app listens on (must match server.port in the app)"
  type        = number
  default     = 8080
}

variable "task_cpu" {
  description = "Fargate task CPU units (512 = 0.5 vCPU)"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory in MiB"
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of tasks to keep running"
  type        = number
  default     = 1
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "log_retention_days" {
  description = "CloudWatch log retention"
  type        = number
  default     = 7
}
