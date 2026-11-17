output "dashboard_url" {
  description = "Public read-only dashboard. It exposes configured endpoint URLs and status history."
  value       = aws_lambda_function_url.dashboard.function_url
}

output "checker_function" {
  value = aws_lambda_function.app["checker"].function_name
}

output "table_name" {
  value = aws_dynamodb_table.monitor.name
}

output "failed_checks_queue_url" {
  value = aws_sqs_queue.failed_checks.url
}
