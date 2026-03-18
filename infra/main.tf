# ECR repository
resource "aws_ecr_repository" "agentcore_terraform_runtime" {
    name = "bedrock-agentcore/${lower(var.app_name)}"
    image_tag_mutability = "MUTABLE"

    image_scanning_configuration {
      scan_on_push = true
    }

    encryption_configuration {
      encryption_type = "KMS"
    }
}

data "aws_ecr_authorization_token" "token" {}
locals {
  src_files = fileset("../${path.root}/src", "**")
  src_hashes = [
    for file in local.src_files:
    filesha256("../${path.root}/src/${file}")
  ]
  # merge all files hashes into one
  src_hash = sha256(join("", local.src_hashes))

  # content-based versioning system for your Docker images
  # This hash will be used as image tag (unique)
  # takes first 12 chars of hash + adds suffix -v3
  image_tag = "${substr(local.src_hash, 0, 12)}-v3"
}

resource "null_resource" "docker_image" {
    depends_on = [aws_ecr_repository.agentcore_terraform_runtime]
    triggers = {
      src_hash = local.src_hash
    }

    provisioner "local-exec" {
        interpreter = ["/bin/bash", "-c"]
        command = <<EOF
        source ~/.bash_profile || source ~/.profile || true
        if ! command -v docker &> /dev/null; then
          echo "Docker is not installed or not in PATH.
          exit 1
        fi
        aws ecr get-login-password | docker login --username AWS --password-stdin ${data.aws_ecr_authorization_token.token.proxy_endpoint}
        docker build --no-cache -t ${aws_ecr_repositoy.agentcore_terraform_runtime.repository_url}:${local.image_tag}
        EOF
    }
  
}

#######################
# Lambda function
#######################
# IAM Role for lambda
resource "aws_iam_role" "mcp_lambda_role" {
    name = "${var.app_name}-McpLambdaRole"
    assume_role_policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Action = "sts:AssumeRole"
            Effect = "Allow"
            Principal = {
                Service = "lambda.amazonaws.com"
            }
        }]
    })
}

resource "aws_iam_role_policy_attachment" "mcp_lambda_basic" {
    role = aws_iam_role.mcp_lambda_role
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSlambdaBasicExecutionRole"
}

data "archive_file" "mcp_lambda_zip" {
    type = "zip"
    source_dir = "../${path.root}/mcp/lambda"
    output_path = "../${path.root}/mcp_lambda.zip"
}

resource "aws_lambda_function" "mcp_lambda" {
    function_name = "${var.app_name}-McpLambda"
    role = aws_iam_role.mcp_lambda_role.arn
    handler = "handler.lambda_handler"
    runtime = "python3.12"
    timeout = 60
    memory_size = 256

    filename = data.archive_file.mcp_lambda_zip.output_path
    source_code_hash = data.archive_file.mcp_lambda_zip.output_base64sha256

    environment {
      variables = {
        EKS_CLUSTER_NAME = var.eks_cluster_name
      }
    }

    dynamic "vpc_config" {
        for_each = var.prometheus_vpc_config != null ? [var.prometheus_vpc_config] : []
        content {
          subnet_ids = vpc_config.value.subnet_ids
          security_group_ids = vpc_config.value.security_group_ids
        }
      
    }
}

# EKS access for lambda
resource "aws_iam_role_policy" "mcp_lambda_eks" {
    role = aws_iam_role.mcp_lambda_role.id
    policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Effect = "Allow"
            Action = [
                "eks:DescribeCluster",
                "eks:ListClusters"
            ]
            Resource = "*"
        }]
    })
}

# VPC access for k8s lambda
resource "aws_iam_role_policy_attachment" "mcp_lambda_vpc" {
    count = var.prometheus_vpc_config != null ? 1: 0
    role = aws_iam_role.mcp_lambda_role.name
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

###################################
# Cloudwatch MCP Lambda function
###################################

resource "aws_iam_role" "cloudwatch_lambda_role" {
    name = "${var.app_name}-CloudWatchLambdaRole"
    assume_role_policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Action = "sts:AssumeRole"
            Effect = "Allow"
            Principal = {
                Service = "lambda.amazonaws.com"
            }
        }]
    })
}

