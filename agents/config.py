import os
from google.adk.models.lite_llm import LiteLlm


def get_model() -> LiteLlm:
    name = os.environ.get("OPENAI_CHAT_LLM_NAME", "gpt-oss-20b")
    base = os.environ.get("OPENAI_API_BASE", "http://gx10.lan:8084/v1")
    return LiteLlm(model=f"openai/{name}", api_base=base)
