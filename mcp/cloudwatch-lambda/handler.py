"""
CloudWatch Tools Lambda Handler
Provides tools for querying EKS metrics, alarms, and logs via real AWS API
"""
import json
import os
import boto3
from datetime import datetime, timedelta
from typing import Any, Dict

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
CLUSTER_NAME = os.environ.get("EKS_CLUSTER_NAME", "sandbox-test-dev-eks")

# Initialize AWS clients
cloudwatch = boto3.client("cloudwatch", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)


def lambda_handler(event, context):
    """Main Lambda handler for CloudWatch tools"""
    try:
        context_tool_name = _get_tool_name_from_context(context)

        tools = {
            "get_alarms": get_alarms,
            "get_metrics": get_metrics,
            "query_logs": query_logs,
        }

        # Handle unified tool schema (cloudwatch_tool)
        if context_tool_name == "cloudwatch_tool":
            tool_name = event.get("tool_name")
            if not tool_name:
                return _response(400, {"error": "Missing 'tool_name'", "available": list(tools.keys())})
        else:
            tool_name = context_tool_name or event.get("tool_name")

        if not tool_name or tool_name not in tools:
            return _response(400, {"error": f"Unknown tool '{tool_name}'", "available": list(tools.keys())})

        result = tools[tool_name](event)
        return _response(200, {"result": result})

    except Exception as e:
        return _response(500, {"error": str(e), "type": type(e).__name__})


def _get_tool_name_from_context(context):
    """Extract tool name from Lambda context"""
    if hasattr(context, 'client_context') and context.client_context:
        custom = getattr(context.client_context, 'custom', {}) or {}
        extended_name = custom.get("bedrockAgentCoreToolName", "")
        if extended_name and "___" in extended_name:
            return extended_name.split("___", 1)[1]
    return None


def _response(status_code: int, body: Dict[str, Any]):
    """JSON response wrapper"""
    return {"statusCode": status_code, "body": json.dumps(body, default=str)}


def get_alarms(event: Dict[str, Any]) -> Dict:
    """Get CloudWatch alarms - real AWS API"""
    state_filter = event.get("state")  # ALARM, OK, INSUFFICIENT_DATA

    try:
        params = {"MaxRecords": 50}
        if state_filter:
            params["StateValue"] = state_filter.upper()

        response = cloudwatch.describe_alarms(**params)

        alarms = []
        for alarm in response.get("MetricAlarms", []):
            alarms.append({
                "alarm_name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "metric": alarm.get("MetricName", "N/A"),
                "namespace": alarm.get("Namespace", "N/A"),
                "threshold": alarm.get("Threshold"),
                "description": alarm.get("AlarmDescription", ""),
                "last_updated": alarm.get("StateUpdatedTimestamp", "").isoformat() if alarm.get("StateUpdatedTimestamp") else None
            })

        alarm_count = len([a for a in alarms if a["state"] == "ALARM"])

        return {
            "alarms": alarms,
            "total": len(alarms),
            "in_alarm": alarm_count,
            "cluster": CLUSTER_NAME,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


def get_metrics(event: Dict[str, Any]) -> Dict:
    """Get CloudWatch metrics for EKS - real AWS API"""
    namespace = event.get("namespace", "ContainerInsights")
    metric_name = event.get("metric_name", "pod_cpu_utilization")
    period = int(event.get("period", 300))  # 5 minutes default

    try:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=1)

        response = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=[
                {"Name": "ClusterName", "Value": CLUSTER_NAME}
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Average", "Maximum", "Minimum"]
        )

        datapoints = sorted(response.get("Datapoints", []), key=lambda x: x["Timestamp"])

        return {
            "metric_name": metric_name,
            "namespace": namespace,
            "cluster": CLUSTER_NAME,
            "datapoints": [
                {
                    "timestamp": dp["Timestamp"].isoformat(),
                    "average": dp.get("Average"),
                    "maximum": dp.get("Maximum"),
                    "minimum": dp.get("Minimum")
                }
                for dp in datapoints[-10:]  # Last 10 datapoints
            ],
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}


def query_logs(event: Dict[str, Any]) -> Dict:
    """Query CloudWatch Logs - real AWS API"""
    log_group = event.get("log_group", f"/aws/containerinsights/{CLUSTER_NAME}/application")
    query = event.get("query", "fields @timestamp, @message | filter @message like /error|Error|ERROR/ | sort @timestamp desc | limit 20")
    hours = int(event.get("hours", 1))

    try:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)

        # Start query
        start_response = logs.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query
        )

        query_id = start_response["queryId"]

        # Wait for results (with timeout)
        import time
        for _ in range(10):  # Max 10 attempts
            result = logs.get_query_results(queryId=query_id)
            if result["status"] == "Complete":
                break
            time.sleep(0.5)

        results = []
        for row in result.get("results", [])[:20]:
            entry = {}
            for field in row:
                entry[field["field"]] = field["value"]
            results.append(entry)

        return {
            "log_group": log_group,
            "query": query,
            "results": results,
            "count": len(results),
            "status": result["status"],
            "timestamp": datetime.utcnow().isoformat()
        }
    except logs.exceptions.ResourceNotFoundException:
        return {"error": f"Log group '{log_group}' not found", "status": "failed"}
    except Exception as e:
        return {"error": str(e), "status": "failed"}