resource "aws_iam_role_policy_attachment" "cloudwatch_lambda_basic" {
    role = aws_iam_role.cloudwatch_lambda_role
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSlambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "cloudwatch_lambda_cw" {
    role = aws_iam_role.cloudwatch_lambda_role.id
    policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Effect = "Allow"
            Action = [
                "cloudwatch:GetMetricData",
                "cloudwatch:GetMetricStatistics",
                "cloudwatch:ListMetrics",
                "cloudwatch:DescribeAlarms",
                "cloudwatch:DescribeAlarmsForMetric"
            ]
            Resource = "*"
        },
        {
            Effect = "Allow"
            Action = [
                "logs:StartQuery",
                "logs:GetQueryResults",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
                "logs:GetLogEvents"
            ]
            Resource = "*"
        }
        
        ]
    })
}



data "archive_file" "cloudwatch_lambda_zip" {
    type = "zip"
    source_dir = "../${path.root}/mcp/cloudwatch-lambda"
    output_path = "../${path.root}/cloudwatch_lambda.zip"
}

resource "aws_lambda_function" "cloudwatch_lambda" {
    function_name = "${var.app_name}-CloudWatchLambda"
    role = aws_iam_role.cloudwatch_lambda_role.arn
    handler = "handler.lambda_handler"
    runtime = "python3.12"
    timeout = 60
    memory_size = 256

    filename = data.archive_file.cloudwatch_lambda_zip.output_path
    source_code_hash = data.archive_file.cloudwatch_lambda_zip.output_base64sha256

    environment {
      variables = {
        EKS_CLUSTER_NAME = var.eks_cluster_name
      }
    }

    dynamic "vpc_config" {
        for_each = var.prometheus_vpc_config != null ? [var.prometheus_vpc_config] : []
        content {
          subnet_ids = vpc_config.value.subnet_ids
          security_group_ids = vpc_config.value.security_group_ids
        }
      
    }
}

###################################################
# Prometheus MCP Lambda Function
###################################################
resource "aws_iam_role" "prometheus_lambda_role" {
    name = "${var.app_name}-PrometheusLambdaRole"
    assume_role_policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Action = "sts:AssumeRole"
            Effect = "Allow"
            Principal = {
                Service = "lambda.amazonaws.com"
            }
        }]
    })
}

resource "aws_iam_role_policy_attachment" "prometheus_lambda_basic" {
    role = aws_iam_role.prometheus_lambda_role
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSlambdaBasicExecutionRole"
}

