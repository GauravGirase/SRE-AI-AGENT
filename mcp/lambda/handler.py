"""
Kubernetes SRE Tools Lambda Handler
Provides tools for investigating and healing EKS pod issues
Uses real Kubernetes API via IAM authentication
"""
import json
import os
import base64
import re
from typing import Any, Dict, List, Optional
from datetime import datetime
import boto3
from botocore.signers import RequestSigner
from kubernetes import client
from kubernetes.client.rest import ApiException

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
CLUSTER_NAME = os.environ.get("EKS_CLUSTER_NAME", "eks-prod-ready")

# Kubernetes API clients (initialized lazily)
_k8s_client = None
_apps_client = None


def get_eks_token():
    """Generate EKS authentication token using IAM"""
    STS_TOKEN_EXPIRES_IN = 60
    session = boto3.session.Session()

    # Get STS client
    sts_client = session.client('sts', region_name=AWS_REGION)
    service_id = sts_client.meta.service_model.service_id

    # Create request signer
    signer = RequestSigner(
        service_id,
        AWS_REGION,
        'sts',
        'v4',
        session.get_credentials(),
        session.events
    )

    # Generate presigned URL for GetCallerIdentity
    params = {
        'method': 'GET',
        'url': f'https://sts.{AWS_REGION}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15',
        'body': {},
        'headers': {
            'x-k8s-aws-id': CLUSTER_NAME
        },
        'context': {}
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=AWS_REGION,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=''
    )

    # Create the token (base64 encoded URL with k8s-aws-v1 prefix)
    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')

    # Remove padding
    return 'k8s-aws-v1.' + re.sub(r'=*$', '', base64_url)


def get_k8s_clients():
    """Initialize Kubernetes clients with EKS authentication"""
    global _k8s_client, _apps_client

    if _k8s_client is not None:
        return _k8s_client, _apps_client

    # Get cluster info from EKS
    eks_client = boto3.client('eks', region_name=AWS_REGION)
    cluster_info = eks_client.describe_cluster(name=CLUSTER_NAME)['cluster']

    # Extract cluster details
    cluster_endpoint = cluster_info['endpoint']
    cluster_ca = cluster_info['certificateAuthority']['data']

    # Get authentication token
    token = get_eks_token()

    # Configure kubernetes client
    configuration = client.Configuration()
    configuration.host = cluster_endpoint
    configuration.verify_ssl = True
    configuration.ssl_ca_cert = '/tmp/ca.crt'

    # Write CA certificate to temp file
    with open('/tmp/ca.crt', 'wb') as f:
        f.write(base64.b64decode(cluster_ca))

    # Set bearer token
    configuration.api_key = {"authorization": f"Bearer {token}"}

    # Create API client
    api_client = client.ApiClient(configuration)
    _k8s_client = client.CoreV1Api(api_client)
    _apps_client = client.AppsV1Api(api_client)

    return _k8s_client, _apps_client


