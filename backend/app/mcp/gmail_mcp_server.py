import os
import base64
from email.message import EmailMessage
from typing import Optional, List, Dict, Any
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
try:
    from mcp.server import MCPServer
except (ImportError, ModuleNotFoundError):
    try:
        from mcp.server.mcpserver import MCPServer
    except (ImportError, ModuleNotFoundError):
        from mcp.server.fastmcp import FastMCP as MCPServer

# Define Google OAuth Scopes for Gmail
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose'
]

APP_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = APP_DIR.parent.parent

def find_credentials_file() -> Path:
    """Finds credentials.json or client_secret_*.json in app/mcp or backend root."""
    candidates = [
        APP_DIR / "credentials.json",
        BACKEND_DIR / "credentials.json",
    ]
    # Check for client_secret_*.json
    for path in list(APP_DIR.glob("client_secret_*.json")) + list(BACKEND_DIR.glob("client_secret_*.json")):
        candidates.append(path)

    for cand in candidates:
        if cand.exists():
            return cand

    raise FileNotFoundError(
        f"OAuth client secret JSON file not found in {APP_DIR} or {BACKEND_DIR}. "
        "Please download client secret JSON from Google Cloud Console."
    )

def find_token_file() -> Path:
    if (APP_DIR / "token.json").exists():
        return APP_DIR / "token.json"
    if (BACKEND_DIR / "token.json").exists():
        return BACKEND_DIR / "token.json"
    return APP_DIR / "token.json"

# Initialize MCP Server
mcp = MCPServer("Gmail MCP Server")

def get_gmail_service():
    """Authenticates and returns the Gmail API client service."""
    token_file = find_token_file()
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds_file = find_credentials_file()
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for future runs
        with open(token_file, 'w', encoding='utf-8') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

def _extract_body(payload: Dict[str, Any]) -> str:
    """Helper to parse plain text body from Gmail message payload."""
    if 'parts' in payload:
        for part in payload['parts']:
            mime_type = part.get('mimeType', '')
            if mime_type == 'text/plain' and 'data' in part.get('body', {}):
                data = part['body']['data']
                return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            elif 'parts' in part:
                sub_body = _extract_body(part)
                if sub_body:
                    return sub_body
    if 'data' in payload.get('body', {}):
        data = payload['body']['data']
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return ""

