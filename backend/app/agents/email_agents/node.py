import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from groq import Groq
from app.agents.email_agents.state import EmailAgentState, EmailDraft
from app.agents.email_agents.prompts import (
    ROUTER_PROMPT,
    READER_PROMPT,
    COMPOSER_PROMPT,
    REPLY_PROMPT,
    FOLLOWUP_PROMPT,
    REVISE_PROMPT,
)
from app.mcp import gmail_mcp_server
from langsmith import traceable

# Traced MCP Tool wrappers for LangSmith visibility
mcp_search_emails = traceable(gmail_mcp_server.search_emails, name="gmail_search_emails", run_type="tool")
mcp_read_email = traceable(gmail_mcp_server.read_email, name="gmail_read_email", run_type="tool")
mcp_read_thread = traceable(gmail_mcp_server.read_thread, name="gmail_read_thread", run_type="tool")
mcp_get_followup_candidates = traceable(gmail_mcp_server.get_followup_candidates, name="gmail_get_followup_candidates", run_type="tool")
mcp_send_email = traceable(gmail_mcp_server.send_email, name="gmail_send_email", run_type="tool")
mcp_reply_to_email = traceable(gmail_mcp_server.reply_to_email, name="gmail_reply_to_email", run_type="tool")


def print_log(agent: str, message: str):
    """Outputs a clean, distinct log line in the command prompt."""
    print(f"[{agent}] {message}")


def format_draft_box(title: str, draft: Dict[str, Any], subtitle: str = "") -> str:
    """Creates a clean, readable ASCII box for CMD terminal."""
    divider = "=" * 70
    subdivider = "-" * 70
    extra = f"{subtitle}\n" if subtitle else ""
    return (
        f"\n{divider}\n"
        f"  {title}\n"
        f"{divider}\n"
        f"To:      {draft.get('to', '')}\n"
        f"Subject: {draft.get('subject', '')}\n"
        f"{extra}"
        f"{subdivider}\n"
        f"{draft.get('body', '').strip()}\n"
        f"{divider}\n"
        f"👉 ASSISTANCE / APPROVAL NEEDED:\n"
        f"   • Type 'yes' or 'send' to confirm and send\n"
        f"   • Type your adjustments (e.g. 'make it shorter', 'change subject')\n"
        f"   • Type 'cancel' to discard this draft\n"
        f"{divider}"
    )


from app.core.LLMConfig import call_llm_json, call_llm_text, get_groq_client
from app.core.database import get_db_connection


# ==========================================
# LANGGRAPH NODES
# ==========================================

def router_node(state: EmailAgentState) -> Dict[str, Any]:
    """Analyzes the user's latest message and decides intent and parameters."""
    print_log("Router", "Analyzing user request...")
    messages = state.get("messages", [])
    last_user_msg = messages[-1]["content"] if messages else ""

    pending_draft = state.get("draft")
    draft_info = json.dumps(pending_draft, indent=2) if pending_draft else "None"

    prompt = ROUTER_PROMPT.format(pending_draft_info=draft_info)
    routing = call_llm_json(prompt, f"User message: {last_user_msg}")

    intent = routing.get("intent", "chat")
    search_query = routing.get("search_query") or state.get("search_query")
    user_notes = routing.get("notes", last_user_msg)

    print_log("Router", f"Target Intent -> [{intent.upper()}]")

    # If pending approval exists and intent is approve/revise/reject
    if pending_draft:
        if intent == "approve":
            print_log("Router", "Approval detected from user. Proceeding to sender...")
            return {
                "intent": "approve",
                "approval_status": "approved"
            }
        elif intent == "revise":
            print_log("Router", f"Revision requested: '{user_notes}'. Handing to editor...")
            return {
                "intent": "revise",
                "approval_status": "revised",
                "user_feedback": user_notes
            }
        elif intent == "reject":
            print_log("Router", "User cancelled the draft.")
            return {
                "intent": "reject",
                "draft": None,
                "approval_status": "rejected",
                "final_response": "❌ Draft has been discarded. The email was not sent."
            }

    return {
        "intent": intent,
        "search_query": search_query,
        "user_feedback": user_notes
    }


