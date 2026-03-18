from strands.models import BedrockModel

# Mistral Large - good reasoning + less restrictive on actions
MODEL_ID = "eu.anthropic.claude-3-5-sonnet-20240620-v1:0"

def load_model() -> BedrockModel:
    """
    Get Bedrock model client.
    Uses IAM authentication via the execution role.
    """
    return BedrockModel(model_id=MODEL_ID)