@mcp.tool()
def search_emails(query: str = "is:unread", max_results: int = 5) -> List[Dict[str, Any]]:
    """Search for emails matching a Gmail query (e.g., 'is:unread', 'from:john@example.com', 'subject:meeting')."""
    try:
        service = get_gmail_service()
        results = service.users().messages().list(
            userId='me', q=query, maxResults=max_results
        ).execute()

        messages = results.get('messages', [])
        summaries = []

        for msg in messages:
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['Subject', 'From', 'Date', 'To']
            ).execute()

            headers = {h['name']: h['value'] for h in msg_data.get('payload', {}).get('headers', [])}
            summaries.append({
                "id": msg['id'],
                "threadId": msg.get('threadId'),
                "from": headers.get('From', 'Unknown'),
                "to": headers.get('To', 'Unknown'),
                "subject": headers.get('Subject', '(No Subject)'),
                "date": headers.get('Date', 'Unknown'),
                "snippet": msg_data.get('snippet', '')
            })

        return summaries
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
def read_email(message_id: str) -> Dict[str, Any]:
    """Read the full content and details of a specific email by its message ID."""
    try:
        service = get_gmail_service()
        msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
        body = _extract_body(msg.get('payload', {})) or msg.get('snippet', '')

        return {
            "id": msg['id'],
            "threadId": msg.get('threadId'),
            "from": headers.get('From', 'Unknown'),
            "to": headers.get('To', 'Unknown'),
            "subject": headers.get('Subject', '(No Subject)'),
            "date": headers.get('Date', 'Unknown'),
            "body": body.strip()
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def create_draft(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Create a draft email in your Gmail account so you can review it before sending."""
    try:
        service = get_gmail_service()
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft_body = {'message': {'raw': encoded_message}}
        draft = service.users().drafts().create(userId='me', body=draft_body).execute()

        return {
            "status": "success",
            "draft_id": draft.get('id'),
            "message": f"Draft created successfully for {to} with subject '{subject}'."
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def send_email(to: str, subject: str, body: str) -> Dict[str, Any]:
    """Send an email immediately using your Gmail account."""
    try:
        service = get_gmail_service()
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to
        message['Subject'] = subject

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent_msg = service.users().messages().send(userId='me', body={'raw': encoded_message}).execute()

        return {
            "status": "success",
            "message_id": sent_msg.get('id'),
            "message": f"Email successfully sent to {to} with subject '{subject}'."
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def read_thread(thread_id: str) -> Dict[str, Any]:
    """Read all messages in an email thread to get the complete conversation history."""
    try:
        service = get_gmail_service()
        thread_data = service.users().threads().get(userId='me', id=thread_id, format='full').execute()
        messages_out = []
        for msg in thread_data.get('messages', []):
            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}
            body = _extract_body(msg.get('payload', {})) or msg.get('snippet', '')
            messages_out.append({
                "id": msg['id'],
                "threadId": msg.get('threadId'),
                "from": headers.get('From', 'Unknown'),
                "to": headers.get('To', 'Unknown'),
                "subject": headers.get('Subject', '(No Subject)'),
                "date": headers.get('Date', 'Unknown'),
                "message_id_header": headers.get('Message-ID', headers.get('Message-Id', '')),
                "body": body.strip()
            })
        return {
            "threadId": thread_id,
            "message_count": len(messages_out),
            "messages": messages_out
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def reply_to_email(
    message_id: str,
    body: str,
    to: Optional[str] = None,
    subject: Optional[str] = None
) -> Dict[str, Any]:
    """Reply to an existing email message while preserving thread context (In-Reply-To, References, and Subject)."""
    try:
        service = get_gmail_service()
        orig_msg = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        headers = {h['name']: h['value'] for h in orig_msg.get('payload', {}).get('headers', [])}

        target_to = to or headers.get('Reply-To') or headers.get('From')
        orig_subject = headers.get('Subject', '')
        if not subject:
            subject = orig_subject if orig_subject.lower().startswith('re:') else f"Re: {orig_subject}"
        
        orig_msg_id = headers.get('Message-ID', headers.get('Message-Id', ''))
        orig_references = headers.get('References', '')
        references = f"{orig_references} {orig_msg_id}".strip() if orig_references else orig_msg_id
        thread_id = orig_msg.get('threadId')

        message = EmailMessage()
        message.set_content(body)
        message['To'] = target_to
        message['Subject'] = subject
        if orig_msg_id:
            message['In-Reply-To'] = orig_msg_id
        if references:
            message['References'] = references

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_body: Dict[str, Any] = {'raw': encoded_message}
        if thread_id:
            send_body['threadId'] = thread_id

        sent_msg = service.users().messages().send(userId='me', body=send_body).execute()
        return {
            "status": "success",
            "message_id": sent_msg.get('id'),
            "threadId": sent_msg.get('threadId'),
            "to": target_to,
            "subject": subject,
            "message": f"Reply successfully sent to {target_to} for thread {thread_id}."
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
def get_followup_candidates(max_results: int = 10) -> List[Dict[str, Any]]:
    """Scan recent sent emails where the conversation has not received a subsequent reply."""
    try:
        service = get_gmail_service()
        user_profile = service.users().getProfile(userId='me').execute()
        my_email = user_profile.get('emailAddress', '').lower()

        # Search for messages sent by me
        results = service.users().messages().list(
            userId='me', q="from:me", maxResults=max_results
        ).execute()

        messages = results.get('messages', [])
        candidates = []
        seen_threads = set()

        for msg in messages:
            thread_id = msg.get('threadId')
            if not thread_id or thread_id in seen_threads:
                continue
            seen_threads.add(thread_id)

            thread_data = service.users().threads().get(userId='me', id=thread_id, format='full').execute()
            t_messages = thread_data.get('messages', [])
            if not t_messages:
                continue

            # Check the last message in this thread
            last_msg = t_messages[-1]
            last_headers = {h['name']: h['value'] for h in last_msg.get('payload', {}).get('headers', [])}
            last_from = last_headers.get('From', '').lower()

            # If the last message was sent by me, then we are waiting on a reply!
            if my_email and my_email in last_from or "me" in last_from:
                first_headers = {h['name']: h['value'] for h in t_messages[0].get('payload', {}).get('headers', [])}
                candidates.append({
                    "threadId": thread_id,
                    "last_message_id": last_msg['id'],
                    "to": last_headers.get('To', 'Unknown'),
                    "subject": first_headers.get('Subject', '(No Subject)'),
                    "last_sent_date": last_headers.get('Date', 'Unknown'),
                    "snippet": last_msg.get('snippet', ''),
                    "total_messages_in_thread": len(t_messages)
                })

        return candidates
    except Exception as e:
        return [{"error": str(e)}]

if __name__ == "__main__":
    mcp.run()
