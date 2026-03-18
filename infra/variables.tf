###############################
#          Variables          #
###############################
variable "app_name" {
  description = "Application name"
  type        = string
}

variable "agent_runtime_version" {
  description = "Runtime version for PROD endpoint"
  type        = string
  default     = "1"
}

# EKS configuration
variable "eks_cluster_name" {
  description = "EKS cluster name for Kubernetes Lambda"
  type        = string
}

# Prometheus configuration
variable "prometheus_url" {
  description = "Prometheus server URL (use NodePort for Lambda access)"
  type        = string
}

variable "prometheus_vpc_config" {
  description = "VPC configuration for Lambda to access Prometheus in EKS"
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}