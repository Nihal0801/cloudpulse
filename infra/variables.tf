variable "aws_region" {
  description = "AWS region for all CloudPulse resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Resource prefix. Use a different name for a separate environment."
  type        = string
  default     = "cloudpulse"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,29}$", var.project_name))
    error_message = "Use 3 to 30 lowercase letters, numbers or hyphens, starting with a letter."
  }
}

variable "alert_email" {
  description = "Optional alert email. Confirm the SNS subscription after deployment."
  type        = string
  default     = ""
  validation {
    condition     = var.alert_email == "" || can(regex("^[^@ ]+@[^@ ]+\\.[^@ ]+$", var.alert_email))
    error_message = "Provide an email address or leave this empty."
  }
}

variable "targets" {
  description = "Public HTTPS endpoints you own or have permission to monitor. Never include tokens in URLs."
  type = list(object({
    id   = string
    name = string
    url  = string
  }))
  validation {
    condition = (
      length(var.targets) >= 1 && length(var.targets) <= 10 &&
      length(distinct([for t in var.targets : t.id])) == length(var.targets) &&
      length(base64encode(jsonencode(var.targets))) <= 3332 &&
      alltrue([for t in var.targets :
        can(regex("^[a-z0-9-]{1,40}$", t.id)) && length(t.name) >= 1 && length(t.name) <= 80 &&
        can(regex("^https://[^/@?# :]+(:443)?(/[^# ]*)?$", t.url))
      ])
    )
    error_message = "Use 1–10 unique targets, valid IDs, names up to 80 characters, HTTPS URLs on port 443 and configuration under 2500 characters."
  }
}