# Runbook data
RUNBOOKS = {
    "crashloopbackoff": {
        "id": "crashloopbackoff-001",
        "title": "CrashLoopBackOff Troubleshooting",
        "symptoms": ["Container repeatedly crashes", "Restart count increasing", "Pod never becomes Ready"],
        "causes": ["Application error", "Missing config/secrets", "Resource limits too low", "Liveness probe failing"],
        "diagnosis_steps": [
            "1. Check pod events: kubectl describe pod <pod-name>",
            "2. Check container logs: kubectl logs <pod-name> --previous",
            "3. Verify ConfigMaps and Secrets exist",
            "4. Check resource limits vs actual usage"
        ],
        "solutions": [
            {"condition": "application_error", "action": "Fix application code or configuration", "command": "kubectl logs {pod_name} -n {namespace} --previous"},
            {"condition": "missing_config", "action": "Create missing ConfigMap or Secret", "command": "kubectl get configmaps,secrets -n {namespace}"},
            {"condition": "resource_limits", "action": "Increase memory/CPU limits", "command": "kubectl set resources deployment/{deployment} --limits=memory=512Mi -n {namespace}"}
        ]
    },
    "oomkilled": {
        "id": "oomkilled-001",
        "title": "OOMKilled Troubleshooting",
        "symptoms": ["Container killed due to OOM", "Exit code 137", "Memory usage spikes"],
        "causes": ["Memory leak", "Insufficient memory limit", "Large data processing"],
        "diagnosis_steps": [
            "1. Check if container was OOMKilled: kubectl describe pod <pod>",
            "2. Check memory limits: kubectl get pod <pod> -o yaml | grep memory",
            "3. Monitor memory usage: kubectl top pod <pod>"
        ],
        "solutions": [
            {"condition": "memory_limit", "action": "Increase memory limit", "command": "kubectl set resources deployment/{deployment} --limits=memory=1Gi -n {namespace}"},
            {"condition": "memory_leak", "action": "Investigate and fix memory leak in application", "command": "kubectl top pod {pod_name} -n {namespace}"},
            {"condition": "restart", "action": "Restart deployment", "command": "kubectl rollout restart deployment/{deployment} -n {namespace}"}
        ]
    },
    "imagepullbackoff": {
        "id": "imagepullbackoff-001",
        "title": "ImagePullBackOff Troubleshooting",
        "symptoms": ["Image pull failing", "ErrImagePull", "ImagePullBackOff status"],
        "causes": ["Wrong image name/tag", "Private registry auth missing", "Network issues"],
        "diagnosis_steps": [
            "1. Verify image name: kubectl describe pod <pod> | grep Image",
            "2. Check imagePullSecrets: kubectl get pod <pod> -o yaml | grep imagePullSecrets",
            "3. Test registry access manually"
        ],
        "solutions": [
            {"condition": "wrong_image", "action": "Fix image name or tag in deployment", "command": "kubectl set image deployment/{deployment} {container}={correct_image} -n {namespace}"},
            {"condition": "auth_missing", "action": "Add imagePullSecret to deployment", "command": "kubectl patch serviceaccount default -p '{\"imagePullSecrets\": [{\"name\": \"regcred\"}]}' -n {namespace}"}
        ]
    }
}


def lambda_handler(event, context):
    """Main Lambda handler for Kubernetes SRE tools"""
    try:
        # Get tool name from context
        context_tool_name = None
        if hasattr(context, 'client_context') and context.client_context:
            custom = getattr(context.client_context, 'custom', {}) or {}
            extended_name = custom.get("bedrockAgentCoreToolName", "")
            if extended_name and "___" in extended_name:
                context_tool_name = extended_name.split("___", 1)[1]

        # Available tools
        tools = {
            "get_pods": get_pods,
            "get_pod_logs": get_pod_logs,
            "describe_pod": describe_pod,
            "get_events": get_events,
            "restart_deployment": restart_deployment,
            "scale_deployment": scale_deployment,
            "search_runbook": search_runbook,
            "get_solution": get_solution,
            "suggest_fix": suggest_fix,
        }

        # Handle unified tool schema (kubernetes_sre_tool)
        if context_tool_name == "kubernetes_sre_tool":
            tool_name = event.get("tool_name")
            if not tool_name:
                return _response(400, {"error": "Missing 'tool_name' parameter", "available": list(tools.keys())})
        else:
            # Direct tool invocation (fallback)
            tool_name = context_tool_name or event.get("tool_name")

        if not tool_name:
            return _response(400, {"error": "Missing tool name"})

        if tool_name not in tools:
            return _response(400, {"error": f"Unknown tool '{tool_name}'", "available": list(tools.keys())})

        result = tools[tool_name](event)
        return _response(200, {"result": result})

    except ApiException as e:
        return _response(e.status, {"error": f"Kubernetes API error: {e.reason}", "details": str(e)})
    except Exception as e:
        return _response(500, {"error": str(e)})


def _response(status_code: int, body: Dict[str, Any]):
    """JSON response wrapper"""
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}


