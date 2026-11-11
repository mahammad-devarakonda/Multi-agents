import sys
from pathlib import Path
from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from langgraph_checkpoint.memory import MemorySaver  # type: ignore[import-untyped, import-not-found]
from app.agents.email_agents.state import EmailAgentState
from app.agents.email_agents.node import (
    router_node,
    read_emails_node,
    draft_email_node,
    draft_reply_node,
    detect_followups_node,
    revise_draft_node,
    send_email_node,
    chat_node,
)


def route_intent(state: EmailAgentState) -> str:
    """Conditional edge router based on intent."""
    intent = state.get("intent", "chat")
    if intent == "approve":
        return "send_email"
    elif intent == "revise":
        return "revise_draft"
    elif intent == "reject":
        return END
    elif intent == "read":
        return "read_emails"
    elif intent == "send":
        return "draft_email"
    elif intent == "reply":
        return "draft_reply"
    elif intent == "followup":
        return "detect_followups"
    else:
        return "chat"


def create_email_agent_graph(checkpointer=None):
    """Assembles and compiles the StateGraph for email operations."""
    workflow = StateGraph(EmailAgentState)

    # Add agent nodes
    workflow.add_node("router", router_node)
    workflow.add_node("read_emails", read_emails_node)
    workflow.add_node("draft_email", draft_email_node)
    workflow.add_node("draft_reply", draft_reply_node)
    workflow.add_node("detect_followups", detect_followups_node)
    workflow.add_node("revise_draft", revise_draft_node)
    workflow.add_node("send_email", send_email_node)
    workflow.add_node("chat", chat_node)

    # Add edges
    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router",
        route_intent,
        {
            "send_email": "send_email",
            "revise_draft": "revise_draft",
            "read_emails": "read_emails",
            "draft_email": "draft_email",
            "draft_reply": "draft_reply",
            "detect_followups": "detect_followups",
            "chat": "chat",
            END: END
        }
    )

    # Terminal transitions
    workflow.add_edge("read_emails", END)
    workflow.add_edge("draft_email", END)
    workflow.add_edge("draft_reply", END)
    workflow.add_edge("detect_followups", END)
    workflow.add_edge("revise_draft", END)
    workflow.add_edge("send_email", END)
    workflow.add_edge("chat", END)

    if checkpointer is None and MemorySaver is not None:
        checkpointer = MemorySaver()

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


class EmailAgentSession:
    """Session manager that preserves conversation and draft state across interactive turns."""

    def __init__(self, thread_id: str = "default_session"):
        self.thread_id = thread_id
        self.checkpointer = MemorySaver() if MemorySaver is not None else None
        self.graph = create_email_agent_graph(checkpointer=self.checkpointer)
        self.config = {"configurable": {"thread_id": self.thread_id}} if self.checkpointer is not None else {}
        self.state: Dict[str, Any] = {
            "messages": [],
            "intent": None,
            "search_query": None,
            "emails": [],
            "draft": None,
            "approval_status": None,
            "user_feedback": None,
            "result": None,
            "final_response": None,
        }

    def process_message(self, user_input: str) -> str:
        """Processes a user input message and returns the agent's response."""
        new_messages = [{"role": "user", "content": user_input}]
        self.state["messages"] = new_messages

        result = self.graph.invoke(self.state, config=self.config)
        self.state.update(result)

        # Append assistant response to messages for history
        agent_reply = result.get("final_response") or "Done."
        self.state["messages"].append({"role": "assistant", "content": agent_reply})

        return agent_reply


def interactive_cli():
    """Runs an interactive CLI loop to chat with the email agents directly from CMD."""
    try:
        if sys.stdout.encoding.lower() != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    divider = "=" * 70
    print(f"\n{divider}")
    print("  📧 AUTONOMOUS EMAIL AGENTS (CMD TERMINAL MODE)")
    print("  Powered by LangGraph + Groq + Gmail MCP")
    print(f"{divider}")
    print("Commands & Examples you can type:")
    print("  • Read:      'Read my unread emails' or 'Search from:someone@example.com'")
    print("  • Send:      'Send an email to john@example.com about the project sync'")
    print("  • Reply:     'Reply to latest email saying I will review it today'")
    print("  • Follow-up: 'Check for sent emails needing follow-up'")
    print("  • Exit:      'exit' or 'quit'")
    print(f"{divider}")
    print("🛡️  SAFETY: The agents will ALWAYS show you a preview and wait for")
    print("   your approval ('yes', edit instructions, or 'cancel') before sending!")
    print(f"{divider}\n")

    session = EmailAgentSession()

    while True:
        try:
            user_input = input("\nYou > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print("\n[System] Exiting Email Agents. Have a great day!")
                break

            response = session.process_message(user_input)
            print(f"\n{response}\n")
        except (KeyboardInterrupt, EOFError):
            print("\n[System] Exiting...")
            break
        except Exception as e:
            print(f"\n[System Error] {e}\n")


if __name__ == "__main__":
    interactive_cli()

