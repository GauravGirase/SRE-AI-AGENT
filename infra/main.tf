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