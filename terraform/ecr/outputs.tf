output "repository_url" {
  description = "Full repository URL, used when tagging and pushing images"
  value       = aws_ecr_repository.app.repository_url
}

output "registry" {
  description = "Registry host, used for docker login"
  value       = split("/", aws_ecr_repository.app.repository_url)[0]
}
