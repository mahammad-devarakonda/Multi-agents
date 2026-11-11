import os
import sys
from pathlib import Path

# Ensure backend root is in sys.path regardless of where python is executed from
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import json
import asyncio
from dotenv import load_dotenv
from groq import Groq

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Load environment variables from .env in backend directory
ROOT_DIR = Path(__file__).parent.parent.resolve()
load_dotenv(ROOT_DIR / ".env")

SERVER_SCRIPT = Path(__file__).parent / "mcp" / "gmail_mcp_server.py"

from app.core.LLMConfig import get_groq_client

def ensure_gmail_auth():
    """Ensures OAuth token.json exists before launching the stdio MCP server."""
    token_candidates = [
        ROOT_DIR / "app" / "mcp" / "token.json",
        ROOT_DIR / "token.json"
    ]
    if any(t.exists() for t in token_candidates):
        return

    print("\n[Auth] First-time setup: Opening browser to authorize your Gmail account...")
    sys.path.insert(0, str(Path(__file__).parent / "mcp"))
    try:
        import gmail_mcp_server
        gmail_mcp_server.get_gmail_service()
        print("[Auth] Authorization successful! Credentials saved to token.json.\n")
    except Exception as e:
        print(f"[Auth Error] Failed to complete Gmail authorization: {e}")
        raise

def mcp_tool_to_groq_schema(tool) -> dict:
    """Converts an MCP tool definition into Groq tool format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema if hasattr(tool, "inputSchema") else {"type": "object", "properties": {}}
        }
    }

async def run_agent_turn(client: Groq, model: str, session: ClientSession, conversation_history: list):
    """Handles an agent turn with Groq LLM, including tool calling via the Gmail MCP Server."""
    tools_list = await session.list_tools()
    groq_tools = [mcp_tool_to_groq_schema(t) for t in tools_list.tools]

    response = client.chat.completions.create(
        model=model,
        messages=conversation_history,
        tools=groq_tools if groq_tools else None,
        tool_choice="auto" if groq_tools else None,
        temperature=0.7
    )

    msg = response.choices[0].message
    
    msg_dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        msg_dict["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                }
            }
            for tc in msg.tool_calls
        ]
    conversation_history.append(msg_dict)

    if msg.tool_calls:
        for tool_call in msg.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)

            print(f"\n[Agent Tool Call] -> {fn_name}")
            print(f"[Arguments] {json.dumps(fn_args, indent=2)}")

            try:
                result = await session.call_tool(fn_name, fn_args)
                tool_output_text = "\n".join([c.text for c in result.content if hasattr(c, "text")])
            except Exception as e:
                tool_output_text = json.dumps({"error": str(e)})

            print(f"[Tool Response] {tool_output_text[:200]}...")

            conversation_history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": fn_name,
                "content": tool_output_text
            })

        followup = client.chat.completions.create(
            model=model,
            messages=conversation_history,
            temperature=0.7
        )
        final_msg = followup.choices[0].message
        conversation_history.append({"role": "assistant", "content": final_msg.content})
        return final_msg.content
    else:
        return msg.content

async def main():
    print("=" * 65)
    print("  🚀 Creative Gmail Agent (Groq + Gmail MCP)")
    print("=" * 65)

    try:
        client, model = get_groq_client()
        print(f"[Active Model] {model}\n")
    except Exception as e:
        print(f"\n[Setup Error] {e}")
        return

    # First-time OAuth pre-check
    ensure_gmail_auth()

    # Prepare stdio connection parameters to Gmail MCP server
    python_exe = sys.executable
    server_params = StdioServerParameters(
        command=python_exe,
        args=[str(SERVER_SCRIPT)],
        env={**os.environ}
    )

    print("[MCP] Connecting to Gmail MCP Server...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"[MCP] Connected! Tools available: {[t.name for t in tools.tools]}\n")

            system_prompt = (
                "You are an AI Email Assistant with creative writing flair powered by Groq. "
                "You have access to tools via the Gmail MCP server: search_emails, read_email, create_draft, send_email. "
                "When crafting messages, write engaging, creative, polished, and polite emails tailored to the intent. "
                "Whenever writing replies, carefully read the email context first. "
                "Before sending emails directly, summarize the email or ask for confirmation unless the user explicitly requested immediate sending."
            )

            conversation_history = [{"role": "system", "content": system_prompt}]

            print("Agent is ready! You can say things like:")
            print(" - 'Check my last 3 unread emails and summarize them'")
            print(" - 'Compose a creative outreach email to someone@example.com proposing a collaboration'")
            print(" - 'Read email id <id> and draft a witty reply'")
            print(" - 'exit' to quit\n")

            while True:
                try:
                    user_input = input("\nYou > ").strip()
                    if not user_input:
                        continue
                    if user_input.lower() in ["exit", "quit", "q"]:
                        print("Goodbye!")
                        break

                    conversation_history.append({"role": "user", "content": user_input})
                    agent_response = await run_agent_turn(client, model, session, conversation_history)
                    print(f"\nAgent > {agent_response}")
                except (KeyboardInterrupt, EOFError):
                    print("\nExiting...")
                    break
                except Exception as ex:
                    print(f"\n[Error] {ex}")

if __name__ == "__main__":
    if "--legacy" in sys.argv or "--raw-mcp" in sys.argv:
        asyncio.run(main())
    else:
        from app.agents.email_agents.graph import interactive_cli
        interactive_cli()
