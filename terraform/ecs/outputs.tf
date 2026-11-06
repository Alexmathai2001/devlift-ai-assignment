output "alb_url" {
  description = "Public URL of the load balancer"
  value       = "http://${aws_lb.main.dns_name}"
}

output "health_url" {
  description = "Health endpoint through the load balancer"
  value       = "http://${aws_lb.main.dns_name}/health"
}

output "image" {
  description = "Image deployed by the task definition"
  value       = local.image
}

output "log_group" {
  description = "CloudWatch log group for the container"
  value       = aws_cloudwatch_log_group.app.name
}