# VPC access for k8s lambda
resource "aws_iam_role_policy_attachment" "prometheus_lambda_vpc" {
    count = var.prometheus_vpc_config != null ? 1: 0
    role = aws_iam_role.prometheus_lambda_role.name
    policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "archive_file" "prometheus_lambda_zip" {
    type = "zip"
    source_dir = "../${path.root}/mcp/prometheus-lambda"
    output_path = "../${path.root}/prometheus_lambda.zip"
}

resource "aws_lambda_function" "prometheus_lambda" {
    function_name = "${var.app_name}-PromethuesLambda"
    role = aws_iam_role.prometheus_lambda_role.arn
    handler = "handler.lambda_handler"
    runtime = "python3.12"
    timeout = 60
    memory_size = 256

    filename = data.archive_file.prometheus_lambda_zip.output_path
    source_code_hash = data.archive_file.prometheus_lambda_zip.output_base64sha256

    environment {
      variables = {
        PROMETHEUS_URL = var.prometheus_url
      }
    }

    dynamic "vpc_config" {
        for_each = var.prometheus_vpc_config != null ? [var.prometheus_vpc_config] : []
        content {
          subnet_ids = vpc_config.value.subnet_ids
          security_group_ids = vpc_config.value.security_group_ids
        }
      
    }
}

#######################################
# AgentCore Gateway Roles
######################################
data "aws_iam_policy_document" "bedrock_agentcore_assume_role" {
    statement {
      effect = "Allow"
      actions = ["sts:AssumeRole"]
      principals {
        type = "Service"
        identifiers = ["bedrock-agentcore.amazonaws.com"]
      }
    }
}

resource "aws_iam_role" "agentcore_gateway_role" {
    name = "${var.app_name}-AgentCoreGatewayRole"
    assume_role_policy = data.aws_ecr_authorization_token.token.json
}

resource "aws_iam_role_policy_attachment" "agentcore_gateway_permissions" {
    role = aws_iam_role.agentcore_gateway_role.name
    policy_arn = "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess"
  
}

resource "aws_iam_role_policy" "agentcore_gateway_lambda_invoke" {
    role = aws_iam_role.agentcore_gateway_role.id
    policy = jsondecode({
        Version = "2012-10-17"
        Statement = [{
            Effect = "Allow"
            Action = [
                "lambda:InvokeFunction"
            ]
            Resource = [
                aws_lambda_function.mcp_lambda.arn,
                aws_lambda_function.cloudwatch_lambda.arn,
                aws_lambda_function.prometheus_lambda.arn
            ]
        }]
    })
}

########################################################
# Agentcore gateway inboung auth- congnito
########################################################
resource "aws_cognito_user_pool" "cognito_user_pool" {
    name = "${var.app_name}-CognitoUserPool"
}

resource "aws_cognito_resource_server" "cognito_resource_server" {
    identifier = "${var.app_name}-CognitoResourceServer"
    name = "${var.app_name}-CognitoResourceServer"
    user_pool_id = aws_cognito_user_pool.cognito_user_pool.id
    scope {
      scope_description = "Basic access to ${var.app_name}"
      scope_name = "basic"
    }
  
}

resource "aws_cognito_user_pool_client" "cognito_app_client" {
    name = "${var.app_name}-CognitoUserPoolClient"
    user_pool_id = aws_cognito_user_pool.cognito_user_pool.id
    generate_secret = true
    allowed_oauth_flows = ["client_credentials"]
    allowed_oauth_flows_user_pool_client = true
    allowed_oauth_scopes = ["${aws_cognito_resource_server.cognito_resource_server.identifier}/basic"]
    supported_identity_providers = ["COGNITO"]
}

resource "aws_cognito_user_pool_domain" "cognito_domain" {
    domain = "${lower(var.app_name)}-${data.aws_region.current.region}"
    user_pool_id = aws_cognito_user_pool.cognito_user_pool.id
}

locals {
  cognito_discovery_url = "https://cognito-idp.${data.aws_region.current.region}.amazonaws.com/${aws_cognito_user_pool.cognito_user_pool.id}/.well-known/openid-configuration"
}

###############################################################
# AgentCore Gateway
###############################################################
resource "aws_bedrockagentcore_gateway" "agentcore_gateway" {
  name = "${var.app_name}-Gateway"
  protocol_type = "MCP"
  role_arn = aws_iam_role.agentcore_gateway_role.arn
  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url = local.cognito_discovery_url
      allowed_clients = [aws_cognito_user_pool_client.cognito_app_client.id]
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "agentcore_gateway_lambda_target" {
  name = "${var.app_name}-KubernetesTools"
  gateway_identifier = aws_bedrockagentcore_gateway.agentcore_gateway.gateway_id

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.mcp_lambda

        # Tool: routes to all k8s and runbook operations
        tool_schema {
          inline_payload {
            name = "kubernetes_sre_tool"
            description = "Kubernetes SRE took for investingating and healing pod issues. supports: get_pods, get_pod_logs, describe_pod, get_events, restart_deployment, search_runbook, get_solution, suggest_fix"
            input_schema {
              type = "object"
              description = "Input for kubernetes_sre_tool"
              property {
                name = "tool_name"
                type = "string"
                description = "Too to execute: get_pods, get_pod_logs, describe_pod, get_events, restart_deployment, search_runbook, get_soltion, suggest_fix"
              }
              property {
                name = "namespace"
                type = "string"
                description = "K8S namespace (default: default)"
              }
              property {
                name = "pod_name"
                type = "string"
                description = "Name of the pod"
              }
              property {
                name = "deployment_type"
                type = "string"
                description = "Name of the deployment"
              }
              property {
                name = "previous"
                type = "boolean"
                description = "Get logs from previous container"
              }
              property {
                name = "query"
                type = "string"
                description = "Search query for runbook search"
              }
              property {
                name = "runbook_id"
                type = "string"
                description = "Runbook ID (for get_solution)"
              }
              property {
                name = "error_message"
                type = "string"
                description = "Error message (for suggest_fix)"
              }

            }
          }
        }
      }
    }
  }
}

