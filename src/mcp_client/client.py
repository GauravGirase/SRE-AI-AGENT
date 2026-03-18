import os
import logging
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient
import requests

logger = logging.getLogger(__name__)

# Required environment variables
COGNITO_TOKEN_URL = os.getenv("COGNITO_TOKEN_URL")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET")
COGNITO_SCOPE = os.getenv("COGNITO_SCOPE")


def _validate_cognito_config():
    """Validate all required Cognito environment variables are set."""
    missing = []
    if not COGNITO_TOKEN_URL:
        missing.append("COGNITO_TOKEN_URL")
    if not COGNITO_CLIENT_ID:
        missing.append("COGNITO_CLIENT_ID")
    if not COGNITO_CLIENT_SECRET:
        missing.append("COGNITO_CLIENT_SECRET")
    if not COGNITO_SCOPE:
        missing.append("COGNITO_SCOPE")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def _get_access_token():
    """
    Make a POST request to the Cognito OAuth token URL using client credentials.
    """
    _validate_cognito_config()

    try:
        response = requests.post(
            COGNITO_TOKEN_URL,
            auth=(COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET),
            data={
                "grant_type": "client_credentials",
                "scope": COGNITO_SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.exceptions.Timeout:
        logger.error("Cognito token request timed out")
        raise RuntimeError("Failed to get access token: request timed out")
    except requests.exceptions.RequestException as e:
        logger.error(f"Cognito token request failed: {e}")
        raise RuntimeError(f"Failed to get access token: {e}")
    except KeyError:
        logger.error("Invalid Cognito response: missing access_token")
        raise RuntimeError("Failed to get access token: invalid response from Cognito")


def get_streamable_http_mcp_client() -> MCPClient:
    """
    Returns an MCP Client for AgentCore Gateway compatible with Strands.
    """
    gateway_url = os.getenv("GATEWAY_URL")
    if not gateway_url:
        raise RuntimeError("Missing required environment variable: GATEWAY_URL")

    access_token = _get_access_token()
    logger.info(f"Connected to MCP Gateway: {gateway_url}")

    return MCPClient(lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {access_token}"}))