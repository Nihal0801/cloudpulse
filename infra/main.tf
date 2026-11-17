data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  functions = { checker = "cloudpulse.handlers.checker", api = "cloudpulse.handlers.api" }
  config    = { TABLE_NAME = aws_dynamodb_table.monitor.name, TARGETS_JSON = jsonencode(var.targets) }
}

data "archive_file" "app" {
  type        = "zip"
  output_path = "${path.module}/cloudpulse.zip"
  dynamic "source" {
    for_each = setunion(fileset("${path.module}/..", "cloudpulse/*.py"), fileset("${path.module}/..", "web/*"))
    content {
      content  = file("${path.module}/../${source.value}")
      filename = source.value
    }
  }
}

resource "aws_dynamodb_table" "monitor" {
  name         = var.project_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_sqs_queue" "failed_checks" {
  name                      = "${var.project_name}-failed-checks"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_cloudwatch_log_group" "function" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.project_name}-${each.key}"
  retention_in_days = 7
}

resource "aws_iam_role" "function" {
  for_each = local.functions
  name     = "${var.project_name}-${each.key}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions
  role     = aws_iam_role.function[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.function[each.key].arn}:*" },
      { Effect = "Allow", Action = each.key == "checker" ? ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:DeleteItem"] : ["dynamodb:GetItem", "dynamodb:Query"], Resource = aws_dynamodb_table.monitor.arn }
      ], each.key == "checker" ? [
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.alerts.arn },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failed_checks.arn }
    ] : [])
  })
}

resource "aws_lambda_function" "app" {
  for_each                       = local.functions
  function_name                  = "${var.project_name}-${each.key}"
  role                           = aws_iam_role.function[each.key].arn
  handler                        = each.value
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  filename                       = data.archive_file.app.output_path
  source_code_hash               = data.archive_file.app.output_base64sha256
  timeout                        = each.key == "checker" ? 55 : 15
  memory_size                    = 128
  reserved_concurrent_executions = each.key == "checker" ? 1 : 2
  environment {
    variables = each.key == "checker" ? merge(local.config, { TOPIC_ARN = aws_sns_topic.alerts.arn }) : local.config
  }
  depends_on = [aws_iam_role_policy.function]
}

resource "aws_lambda_function_url" "dashboard" {
  function_name      = aws_lambda_function.app["api"].function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "url" {
  statement_id           = "PublicReadOnlyDashboardURL"
  function_name          = aws_lambda_function.app["api"].function_name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "url_invoke" {
  statement_id             = "PublicInvokeOnlyViaURL"
  function_name            = aws_lambda_function.app["api"].function_name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}

resource "aws_lambda_function_event_invoke_config" "checker" {
  function_name                = aws_lambda_function.app["checker"].function_name
  maximum_event_age_in_seconds = 120
  maximum_retry_attempts       = 1
  destination_config {
    on_failure {
      destination = aws_sqs_queue.failed_checks.arn
    }
  }
}

resource "aws_scheduler_schedule_group" "monitor" {
  name = var.project_name
}

resource "aws_iam_role" "scheduler" {
  name = "${var.project_name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }, ArnEquals = { "aws:SourceArn" = aws_scheduler_schedule_group.monitor.arn } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.app["checker"].arn },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failed_checks.arn }
    ]
  })
}

resource "aws_scheduler_schedule" "monitor" {
  name                = "${var.project_name}-minute"
  group_name          = aws_scheduler_schedule_group.monitor.name
  schedule_expression = "rate(1 minute)"
  flexible_time_window {
    mode = "OFF"
  }
  target {
    arn      = aws_lambda_function.app["checker"].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ scheduled_at = "<aws.scheduler.scheduled-time>" })
    retry_policy {
      maximum_event_age_in_seconds = 120
      maximum_retry_attempts       = 1
    }
    dead_letter_config {
      arn = aws_sqs_queue.failed_checks.arn
    }
  }
  depends_on = [aws_iam_role_policy.scheduler, aws_lambda_function_event_invoke_config.checker]
}