############################################
# Cloudwatch target
############################################
resource "aws_bedrockagentcore_gateway_target" "agentcore_gateway_cloudwatch_target" {
    name = "${var.app_name}-CloudWatchTools"
    gateway_identifier = aws_bedrockagentcore_gateway.agentcore_gateway.gateway_id

    credential_provider_configuration {
      gateway_iam_role {}
    }

    target_configuration {
      mcp {
        lambda {
          lambda_arn = aws_lambda_function.cloudwatch_lambda.arn
          tool_schema {
            inline_payload {
              name = "cloudwatch_tool"
              description = "Cloudwatch monitoring tool for EKS metrics, alarms and logs, Supports: get_eks_metrics, get_node_metrics, get_pod_metrics, get_alarms, query_logs, get_cluster_health"
              input_schema {
                type = "object"
                description = "Input for cloudwatch_tool"
                property {
                  name = "tool_name"
                  type = "string"
                  description = "Tool to execute"
                }
                property {
                  name = "period"
                  type = "string"
                  description = "Time period for metrics: 5m, 1h, 24h (default: 5m)"
                }
                property {
                  name = "node_name"
                  type = "string"
                  description = "Specific node name for get_node_metrics"
                }
                property {
                  name = "namespace"
                  type = "string"
                  description = "K8s namespace for get_pod_metrics (default: default)"
                }
                property {
                  name = "pod_name"
                  type = "string"
                  description = "Specific pod name"
                }
                property {
                  name = "state"
                  type = "string"
                  description = "Filter alarms by state: ALARM, OK, INSUFFICIENT_DATA"
                }
                property {
                  name = "query_type"
                  type = "string"
                  description = "Log query type: errors, warning, all (default: errors)"
                }
                property {
                  name = "time_range"
                  type = "string"
                  description = "Time range for logs: 1h, 6h, 24h (default:1h)"
                }
                property {
                  name = "limit"
                  type = "number"
                  description = "Maximum number of log results (default: 20)"
                }
              }
            }
          }
        }
      }
    }
}

############################################
# Prometheus target
############################################
resource "aws_bedrockagentcore_gateway_target" "agentcore_gateway_prometheus_target" {
    name = "${var.app_name}-PrometheusTools"
    gateway_identifier = aws_bedrockagentcore_gateway.agentcore_gateway.gateway_id

    credential_provider_configuration {
      gateway_iam_role {}
    }

    target_configuration {
      mcp {
        lambda {
          lambda_arn = aws_lambda_function.prometheus_lambda.arn
          tool_schema {
            inline_payload {
              name = "prometheus_tool"
              description = "Prometheus monitoring tool. Supports: query, query_range, get_alerts, get_error_rate, get_latency_percentiles, get_throghput, get_saturation, analyze_service, calculate_sli, check_error_budget"
              input_schema {
                type = "object"
                description = "Input for prometheus_tool"
                property {
                  name = "tool_name"
                  type = "string"
                  description = "Tool to execute"
                }
                property {
                  name = "query"
                  type = "string"
                  description = "PromQL query (for query/query range)"
                }
                property {
                  name = "job"
                  type = "string"
                  description = "Service/job name (for golden signals, SLI, error_budget)"
                }
                property {
                  name = "window"
                  type = "string"
                  description = "Time window: 5m, 1h, 24h, 30d (default: 5m)"
                }
                property {
                  name = "resource"
                  type = "string"
                  description = "Resource type for saturation: cpu, memory (default: cpu)"
                }
                property {
                  name = "namespace"
                  type = "string"
                  description = "K8S namespace"
                }
                property {
                  name = "slo_target"
                  type = "string"
                  description = "SLO target percentage (default: 99.9)"
                }
                property {
                  name = "start"
                  type = "string"
                  description = "Start time for query_range (ISO8601)"
                }
                property {
                  name = "end"
                  type = "string"
                  description = "End time for query_range (ISO8601)"
                }
                property {
                  name = "step"
                  type = "string"
                  description = "Step interval for query_range (default: 60s)"
                }
              }
            }
          }
        }
      }
    }
}