# Kubernetes Tools (using real EKS API)
def get_pods(event: Dict[str, Any]) -> Dict:
    """Get pods in a namespace"""
    namespace = event.get("namespace", "default")
    label_selector = event.get("label_selector", "")

    v1, _ = get_k8s_clients()

    pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)

    result = []
    for pod in pods.items:
        containers = []
        for cs in pod.status.container_statuses or []:
            state = "Unknown"
            if cs.state.running:
                state = "Running"
            elif cs.state.waiting:
                state = f"Waiting: {cs.state.waiting.reason}"
            elif cs.state.terminated:
                state = f"Terminated: {cs.state.terminated.reason}"

            containers.append({
                "name": cs.name,
                "ready": cs.ready,
                "restarts": cs.restart_count,
                "state": state
            })

        result.append({
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "containers": containers,
            "node": pod.spec.node_name
        })

    return {"pods": result, "count": len(result), "namespace": namespace}


def get_pod_logs(event: Dict[str, Any]) -> Dict:
    """Get logs from a pod"""
    namespace = event.get("namespace", "default")
    pod_name = event.get("pod_name")
    container = event.get("container")
    previous = event.get("previous", False)
    tail_lines = event.get("tail_lines", 100)

    if not pod_name:
        return {"error": "pod_name is required"}

    v1, _ = get_k8s_clients()

    try:
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            previous=previous,
            tail_lines=tail_lines
        )

        return {
            "logs": logs,
            "pod": pod_name,
            "namespace": namespace,
            "container": container,
            "previous": previous
        }
    except ApiException as e:
        if e.status == 400 and "previous terminated container" in str(e):
            return {"error": "No previous container logs available", "pod": pod_name}
        raise


def describe_pod(event: Dict[str, Any]) -> Dict:
    """Describe a pod with detailed info"""
    namespace = event.get("namespace", "default")
    pod_name = event.get("pod_name")

    if not pod_name:
        return {"error": "pod_name is required"}

    v1, _ = get_k8s_clients()

    pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)

    # Extract container statuses
    containers = []
    for cs in pod.status.container_statuses or []:
        state = "Unknown"
        state_details = {}
        if cs.state.running:
            state = "Running"
            state_details = {"started_at": str(cs.state.running.started_at)}
        elif cs.state.waiting:
            state = f"Waiting: {cs.state.waiting.reason}"
            state_details = {"message": cs.state.waiting.message}
        elif cs.state.terminated:
            state = f"Terminated: {cs.state.terminated.reason}"
            state_details = {
                "exit_code": cs.state.terminated.exit_code,
                "message": cs.state.terminated.message
            }

        containers.append({
            "name": cs.name,
            "ready": cs.ready,
            "restarts": cs.restart_count,
            "state": state,
            "state_details": state_details,
            "image": cs.image
        })

    # Extract conditions
    conditions = []
    for cond in pod.status.conditions or []:
        conditions.append({
            "type": cond.type,
            "status": cond.status,
            "reason": cond.reason,
            "message": cond.message
        })

    # Extract resource requests/limits
    resources = []
    for container in pod.spec.containers:
        res = {"name": container.name}
        if container.resources:
            if container.resources.requests:
                res["requests"] = dict(container.resources.requests)
            if container.resources.limits:
                res["limits"] = dict(container.resources.limits)
        resources.append(res)

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "node": pod.spec.node_name,
        "host_ip": pod.status.host_ip,
        "pod_ip": pod.status.pod_ip,
        "start_time": str(pod.status.start_time),
        "containers": containers,
        "conditions": conditions,
        "resources": resources,
        "labels": dict(pod.metadata.labels or {}),
        "annotations": dict(pod.metadata.annotations or {})
    }


def get_events(event: Dict[str, Any]) -> Dict:
    """Get events for a namespace or pod"""
    namespace = event.get("namespace", "default")
    pod_name = event.get("pod_name")

    v1, _ = get_k8s_clients()

    # Get events from the namespace
    if pod_name:
        field_selector = f"involvedObject.name={pod_name}"
        events = v1.list_namespaced_event(namespace=namespace, field_selector=field_selector)
    else:
        events = v1.list_namespaced_event(namespace=namespace)

    result = []
    for ev in events.items:
        result.append({
            "type": ev.type,
            "reason": ev.reason,
            "message": ev.message,
            "object": f"{ev.involved_object.kind}/{ev.involved_object.name}",
            "count": ev.count,
            "first_seen": str(ev.first_timestamp),
            "last_seen": str(ev.last_timestamp)
        })

    # Sort by last_seen (most recent first)
    result.sort(key=lambda x: x["last_seen"] or "", reverse=True)

    return {"events": result[:50], "count": len(result), "namespace": namespace}


