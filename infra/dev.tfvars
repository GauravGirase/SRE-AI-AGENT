
app_name         = "eks-sre-agent"
eks_cluster_name = "your-eks-cluster-name"
prometheus_url   = "http://your-prometheus-nodeport-ip:32575"

# VPC configuration for Lambda to access EKS/Prometheus
prometheus_vpc_config = {
  subnet_ids         = ["subnet-xxx", "subnet-yyy"]
  security_group_ids = ["sg-xxx"]
}