"""
Prometheus MCP Lambda - SRE Tools for Prometheus
Provides metrics analysis, SLI/SLO calculations, and alerting tools
"""
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")


def prometheus_query(query: str, time: str = None) -> dict:
    """Execute instant PromQL query."""
    params = {"query": query}
    if time:
        params["time"] = time

    url = f"{PROMETHEUS_URL}/api/v1/query?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def prometheus_query_range(query: str, start: str, end: str, step: str = "60s") -> dict:
    """Execute range PromQL query."""
    params = {"query": query, "start": start, "end": end, "step": step}
    url = f"{PROMETHEUS_URL}/api/v1/query_range?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_alerts() -> dict:
    """Get active alerts from Prometheus."""
    url = f"{PROMETHEUS_URL}/api/v1/alerts"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                alerts = data.get("data", {}).get("alerts", [])
                firing = [a for a in alerts if a.get("state") == "firing"]
                pending = [a for a in alerts if a.get("state") == "pending"]
                return {
                    "status": "success",
                    "firing_count": len(firing),
                    "pending_count": len(pending),
                    "firing_alerts": firing[:20],
                    "pending_alerts": pending[:10]
                }
            return data
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_error_rate(job: str, window: str = "5m") -> dict:
    """Calculate error rate for a service."""
    # HTTP 5xx errors
    query = f"""
    sum(rate(http_requests_total{{job="{job}", status=~"5.."}}[{window}])) /
    sum(rate(http_requests_total{{job="{job}"}}[{window}])) * 100
    """
    result = prometheus_query(query.strip())

    if result.get("status") == "success":
        data = result.get("data", {}).get("result", [])
        if data:
            error_rate = float(data[0].get("value", [0, 0])[1])
            return {
                "status": "success",
                "job": job,
                "window": window,
                "error_rate_percent": round(error_rate, 4),
                "health": "healthy" if error_rate < 1 else "degraded" if error_rate < 5 else "critical"
            }
        return {"status": "success", "job": job, "error_rate_percent": 0, "health": "healthy", "note": "No data"}
    return result


def get_latency_percentiles(job: str, window: str = "5m") -> dict:
    """Get p50, p90, p99 latency percentiles."""
    percentiles = {}

    for p, label in [(0.5, "p50"), (0.9, "p90"), (0.99, "p99")]:
        query = f'histogram_quantile({p}, sum(rate(http_request_duration_seconds_bucket{{job="{job}"}}[{window}])) by (le))'
        result = prometheus_query(query)

        if result.get("status") == "success":
            data = result.get("data", {}).get("result", [])
            if data:
                value = float(data[0].get("value", [0, 0])[1])
                percentiles[label] = round(value * 1000, 2)  # Convert to ms

    return {
        "status": "success",
        "job": job,
        "window": window,
        "latency_ms": percentiles,
        "health": "healthy" if percentiles.get("p99", 0) < 500 else "degraded" if percentiles.get("p99", 0) < 1000 else "critical"
    }


def get_throughput(job: str, window: str = "5m") -> dict:
    """Get requests per second."""
    query = f'sum(rate(http_requests_total{{job="{job}"}}[{window}]))'
    result = prometheus_query(query)

    if result.get("status") == "success":
        data = result.get("data", {}).get("result", [])
        if data:
            rps = float(data[0].get("value", [0, 0])[1])
            return {
                "status": "success",
                "job": job,
                "window": window,
                "requests_per_second": round(rps, 2)
            }
        return {"status": "success", "job": job, "requests_per_second": 0, "note": "No data"}
    return result


def get_saturation(resource: str = "cpu", namespace: str = None) -> dict:
    """Get resource saturation (CPU/Memory)."""
    if resource == "cpu":
        if namespace:
            query = f'avg(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[5m]) / container_spec_cpu_quota * 100)'
        else:
            query = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    elif resource == "memory":
        if namespace:
            query = f'avg(container_memory_usage_bytes{{namespace="{namespace}"}} / container_spec_memory_limit_bytes * 100)'
        else:
            query = '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
    else:
        return {"status": "error", "error": f"Unknown resource type: {resource}"}

    result = prometheus_query(query)

    if result.get("status") == "success":
        data = result.get("data", {}).get("result", [])
        if data:
            saturation = float(data[0].get("value", [0, 0])[1])
            return {
                "status": "success",
                "resource": resource,
                "namespace": namespace or "cluster-wide",
                "saturation_percent": round(saturation, 2),
                "health": "healthy" if saturation < 70 else "warning" if saturation < 85 else "critical"
            }
        return {"status": "success", "resource": resource, "saturation_percent": 0, "note": "No data"}
    return result