def read_emails_node(state: EmailAgentState) -> Dict[str, Any]:
    """Searches and reads emails using Gmail MCP tools and produces a readable summary."""
    query = state.get("search_query") or "is:unread"
    print_log("Reader Agent", f"Searching Gmail with query: '{query}'...")
    raw_emails = mcp_search_emails(query=query, max_results=5)

    if not raw_emails or (len(raw_emails) == 1 and "error" in raw_emails[0]):
        error_msg = raw_emails[0].get("error") if raw_emails else "No emails found"
        print_log("Reader Agent", f"No emails found ({error_msg}).")
        return {
            "emails": [],
            "final_response": f"ℹ️ Could not find any emails matching query '{query}'. ({error_msg})"
        }

    print_log("Reader Agent", f"Found {len(raw_emails)} message(s). Reading details...")
    detailed_emails = []
    for em in raw_emails[:3]:
        msg_id = em.get("id")
        if msg_id:
            full = mcp_read_email(msg_id)
            if "error" not in full:
                detailed_emails.append(full)
            else:
                detailed_emails.append(em)
        else:
            detailed_emails.append(em)

    print_log("Reader Agent", "Formatting inbox summary...")
    summary = call_llm_text(
        READER_PROMPT.format(email_data=json.dumps(detailed_emails, indent=2)),
        "Summarize these emails clearly."
    )

    divider = "=" * 70
    boxed_summary = (
        f"\n{divider}\n"
        f"  📬 INBOX / EMAIL SUMMARY (Query: {query})\n"
        f"{divider}\n"
        f"{summary.strip()}\n"
        f"{divider}"
    )

    return {
        "emails": detailed_emails,
        "final_response": boxed_summary
    }


def draft_email_node(state: EmailAgentState) -> Dict[str, Any]:
    """Composes a new email draft and requests human assistance/confirmation."""
    print_log("Composer Agent", "Drafting new email based on instructions...")
    messages = state.get("messages", [])
    user_msg = messages[-1]["content"] if messages else ""
    feedback = state.get("user_feedback") or user_msg

    prompt = COMPOSER_PROMPT.format(
        instructions=feedback,
        recipient="",
        subject=""
    )
    draft_data = call_llm_json(prompt, f"Draft request: {user_msg}")

    draft = {
        "action_type": "send",
        "to": draft_data.get("to", ""),
        "subject": draft_data.get("subject", "(No Subject)"),
        "body": draft_data.get("body", "")
    }

    print_log("Composer Agent", f"Draft generated for: {draft['to']}. Requesting user assistance...")
    preview_message = format_draft_box(
        "📝 NEW EMAIL DRAFT (AWAITING YOUR APPROVAL)",
        draft
    )

    return {
        "draft": draft,
        "approval_status": "pending",
        "final_response": preview_message
    }


