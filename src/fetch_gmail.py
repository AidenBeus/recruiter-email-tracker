"""Ingest labeled recruiter mail from Gmail into the local SQLite database."""

from __future__ import annotations

import argparse
import base64
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.db import connect, insert_email, record_email_metadata
from src.parse_email import classify_email, is_recruiting_candidate

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def gmail_service(client_secrets: Path, token_file: Path):
    """Authenticate locally. The browser consent screen appears only when needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not client_secrets.exists():
            raise FileNotFoundError(
                f"Could not find {client_secrets}. Download an OAuth Desktop App "
                "credential from Google Cloud and save it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
        credentials = flow.run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credentials)


def header(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    return next(
        (item["value"] for item in headers if item["name"].lower() == name.lower()), ""
    )


def decode_body(encoded: str) -> str:
    if not encoded:
        return ""
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode(
        "utf-8", errors="replace"
    )


def body_from_payload(payload: dict[str, Any]) -> str:
    """Prefer plain text, including nested multipart messages."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        return decode_body(payload.get("body", {}).get("data", ""))
    children = payload.get("parts", [])
    for part in children:
        body = body_from_payload(part)
        if body:
            return body
    return ""


def execute_with_backoff(request: Any, request_delay: float) -> Any:
    """Execute Gmail calls gently and recover from temporary quota/server errors."""
    from googleapiclient.errors import HttpError

    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            response = request.execute()
            if request_delay:
                time.sleep(request_delay)
            return response
        except HttpError as error:
            retryable = error.resp.status in (403, 429, 500, 502, 503, 504)
            if not retryable or attempt == max_attempts - 1:
                raise
            delay = min(2**attempt + random.uniform(0, 1), 32)
            print(
                f"Gmail temporarily rate-limited the request ({error.resp.status}); "
                f"retrying in {delay:.1f}s ({attempt + 1}/{max_attempts - 1})..."
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable")


def message_ids(service: Any, query: str, request_delay: float):
    page_token = None
    while True:
        response = execute_with_backoff(service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ), request_delay)
        yield from (item["id"] for item in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Store Gmail metadata and retain full bodies only for recruiting candidates."
    )
    parser.add_argument(
        "--query",
        default=os.getenv("GMAIL_QUERY", "in:all"),
        help="Gmail search query; defaults to GMAIL_QUERY or in:all.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Classify but do not store emails.")
    parser.add_argument(
        "--request-delay", type=float,
        default=float(os.getenv("GMAIL_REQUEST_DELAY_SECONDS", "1.0")),
        help="Minimum seconds between Gmail API calls (default: 1.0).",
    )
    args = parser.parse_args()

    service = gmail_service(
        Path(os.getenv("GOOGLE_CLIENT_SECRETS_FILE", "credentials.json")),
        Path(os.getenv("GOOGLE_TOKEN_FILE", "token.json")),
    )
    database = os.getenv("RECRUITER_DB_PATH", "data/recruiter_tracker.db")
    scanned = candidates = inserted = duplicates = 0
    with connect(database) as connection:
        known_companies = tuple(
            row["company"]
            for row in connection.execute("SELECT company FROM applications").fetchall()
        )
        for message_id in message_ids(service, args.query, args.request_delay):
            # Metadata mode deliberately avoids retrieving the message body for every email.
            metadata = execute_with_backoff(service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "Subject"],
            ), args.request_delay)
            scanned += 1
            received_at = datetime.fromtimestamp(
                int(metadata["internalDate"]) / 1000, tz=UTC
            ).isoformat()
            sender = header(metadata, "From")
            subject = header(metadata, "Subject") or "(no subject)"
            candidate, candidate_reason = is_recruiting_candidate(
                sender, subject, known_companies
            )
            if not args.dry_run:
                record_email_metadata(
                    connection,
                    message_id=metadata["id"],
                    thread_id=metadata.get("threadId"),
                    received_at=received_at,
                    sender=sender,
                    subject=subject,
                    labels=",".join(metadata.get("labelIds", [])),
                    relevance="candidate" if candidate else "ignored",
                    filter_reason=candidate_reason,
                )
            if not candidate:
                continue
            candidates += 1
            message = execute_with_backoff(service.users().messages().get(
                userId="me", id=message_id, format="full"
            ), args.request_delay)
            body = body_from_payload(message.get("payload", {}))
            result = classify_email(subject, body)
            if args.dry_run:
                print(f"{received_at} | {result.status:18} | {subject}")
                continue
            did_insert = insert_email(
                connection,
                message_id=message["id"],
                thread_id=message.get("threadId"),
                received_at=received_at,
                sender=sender,
                subject=subject,
                body=body,
                classification=result.status,
                confidence=result.confidence,
                reason=result.reason,
            )
            inserted += int(did_insert)
            duplicates += int(not did_insert)
        if not args.dry_run:
            connection.execute(
                """INSERT INTO pipeline_state (key, value) VALUES ('last_success_at', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                   updated_at = CURRENT_TIMESTAMP""",
                (datetime.now(tz=UTC).isoformat(),),
            )
    if args.dry_run:
        print(
            f"Dry run complete: scanned metadata for {scanned} email(s), "
            f"with {candidates} recruiting candidate(s); no data was stored."
        )
    else:
        print(
            f"Ingestion complete: stored metadata for {scanned}, inspected {candidates} "
            f"candidate(s), inserted {inserted} full email(s), duplicates skipped {duplicates}."
        )


if __name__ == "__main__":
    main()