def restart_deployment(event: Dict[str, Any]) -> Dict:
    """Restart a deployment by patching with a restart annotation"""
    namespace = event.get("namespace", "default")
    deployment_name = event.get("deployment_name")

    if not deployment_name:
        return {"error": "deployment_name is required"}

    _, apps_v1 = get_k8s_clients()

    # Patch the deployment with a restart annotation
    # This is equivalent to: kubectl rollout restart deployment/<name>
    now = datetime.utcnow().isoformat() + "Z"
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now
                    }
                }
            }
        }
    }

    apps_v1.patch_namespaced_deployment(
        name=deployment_name,
        namespace=namespace,
        body=patch
    )

    return {
        "success": True,
        "message": f"Deployment {deployment_name} restart triggered in namespace {namespace}",
        "action": "rollout_restart",
        "timestamp": now
    }


def scale_deployment(event: Dict[str, Any]) -> Dict:
    """Scale a deployment to specified replicas"""
    namespace = event.get("namespace", "default")
    deployment_name = event.get("deployment_name")
    replicas = event.get("replicas")

    if not deployment_name:
        return {"error": "deployment_name is required"}

    if replicas is None:
        return {"error": "replicas is required"}

    _, apps_v1 = get_k8s_clients()

    # Get current deployment
    deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    current_replicas = deployment.spec.replicas

    # Scale the deployment
    patch = {"spec": {"replicas": int(replicas)}}
    apps_v1.patch_namespaced_deployment(
        name=deployment_name,
        namespace=namespace,
        body=patch
    )

    return {
        "success": True,
        "message": f"Deployment {deployment_name} scaled from {current_replicas} to {replicas} replicas",
        "deployment": deployment_name,
        "namespace": namespace,
        "previous_replicas": current_replicas,
        "new_replicas": replicas
    }


# Runbook Tools
def search_runbook(event: Dict[str, Any]) -> Dict:
    """Search runbooks based on keywords"""
    query = event.get("query", "").lower()
    max_results = event.get("max_results", 5)

    results = []
    for key, runbook in RUNBOOKS.items():
        score = 0
        if query in key:
            score += 5
        if query in runbook["title"].lower():
            score += 3
        for symptom in runbook["symptoms"]:
            if query in symptom.lower():
                score += 2
        for cause in runbook["causes"]:
            if query in cause.lower():
                score += 1

        if score > 0:
            results.append({
                "id": runbook["id"],
                "title": runbook["title"],
                "score": score,
                "symptoms": runbook["symptoms"]
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"results": results[:max_results], "query": query}


def get_solution(event: Dict[str, Any]) -> Dict:
    """Get detailed solution from a runbook"""
    runbook_id = event.get("runbook_id", "")

    for key, runbook in RUNBOOKS.items():
        if runbook["id"] == runbook_id:
            return runbook

    return {"error": f"Runbook not found: {runbook_id}", "available": [r["id"] for r in RUNBOOKS.values()]}


def suggest_fix(event: Dict[str, Any]) -> Dict:
    """Suggest a fix based on error message"""
    error_message = event.get("error_message", "").lower()

    suggestions = []

    if "crashloop" in error_message or "crash" in error_message or "backoff" in error_message:
        suggestions.append(RUNBOOKS["crashloopbackoff"])
    if "oom" in error_message or "memory" in error_message or "137" in error_message or "killed" in error_message:
        suggestions.append(RUNBOOKS["oomkilled"])
    if "imagepull" in error_message or "image" in error_message or "pull" in error_message:
        suggestions.append(RUNBOOKS["imagepullbackoff"])

    if not suggestions:
        return {
            "message": "No specific runbook found for this error",
            "error_message": error_message,
            "general_steps": [
                "1. Check pod events: kubectl describe pod <pod>",
                "2. Check logs: kubectl logs <pod> --previous",
                "3. Check resources: kubectl top pod <pod>",
                "4. Check cluster events: kubectl get events --sort-by='.lastTimestamp'"
            ]
        }

    return {"suggestions": suggestions, "error_message": error_message}