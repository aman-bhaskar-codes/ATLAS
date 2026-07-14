from typing import Any

from atlas.capabilities.domain.email import EmailMessage
from atlas.capabilities.providers.email.gmail import GmailProvider


# Dummy identity platform for the test
class DummyIdentity:
    async def get_usable_secret(self, key: str) -> str:
        return "fake_token"

def test_gmail_normalization() -> None:
    provider = GmailProvider(DummyIdentity(), "fake_key") # type: ignore
    
    raw_payload: dict[str, Any] = {
        "id": "msg_123",
        "threadId": "thread_123",
        "snippet": "This is a snippet",
        "labelIds": ["UNREAD", "INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Bob <bob@example.com>, charlie@example.com"},
                {"name": "Cc", "value": "Dave <dave@example.com>"},
                {"name": "Subject", "value": "Hello World"}
            ],
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": "PGh0bWw+... (ignored)"}
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": "VGhpcyBpcyB0aGUgYm9keQ=="} # "This is the body" in base64
                }
            ]
        }
    }
    
    msg: EmailMessage = provider._to_message(raw_payload)
    
    assert msg.id == "msg_123"
    assert msg.thread_id == "thread_123"
    assert msg.sender.email == "alice@example.com"
    assert msg.sender.name == "Alice"
    assert len(msg.to) == 2
    assert msg.to[0].email == "bob@example.com"
    assert msg.to[0].name == "Bob"
    assert msg.to[1].email == "charlie@example.com"
    assert len(msg.cc) == 1
    assert msg.cc[0].email == "dave@example.com"
    assert msg.subject == "Hello World"
    assert msg.snippet == "This is a snippet"
    assert msg.body_text == "This is the body"
    assert "UNREAD" in msg.labels
    assert "INBOX" in msg.labels
    assert msg.unread is True

