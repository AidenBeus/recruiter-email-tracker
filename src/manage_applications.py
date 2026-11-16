"""Command-line tool for registering applications before Gmail matching."""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from src.db import add_application, connect, reconcile_unassigned_emails


def database_path() -> str:
    return os.getenv("RECRUITER_DB_PATH", "data/recruiter_tracker.db")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Manage tracked job applications.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add = subparsers.add_parser("add", help="Add or update an application.")
    add.add_argument("--company", required=True)
    add.add_argument("--role", required=True)
    add.add_argument("--applied-at", help="ISO date or timestamp, e.g. 2026-11-10")
    add.add_argument("--notes")
    subparsers.add_parser("list", help="List applications.")
    subparsers.add_parser(
        "reconcile", help="Match already imported unassigned emails to applications."
    )
    args = parser.parse_args()

    with connect(database_path()) as connection:
        if args.command == "add":
            application_id = add_application(
                connection, args.company, args.role, args.applied_at, args.notes
            )
            matched = reconcile_unassigned_emails(connection)
            print(
                f"Saved application #{application_id}: {args.company} — {args.role}. "
                f"Matched {matched} previously unassigned email(s)."
            )
        elif args.command == "reconcile":
            matched = reconcile_unassigned_emails(connection)
            print(f"Matched {matched} previously unassigned email(s).")
        else:
            rows = connection.execute(
                """SELECT id, company, role, status, applied_at, updated_at
                   FROM applications ORDER BY updated_at DESC"""
            ).fetchall()
            if not rows:
                print("No applications yet. Use the add command first.")
            for row in rows:
                print(
                    f"#{row['id']} | {row['company']} | {row['role']} | "
                    f"{row['status']} | updated {row['updated_at']}"
                )


if __name__ == "__main__":
    main()