def draft_reply_node(state: EmailAgentState) -> Dict[str, Any]:
    """Fetches thread context, drafts a context-aware reply, and requests human assistance."""
    print_log("Replier Agent", "Locating email thread to formulate response...")
    messages = state.get("messages", [])
    user_msg = messages[-1]["content"] if messages else ""
    feedback = state.get("user_feedback") or user_msg

    emails = state.get("emails", [])
    target_msg = None

    if emails:
        target_msg = emails[0]
    else:
        results = mcp_search_emails(query="is:unread", max_results=3)
        if not results or "error" in results[0]:
            results = mcp_search_emails(query="", max_results=3)
        if results and "error" not in results[0]:
            target_msg = mcp_read_email(results[0]["id"])

    if not target_msg or "error" in target_msg:
        print_log("Replier Agent", "Could not locate target email to reply to.")
        return {
            "final_response": "⚠️ Could not find an email to reply to. Please specify the email subject, sender, or message ID."
        }

    thread_id = target_msg.get("threadId")
    thread_context = target_msg
    if thread_id:
        print_log("Replier Agent", f"Reading full thread history for thread ID: {thread_id}...")
        t_data = mcp_read_thread(thread_id)
        if "error" not in t_data:
            thread_context = t_data

    print_log("Replier Agent", "Drafting contextual reply...")
    prompt = REPLY_PROMPT.format(
        thread_context=json.dumps(thread_context, indent=2),
        instructions=feedback
    )
    reply_data = call_llm_json(prompt, f"Compose reply for message {target_msg.get('id')}")

    target_to = reply_data.get("to") or target_msg.get("from") or target_msg.get("From")
    subject = reply_data.get("subject") or f"Re: {target_msg.get('subject', '')}"
    body = reply_data.get("body", "")

    draft = {
        "action_type": "reply",
        "to": target_to,
        "subject": subject,
        "body": body,
        "thread_id": thread_id,
        "message_id": target_msg.get("id")
    }

    print_log("Replier Agent", "Reply drafted. Requesting user assistance...")
    preview_message = format_draft_box(
        "✉️ CONTEXTUAL REPLY DRAFT (AWAITING YOUR APPROVAL)",
        draft,
        subtitle=f"In-Reply-To: {target_msg.get('subject', '(No Subject)')} (From: {target_msg.get('from', 'Unknown')})"
    )

    return {
        "draft": draft,
        "approval_status": "pending",
        "final_response": preview_message
    }


def detect_followups_node(state: EmailAgentState) -> Dict[str, Any]:
    """Finds sent emails waiting for a response, drafts a follow-up, and seeks human confirmation."""
    print_log("Follow-up Agent", "Scanning sent emails waiting for a response...")
    candidates = mcp_get_followup_candidates(max_results=5)

    if not candidates or (len(candidates) == 1 and "error" in candidates[0]):
        print_log("Follow-up Agent", "No pending threads found.")
        return {
            "final_response": "🎉 Great news! You have no pending sent emails that are currently awaiting a follow-up."
        }

    candidate = candidates[0]
    thread_id = candidate.get("threadId")
    print_log("Follow-up Agent", f"Candidate identified: To {candidate.get('to')} on '{candidate.get('subject')}'")

    thread_info = mcp_read_thread(thread_id) if thread_id else candidate
    user_msg = state.get("messages", [])[-1]["content"] if state.get("messages") else ""

    print_log("Follow-up Agent", "Drafting polite reminder...")
    prompt = FOLLOWUP_PROMPT.format(
        thread_context=json.dumps(thread_info, indent=2),
        instructions=user_msg
    )
    followup_data = call_llm_json(prompt, f"Draft polite follow-up for thread {thread_id}")

    draft = {
        "action_type": "followup",
        "to": followup_data.get("to") or candidate.get("to", ""),
        "subject": followup_data.get("subject") or f"Follow-up: {candidate.get('subject', '')}",
        "body": followup_data.get("body", ""),
        "thread_id": thread_id,
        "message_id": candidate.get("last_message_id")
    }

    print_log("Follow-up Agent", "Follow-up drafted. Requesting user assistance...")
    preview_message = format_draft_box(
        "🔔 PROPOSED FOLLOW-UP DRAFT (AWAITING YOUR APPROVAL)",
        draft,
        subtitle=f"Original sent to: {candidate.get('to')} on {candidate.get('last_sent_date')}"
    )

    return {
        "draft": draft,
        "approval_status": "pending",
        "final_response": preview_message
    }


