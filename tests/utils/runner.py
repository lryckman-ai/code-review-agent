"""Helpers to run individual agents or the full pipeline in tests."""
import sys
from pathlib import Path

# Make sure codereview/ root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part


async def run_agent(agent, code: str, label: str = "test") -> str:
    """Run a single ADK agent directly — no A2A servers needed."""
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test", user_id="test")

    runner = Runner(agent=agent, app_name="test", session_service=session_service)
    message = Content(
        role="user",
        parts=[Part(text=f"Review this code ({label}):\n\n```python\n{code}\n```")],
    )

    output = ""
    async for event in runner.run_async(
        user_id="test", session_id=session.id, new_message=message
    ):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    output += part.text

    return output


async def run_security(code: str) -> str:
    from agents.security_reviewer import security_reviewer
    return await run_agent(security_reviewer, code)


async def run_performance(code: str) -> str:
    from agents.performance_reviewer import performance_reviewer
    return await run_agent(performance_reviewer, code)


async def run_dependency(code: str) -> str:
    from agents.dependency_auditor import dependency_auditor
    return await run_agent(dependency_auditor, code)


async def run_remediation(code: str, review_output: str) -> str:
    from agents.remediation import remediation_agent
    combined = (
        f"Original code:\n```python\n{code}\n```\n\n"
        f"Review findings:\n{review_output}"
    )
    return await run_agent(remediation_agent, combined)


CATEGORY_RUNNERS = {
    "security": run_security,
    "performance": run_performance,
    "dependency": run_dependency,
}


async def run_for_category(category: str, code: str) -> str:
    runner_fn = CATEGORY_RUNNERS.get(category)
    if runner_fn is None:
        raise ValueError(f"No direct runner for category '{category}'. Use full pipeline.")
    return await runner_fn(code)
