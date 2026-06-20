# Code Review Agent

Multi-agent code review tool, built from scratch with Google ADK and A2A protocol. Wanted to compare a different agent framework against LlamaIndex and see how independent HTTP microservices behave vs function calls.

**how it was built**
- used Claude Code to scaffold the A2A server boilerplate, agent structure, and shadow scoring implementation
- human-in-the-loop: directed the agent through many iterations to achieve supervisor routing reliability/accuracy, improve shadow scoring criteria, identify evaluation gaps the shadow scorer can't catch

**why I did this**
- wanted to compare a different RAG/agent framework against LlamaIndex
- learn how multi-agent coordination works and test its performance
- experiment with shadow scoring across different LLMs

**how it works**
- supervisor routes to three specialist agents in parallel: security, performance, dependency
- each agent runs as its own HTTP server using A2A protocol
- synthesis agent formats the findings, remediation agent suggests the fixes
- 10% of runs get re-evaluated asynchronously by a judge LLM (shadow scoring)

**what I learned**
- A2A is flexible, like micro-services, can be built, tested, scaled independently
- supervisor prompt took many tries to get it working well
- it is hard to define what "good result" means in shadow scoring
- unclear how well the LLM work for complex security bugs (next step)

**what's still open**
- shadow scoring detects quality regressions but not missed issues
- proper validation needs a golden dataset with known vulnerabilities and recall measurement
- build more tests with complex logic bugs 

**running it**
```
cp .env.example .env
python run_servers.py        # terminal 1
python main.py path/to/file  # terminal 2
pytest tests/ -v
```