def revise_draft_node(state: EmailAgentState) -> Dict[str, Any]:
    """Applies user feedback/edits to an existing draft and re-presents for approval."""
    current_draft = state.get("draft")
    if not current_draft:
        return {
            "final_response": "There is currently no active draft to revise."
        }

    feedback = state.get("user_feedback", "Make improvements as requested.")
    print_log("Editor Agent", f"Applying revisions based on feedback: '{feedback}'...")

    prompt = REVISE_PROMPT.format(
        to=current_draft.get("to", ""),
        subject=current_draft.get("subject", ""),
        body=current_draft.get("body", ""),
        feedback=feedback
    )
    revised = call_llm_json(prompt, feedback)

    updated_draft = {
        **current_draft,
        "to": revised.get("to") or current_draft.get("to"),
        "subject": revised.get("subject") or current_draft.get("subject"),
        "body": revised.get("body") or current_draft.get("body")
    }

    print_log("Editor Agent", "Revision complete. Presenting updated draft...")
    preview_message = format_draft_box(
        "✏️ REVISED EMAIL DRAFT (AWAITING YOUR CONFIRMATION)",
        updated_draft
    )

    return {
        "draft": updated_draft,
        "approval_status": "pending",
        "final_response": preview_message
    }


def send_email_node(state: EmailAgentState) -> Dict[str, Any]:
    """Sends the approved draft via Gmail MCP after human confirmation."""
    approval_status = state.get("approval_status")
    draft = state.get("draft")

    if approval_status != "approved" or not draft:
        print_log("Sender Agent", "⚠️ Action blocked: Draft has not been confirmed by user.")
        return {
            "final_response": "⚠️ Cannot send email: Draft has not been confirmed by you."
        }

    action_type = draft.get("action_type", "send")
    to = draft.get("to", "")
    subject = draft.get("subject", "")
    body = draft.get("body", "")
    message_id = draft.get("message_id")

    print_log("Sender Agent", f"Dispatching email ({action_type}) to {to} via Gmail API...")

    try:
        if action_type in ["reply", "followup"] and message_id:
            result = mcp_reply_to_email(
                message_id=message_id,
                body=body,
                to=to,
                subject=subject
            )
        else:
            result = mcp_send_email(
                to=to,
                subject=subject,
                body=body
            )

        if result.get("status") == "success":
            print_log("Sender Agent", "✅ Email sent successfully!")
            try:
                with get_db_connection() as conn:
                    conn.execute(
                        "INSERT INTO email_actions (action_type, recipient, subject, body, thread_id, message_id, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (action_type, to, subject, body, draft.get("thread_id"), message_id, "sent")
                    )
            except Exception:
                pass
            divider = "=" * 70
            success_msg = (
                f"\n{divider}\n"
                f"  ✅ EMAIL SENT SUCCESSFULLY!\n"
                f"{divider}\n"
                f"To:      {to}\n"
                f"Subject: {subject}\n"
                f"{divider}"
            )
            return {
                "draft": None,
                "approval_status": None,
                "result": result,
                "final_response": success_msg
            }
        else:
            err = result.get("error", "Unknown error")
            print_log("Sender Agent", f"❌ Send failed: {err}")
            return {
                "final_response": f"❌ Failed to send email: {err}"
            }
    except Exception as e:
        print_log("Sender Agent", f"❌ Error occurred: {str(e)}")
        return {
            "final_response": f"❌ Error occurred while sending email: {str(e)}"
        }


def chat_node(state: EmailAgentState) -> Dict[str, Any]:
    """Handles general conversational queries about email assistance."""
    print_log("Assistant", "Responding to general query...")
    messages = state.get("messages", [])
    user_msg = messages[-1]["content"] if messages else ""

    system_prompt = (
        "You are an AI Email Assistant. You help users read incoming emails, "
        "compose and send new emails with human review, draft context-aware replies with approval, "
        "and track/follow-up on pending conversations. Let the user know what you can do politely."
    )
    reply = call_llm_text(system_prompt, user_msg)
    return {"final_response": reply}
