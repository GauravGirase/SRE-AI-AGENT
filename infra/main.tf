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

resource "aws_lambda_function" "mcp_lambda" {
    function_name = "${var.app_name}-CloudWatchLambda"
    role = aws_iam_role.mcp_lambda_role.arn
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


