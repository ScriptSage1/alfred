"""Alfred's generic agent. The Copilot backend lives in alfred.agent.copilot_backend
and is imported only where Alfred is wired together (alfred.assistant)."""

from alfred.agent.agent import Agent, AgentBackend, AgentError, AgentEvent

__all__ = ["Agent", "AgentBackend", "AgentError", "AgentEvent"]