def analyze_service(job: str, window: str = "5m") -> dict:
    """Comprehensive service health analysis using Golden Signals."""
    error_rate = get_error_rate(job, window)
    latency = get_latency_percentiles(job, window)
    throughput = get_throughput(job, window)
    alerts = get_alerts()

    # Calculate overall health
    health_scores = []
    if error_rate.get("health") == "healthy":
        health_scores.append(100)
    elif error_rate.get("health") == "degraded":
        health_scores.append(70)
    else:
        health_scores.append(30)

    if latency.get("health") == "healthy":
        health_scores.append(100)
    elif latency.get("health") == "degraded":
        health_scores.append(70)
    else:
        health_scores.append(30)

    overall_health = sum(health_scores) / len(health_scores) if health_scores else 0

    # Get service-related alerts
    service_alerts = [a for a in alerts.get("firing_alerts", []) if job in str(a)]

    return {
        "status": "success",
        "job": job,
        "window": window,
        "overall_health_score": round(overall_health, 1),
        "golden_signals": {
            "error_rate": error_rate,
            "latency": latency,
            "throughput": throughput
        },
        "active_alerts": service_alerts[:5],
        "recommendation": "Service is healthy" if overall_health >= 80 else "Service needs attention" if overall_health >= 50 else "Service is critical - immediate action required"
    }


def calculate_sli(job: str, slo_target: float = 99.9, window: str = "24h") -> dict:
    """Calculate SLI and compare against SLO target."""
    # Availability SLI (successful requests / total requests)
    query = f"""
    (sum(rate(http_requests_total{{job="{job}", status!~"5.."}}[{window}])) /
    sum(rate(http_requests_total{{job="{job}"}}[{window}]))) * 100
    """
    result = prometheus_query(query.strip())

    if result.get("status") == "success":
        data = result.get("data", {}).get("result", [])
        if data:
            sli_value = float(data[0].get("value", [0, 0])[1])
            slo_met = sli_value >= slo_target

            return {
                "status": "success",
                "job": job,
                "window": window,
                "sli_value": round(sli_value, 4),
                "slo_target": slo_target,
                "slo_met": slo_met,
                "gap": round(sli_value - slo_target, 4),
                "health": "healthy" if slo_met else "at_risk" if sli_value > slo_target - 0.5 else "breached"
            }
        return {"status": "success", "job": job, "sli_value": 100, "slo_met": True, "note": "No data"}
    return result


def check_error_budget(job: str, slo_target: float = 99.9, window: str = "30d") -> dict:
    """Check error budget consumption."""
    sli_result = calculate_sli(job, slo_target, window)

    if sli_result.get("status") == "success":
        sli_value = sli_result.get("sli_value", 100)

        # Error budget = 100 - SLO target (e.g., 0.1% for 99.9% SLO)
        error_budget_total = 100 - slo_target
        # Consumed = how much of the budget we've used
        error_budget_consumed = 100 - sli_value
        # Remaining = what's left
        budget_remaining_percent = max(0, (error_budget_total - error_budget_consumed) / error_budget_total * 100)

        return {
            "status": "success",
            "job": job,
            "window": window,
            "slo_target": slo_target,
            "error_budget_total_percent": error_budget_total,
            "error_budget_consumed_percent": round(error_budget_consumed, 4),
            "budget_remaining_percent": round(budget_remaining_percent, 2),
            "health": "healthy" if budget_remaining_percent > 50 else "warning" if budget_remaining_percent > 20 else "critical",
            "recommendation": "Budget healthy" if budget_remaining_percent > 50 else "Slow down deployments" if budget_remaining_percent > 20 else "Freeze changes - budget exhausted"
        }
    return sli_result


# Tool registry
TOOL_FUNCTIONS = {
    "query": lambda **kw: prometheus_query(kw.get("query", "up")),
    "query_range": lambda **kw: prometheus_query_range(
        kw.get("query", "up"),
        kw.get("start", (datetime.now() - timedelta(hours=1)).isoformat() + "Z"),
        kw.get("end", datetime.now().isoformat() + "Z"),
        kw.get("step", "60s")
    ),
    "get_alerts": lambda **kw: get_alerts(),
    "get_error_rate": lambda **kw: get_error_rate(kw.get("job", ""), kw.get("window", "5m")),
    "get_latency_percentiles": lambda **kw: get_latency_percentiles(kw.get("job", ""), kw.get("window", "5m")),
    "get_throughput": lambda **kw: get_throughput(kw.get("job", ""), kw.get("window", "5m")),
    "get_saturation": lambda **kw: get_saturation(kw.get("resource", "cpu"), kw.get("namespace")),
    "analyze_service": lambda **kw: analyze_service(kw.get("job", ""), kw.get("window", "5m")),
    "calculate_sli": lambda **kw: calculate_sli(kw.get("job", ""), kw.get("slo_target", 99.9), kw.get("window", "24h")),
    "check_error_budget": lambda **kw: check_error_budget(kw.get("job", ""), kw.get("slo_target", 99.9), kw.get("window", "30d")),
}


def lambda_handler(event: dict, context: Any) -> dict:
    """AWS Lambda handler for AgentCore Gateway."""
    try:
        # Parse tool call
        tool_name = event.get("tool_name", "")

        if not tool_name:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "tool_name is required"})
            }

        if tool_name not in TOOL_FUNCTIONS:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": f"Unknown tool: {tool_name}", "available_tools": list(TOOL_FUNCTIONS.keys())})
            }

        # Execute tool
        result = TOOL_FUNCTIONS[tool_name](**event)

        return {
            "statusCode": 200,
            "body": json.dumps(result, indent=2, default=str)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }