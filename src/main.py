
"""
Kubernetes SRE Agent - Powered by AWS Bedrock AgentCore
Investigates and heals EKS pod issues automatically
"""
import os
from strands import Agent, tool
from strands_tools.code_interpreter import AgentCoreCodeInterpreter
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from .mcp_client.client import get_streamable_http_mcp_client
from .model.load import load_model

MEMORY_ID = os.getenv("BEDROCK_AGENTCORE_MEMORY_ID")
REGION = os.getenv("AWS_REGION", "ap-south-1")

# System prompt for Kubernetes SRE Agent
SYSTEM_PROMPT = """Kubernetes SRE.

## TOOL 

- eks-sre-agent-KubernetesTools___kubernetes_sre_tool → Pod sorunları 
- eks-sre-agent-PrometheusTools___prometheus_tool → Prometheus alerts 
- eks-sre-agent-CloudWatchTools___cloudwatch_tool → CloudWatch alarms 

## CAPABILITIES

1. Pod  (crash, imagepull, oom, restart, hata) →  kubernetes_sre_tool
2. Alert  → prometheus_tool, cloudwatch_tool
3. MAX 5 
"""

if os.getenv("LOCAL_DEV") == "1":
    from contextlib import nullcontext
    from types import SimpleNamespace
    strands_mcp_client = nullcontext(SimpleNamespace(list_tools_sync=lambda: []))
else:
    strands_mcp_client = get_streamable_http_mcp_client()

# Integrate with Bedrock AgentCore
app = BedrockAgentCoreApp()
log = app.logger


@app.entrypoint
async def invoke(payload, context):
    """Main entry point for the SRE Agent"""
    session_id = getattr(context, 'session_id', 'default')

    # Configure memory for incident learning
    session_manager = None
    if MEMORY_ID:
        session_manager = AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id="sre-agent",
                retrieval_config={
                    "/incidents/summaries": RetrievalConfig(top_k=5, relevance_score=0.6),
                    "/incidents/patterns": RetrievalConfig(top_k=5, relevance_score=0.6),
                    "/incidents/fixes": RetrievalConfig(top_k=5, relevance_score=0.7)
                }
            ),
            REGION
        )
        log.info(f"Memory session initialized: {session_id}")
    else:
        log.warning("MEMORY_ID is not set. Agent will not remember past incidents.")

    # Create code interpreter for log analysis
    code_interpreter = AgentCoreCodeInterpreter(
        region=REGION,
        session_name=session_id,
        auto_create=True,
        persist_sessions=True
    )

    with strands_mcp_client as client:
        # Get MCP Tools (Kubernetes, Runbook, CloudWatch)
        tools = client.list_tools_sync()
        log.info(f"Loaded {len(tools)} MCP tools")

        # Create the SRE Agent
        agent = Agent(
            model=load_model(),
            session_manager=session_manager,
            system_prompt=SYSTEM_PROMPT,
            tools=[code_interpreter.code_interpreter] + tools
        )

        # Process the request
        prompt = payload.get("prompt", "")
        log.info(f"Processing request: {prompt[:100]}...")

        # Stream the response
        stream = agent.stream_async(prompt)

        async for event in stream:
            if "data" in event and isinstance(event["data"], str):
                yield event["data"]


@app.entrypoint
async def investigate(payload, context):
    """Investigate a specific pod issue"""
    pod_name = payload.get("pod_name")
    namespace = payload.get("namespace", "default")

    prompt = f"""Investigate the pod '{pod_name}' in namespace '{namespace}'.

1. Get pod status and description
2. Check container logs (use previous=true for crashed containers)
3. Review namespace events
4. Search runbooks for relevant solutions
5. Provide detailed diagnosis with root cause and recommendations

Be thorough and provide actionable insights."""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


@app.entrypoint
async def heal(payload, context):
    """Heal a pod issue (with dry-run support)"""
    pod_name = payload.get("pod_name")
    namespace = payload.get("namespace", "default")
    dry_run = payload.get("dry_run", True)

    mode = "dry-run" if dry_run else "execute"

    prompt = f"""Heal the pod '{pod_name}' in namespace '{namespace}' (mode: {mode}).

1. Diagnose the current issue
2. Search runbooks for solutions
3. {"Recommend healing actions (do NOT execute)" if dry_run else "Execute the appropriate fix"}
4. {"Provide kubectl commands that would fix the issue" if dry_run else "Verify the fix was applied"}

{"This is a DRY-RUN - do not make any changes, only recommend." if dry_run else "You are AUTHORIZED to make changes to fix the issue."}"""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


@app.entrypoint
async def scan(payload, context):
    """Scan a namespace for problematic pods"""
    namespace = payload.get("namespace", "default")

    prompt = f"""Scan the namespace '{namespace}' for problematic pods.

1. Get all pods in the namespace
2. Identify pods with issues:
   - CrashLoopBackOff
   - ImagePullBackOff
   - OOMKilled
   - Pending (stuck)
   - Error states
3. For each problematic pod, briefly describe the issue
4. Prioritize by severity

List all issues found with brief descriptions."""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


@app.entrypoint
async def analyze(payload, context):
    """Analyze service health using Golden Signals"""
    job = payload.get("job", payload.get("service"))
    window = payload.get("window", "5m")

    if not job:
        yield "Error: 'job' or 'service' parameter is required"
        return

    prompt = f"""Analyze the health of service '{job}' using SRE Golden Signals.

1. Use prometheus_tool with analyze_service (job="{job}", window="{window}")
2. Get detailed error rate with get_error_rate
3. Get latency percentiles with get_latency_percentiles
4. Get throughput with get_throughput
5. Check for any active alerts related to this service

Provide a comprehensive health report with:
- Overall health score
- All Golden Signals metrics
- Any anomalies or concerns
- Actionable recommendations"""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


@app.entrypoint
async def slo_status(payload, context):
    """Check SLI/SLO status and error budget"""
    job = payload.get("job", payload.get("service"))
    slo_target = payload.get("slo_target", 99.9)
    window = payload.get("window", "30d")

    if not job:
        yield "Error: 'job' or 'service' parameter is required"
        return

    prompt = f"""Check the SLI/SLO status for service '{job}'.

1. Use prometheus_tool with calculate_sli (job="{job}", slo_target={slo_target}, window="{window}")
2. Use prometheus_tool with check_error_budget (job="{job}", slo_target={slo_target}, window="{window}")
3. Get current error rate for context

Provide a report with:
- Current SLI value
- SLO target and whether it's being met
- Error budget remaining
- Trend analysis
- Recommendations (freeze deployments if budget is low, etc.)"""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


@app.entrypoint
async def alerts(payload, context):
    """Get active alerts from Prometheus and CloudWatch"""
    state = payload.get("state", "firing")  # firing, pending, all

    prompt = f"""Get all active alerts from the monitoring systems.

1. Use prometheus_tool with get_alerts to get Prometheus alerts
2. Use cloudwatch_tool with get_alarms to get CloudWatch alarms
3. Correlate alerts with potential pod issues

For each alert, provide:
- Alert name and severity
- Service/resource affected
- How long it's been active
- Potential root cause
- Recommended action

Prioritize by severity (critical > warning > info)."""

    async for chunk in invoke({"prompt": prompt}, context):
        yield chunk


if __name__ == "__main__":
    app.run()