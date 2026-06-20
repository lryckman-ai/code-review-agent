import os
from google.adk.agents import LlmAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.adk.tools.agent_tool import AgentTool
from .config import get_model

# A2A server locations — override via env vars if agents run on other machines
_HOST = os.environ.get("AGENT_HOST", "localhost")
_PORTS = {
    "security_reviewer":    int(os.environ.get("SECURITY_PORT",    "8001")),
    "performance_reviewer": int(os.environ.get("PERFORMANCE_PORT", "8002")),
    "dependency_auditor":   int(os.environ.get("DEPENDENCY_PORT",  "8003")),
}

def _card(name: str) -> str:
    return f"http://{_HOST}:{_PORTS[name]}/.well-known/agent.json"


# Each specialist runs as an independent A2A microservice
remote_security = RemoteA2aAgent(
    name="security_reviewer",
    agent_card=_card("security_reviewer"),
    description="Reviews code for auth flaws, injection, and input validation issues.",
)

remote_performance = RemoteA2aAgent(
    name="performance_reviewer",
    agent_card=_card("performance_reviewer"),
    description="Reviews code for loop inefficiencies, memory issues, and N+1 DB queries.",
)

remote_dependency = RemoteA2aAgent(
    name="dependency_auditor",
    agent_card=_card("dependency_auditor"),
    description="Audits imports and dependencies for CVEs and dangerous usage patterns.",
)


INSTRUCTION = """
You are a senior code review supervisor. You coordinate specialist reviewers that run as
remote A2A microservices. Your role is to assess the submitted code and call the right
reviewers, then summarize what was found.

**Step 1 — Assess the code**
Quickly scan the code and determine:
- Language and general purpose
- Complexity: SIMPLE / MODERATE / COMPLEX
- Which tracks apply:
  * Security:     code handling user input, auth, sessions, file ops, external APIs, DB writes
  * Performance:  loops over large data, DB queries, memory-intensive ops, async logic
  * Dependency:   ALWAYS run — every file has imports that need auditing

**Step 2 — Call reviewers via A2A**
- ALWAYS call dependency_auditor (every file has imports worth checking)
- Call security_reviewer when security concerns apply
- Call performance_reviewer when performance concerns apply
- Pass the FULL original code text to each reviewer you call

**Step 3 — Handoff summary**
After receiving all reviewer outputs, write 2-3 sentences covering:
- What the code does and its complexity
- Which reviewers were called and why
- Total finding counts per track (e.g., "2 security HIGH, 1 perf MEDIUM, 3 dep findings")

Do not reproduce the full reviewer findings — the synthesis agent will format those.
"""


supervisor = LlmAgent(
    name="supervisor",
    model=get_model(),
    description=(
        "Analyzes code complexity and coordinates remote A2A specialist reviewers "
        "(security, performance, dependency)."
    ),
    instruction=INSTRUCTION,
    tools=[
        AgentTool(agent=remote_security),
        AgentTool(agent=remote_performance),
        AgentTool(agent=remote_dependency),
    ],
    output_key="supervisor_summary",
)
