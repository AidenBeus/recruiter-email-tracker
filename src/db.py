"""SQLite persistence for the recruiter email tracker."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


VALID_STATUSES = (
    "applied",
    "recruiter_contact",
    "screening",
    "interviewing",
    "waiting",
    "rejected",
    "offer",
    "withdrawn",
    "needs_review",
)


@contextmanager
def connect(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open and initialize a database connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize(connection)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied'
                CHECK (status IN ('applied', 'recruiter_contact', 'screening',
                    'interviewing', 'waiting', 'rejected', 'offer', 'withdrawn',
                    'needs_review')),
            applied_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            UNIQUE(company, role)
        );

        CREATE TABLE IF NOT EXISTS emails (
            gmail_message_id TEXT PRIMARY KEY,
            gmail_thread_id TEXT,
            application_id INTEGER,
            received_at TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            classification TEXT NOT NULL CHECK (classification IN (
                'applied', 'recruiter_contact', 'screening', 'interviewing',
                'waiting', 'rejected', 'offer', 'withdrawn', 'needs_review')),
            confidence REAL NOT NULL,
            classification_reason TEXT NOT NULL,
            ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (application_id) REFERENCES applications(id)
        );

        CREATE TABLE IF NOT EXISTS email_metadata (
            gmail_message_id TEXT PRIMARY KEY,
            gmail_thread_id TEXT,
            received_at TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            gmail_labels TEXT NOT NULL,
            relevance TEXT NOT NULL CHECK (relevance IN ('candidate', 'ignored')),
            filter_reason TEXT NOT NULL,
            inspected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY,
            application_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT NOT NULL,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            email_id TEXT UNIQUE,
            FOREIGN KEY (application_id) REFERENCES applications(id),
            FOREIGN KEY (email_id) REFERENCES emails(gmail_message_id)
        );

        CREATE TABLE IF NOT EXISTS pipeline_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_emails_application_id ON emails(application_id);
        CREATE INDEX IF NOT EXISTS idx_emails_received_at ON emails(received_at);
        CREATE INDEX IF NOT EXISTS idx_metadata_received_at ON email_metadata(received_at);
        """
    )


def record_email_metadata(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    thread_id: str | None,
    received_at: str,
    sender: str,
    subject: str,
    labels: str,
    relevance: str,
    filter_reason: str,
) -> None:
    """Save non-sensitive message metadata for every unique Gmail message."""
    connection.execute(
        """
        INSERT INTO email_metadata (
            gmail_message_id, gmail_thread_id, received_at, sender, subject,
            gmail_labels, relevance, filter_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_message_id) DO UPDATE SET
            gmail_thread_id = excluded.gmail_thread_id,
            gmail_labels = excluded.gmail_labels,
            relevance = excluded.relevance,
            filter_reason = excluded.filter_reason,
            inspected_at = CURRENT_TIMESTAMP
        """,
        (message_id, thread_id, received_at, sender, subject, labels, relevance,
         filter_reason),
    )


def add_application(
    connection: sqlite3.Connection,
    company: str,
    role: str,
    applied_at: str | None = None,
    notes: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO applications (company, role, applied_at, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(company, role) DO UPDATE SET
            applied_at = COALESCE(excluded.applied_at, applications.applied_at),
            notes = COALESCE(excluded.notes, applications.notes),
            updated_at = CURRENT_TIMESTAMP
        """,
        (company.strip(), role.strip(), applied_at, notes),
    )
    row = connection.execute(
        "SELECT id FROM applications WHERE company = ? AND role = ?",
        (company.strip(), role.strip()),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def find_application_for_email(
    connection: sqlite3.Connection, sender: str, subject: str, body: str
) -> sqlite3.Row | None:
    """Match a known company name against email text; never guesses a new application."""
    haystack = f"{sender}\n{subject}\n{body}".lower()
    applications = connection.execute(
        "SELECT * FROM applications ORDER BY updated_at DESC"
    ).fetchall()
    for application in applications:
        company = application["company"].strip().lower()
        if len(company) >= 3 and company in haystack:
            return application
    return None


def insert_email(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    thread_id: str | None,
    received_at: str,
    sender: str,
    subject: str,
    body: str,
    classification: str,
    confidence: float,
    reason: str,
) -> bool:
    """Insert once by immutable Gmail message id and update a matched application."""
    application = find_application_for_email(connection, sender, subject, body)
    application_id = application["id"] if application else None
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO emails (
            gmail_message_id, gmail_thread_id, application_id, received_at, sender,
            subject, body, classification, confidence, classification_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, thread_id, application_id, received_at, sender, subject, body,
         classification, confidence, reason),
    )
    if cursor.rowcount == 0:
        return False

    if application and confidence >= 0.8 and classification != "needs_review":
        old_status = application["status"]
        if old_status != classification:
            connection.execute(
                """UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (classification, application_id),
            )
            connection.execute(
                """INSERT INTO status_history
                   (application_id, old_status, new_status, email_id)
                   VALUES (?, ?, ?, ?)""",
                (application_id, old_status, classification, message_id),
            )
    return True


def reconcile_unassigned_emails(connection: sqlite3.Connection) -> int:
    """Link previously imported emails after applications are registered or corrected."""
    rows = connection.execute(
        """SELECT gmail_message_id, sender, subject, body, classification, confidence
           FROM emails WHERE application_id IS NULL ORDER BY received_at"""
    ).fetchall()
    matched = 0
    for email in rows:
        application = find_application_for_email(
            connection, email["sender"], email["subject"], email["body"]
        )
        if application is None:
            continue
        application_id = application["id"]
        connection.execute(
            "UPDATE emails SET application_id = ? WHERE gmail_message_id = ?",
            (application_id, email["gmail_message_id"]),
        )
        if email["confidence"] >= 0.8 and email["classification"] != "needs_review":
            old_status = application["status"]
            new_status = email["classification"]
            if old_status != new_status:
                connection.execute(
                    """UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (new_status, application_id),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO status_history
                       (application_id, old_status, new_status, email_id)
                       VALUES (?, ?, ?, ?)""",
                    (application_id, old_status, new_status, email["gmail_message_id"]),
                )
        matched += 1
    return matched


def assign_email_to_application(
    connection: sqlite3.Connection,
    message_id: str,
    application_id: int,
    apply_detected_status: bool = False,
) -> None:
    """Manually link one imported email to an application.

    A manual link does not change status unless the caller explicitly requests a
    high-confidence detected status be applied as well.
    """
    email = connection.execute(
        """SELECT gmail_message_id, classification, confidence
           FROM emails WHERE gmail_message_id = ?""",
        (message_id,),
    ).fetchone()
    application = connection.execute(
        "SELECT id, status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    if email is None or application is None:
        raise ValueError("Email or application no longer exists.")
    connection.execute(
        "UPDATE emails SET application_id = ? WHERE gmail_message_id = ?",
        (application_id, message_id),
    )
    if (
        apply_detected_status
        and email["confidence"] >= 0.8
        and email["classification"] != "needs_review"
        and email["classification"] != application["status"]
    ):
        connection.execute(
            """UPDATE applications SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (email["classification"], application_id),
        )
        connection.execute(
            """INSERT OR IGNORE INTO status_history
               (application_id, old_status, new_status, email_id)
               VALUES (?, ?, ?, ?)""",
            (application_id, application["status"], email["classification"], message_id),
        )
