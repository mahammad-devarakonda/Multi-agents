# Autonomous Email Agents (LangGraph + Groq + Gmail MCP)

An agentic email management system featuring Human-in-the-Loop (HITL) assistance, context-aware drafting, thread-preserving replies, and automatic follow-up tracking.

## 🚀 Key Capabilities

1. **Read Emails**: Search, fetch, categorize, and summarize inbox messages or specific email threads.
2. **Send Emails (Human-Assisted)**: Composes new emails based on natural language instructions, generates a formatted preview, and sends **only after** explicit user confirmation.
3. **Reply to Emails (Human-Assisted)**: Analyzes the complete thread history, constructs a context-aware reply preserving headers (`In-Reply-To`, `References`, `threadId`), displays the preview, and sends **only after** confirmation.
4. **Follow-up on Emails**: Scans sent emails where no response was received, crafts polite follow-up reminders, and waits for your confirmation to send.
5. **Interactive Revisions**: If you want changes to any draft, simply ask (*"make it shorter"*, *"change subject"*, *"add X"*), and the agent updates the draft before sending.

---

## 📁 Architecture Overview

```
backend/
├── app/
│   ├── mcp/
│   │   └── gmail_mcp_server.py       # Gmail MCP server (tools: search, read, thread, reply, follow-up)
│   ├── agents/
│   │   ├── __init__.py
│   │   └── email_agents/
│   │       ├── state.py              # TypedDict & Pydantic state schemas
│   │       ├── prompts.py            # Specialized prompt templates
│   │       ├── node.py               # LangGraph node implementations
│   │       └── graph.py              # Compiled StateGraph & interactive session
│   └── main.py                       # CLI entry point
```

---

## 🛠️ How to Run

### 1. Interactive Email Agent (LangGraph)
Run the agent with the command line interface:

```bash
cd backend
python -m app.agents.email_agents.graph
```
*(Or via `python app/main.py --agent`)*

### 2. Sample Prompts
- **Reading Emails**:
  - `"Read my latest unread emails"`
  - `"Search emails from client@example.com"`
- **Sending an Email (Assisted)**:
  - `"Draft an email to john@example.com asking for the quarterly report"`
  - *(Agent previews draft)* $\rightarrow$ Reply `"yes"` or `"change the tone to formal"`
- **Replying to an Email (Assisted)**:
  - `"Reply to the latest email confirming our meeting for 2 PM tomorrow"`
  - *(Agent previews reply with thread context)* $\rightarrow$ Reply `"yes"`
- **Checking Follow-ups**:
  - `"Check my sent emails that haven't received a reply and draft a follow-up"`
  - *(Agent previews follow-up)* $\rightarrow$ Reply `"yes"` or `"cancel"`

