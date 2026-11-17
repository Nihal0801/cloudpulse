mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/cloudpulse-test" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:cloudpulse-alerts" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:cloudpulse-failed-checks" }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/cloudpulse" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/cloudpulse-test" }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:us-east-1:123456789012:function:cloudpulse-test" }
  }
  mock_resource "aws_scheduler_schedule_group" {
    defaults = { arn = "arn:aws:scheduler:us-east-1:123456789012:schedule-group/cloudpulse" }
  }
}

# AWS resources are mocked. This apply does not contact AWS.
run "small_monitoring_stack" {
  command = apply
  variables {
    targets = [{ id = "api", name = "My API", url = "https://example.com/" }]
  }
  assert {
    condition     = aws_dynamodb_table.monitor.billing_mode == "PAY_PER_REQUEST"
    error_message = "Use on-demand DynamoDB billing."
  }
  assert {
    condition     = aws_lambda_function.app["checker"].reserved_concurrent_executions == 1
    error_message = "Serialize checker executions to protect incident state."
  }
  assert {
    condition     = aws_scheduler_schedule.monitor.schedule_expression == "rate(1 minute)"
    error_message = "The monitor should run once a minute."
  }
  assert {
    condition     = !contains(jsondecode(aws_iam_role_policy.function["api"].policy).Statement[1].Action, "dynamodb:PutItem")
    error_message = "The public API must not write monitoring data."
  }
  assert {
    condition     = aws_lambda_permission.url_invoke.invoked_via_function_url
    error_message = "Public invocation must be restricted to the dashboard URL."
  }
}

run "invalid_targets_are_rejected" {
  command = plan
  variables {
    targets = [{ id = "Bad ID", name = "My API", url = "http://example.com/" }]
  }
  expect_failures = [var.targets]
}
