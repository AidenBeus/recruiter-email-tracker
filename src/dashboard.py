"""A local Streamlit dashboard for applications and their email timeline."""

from __future__ import annotations

import os
import sqlite3

import streamlit as st
from dotenv import load_dotenv

# Streamlit runs this file as a script, so its directory (src/) is on
# sys.path rather than the project root.
from db import add_application, assign_email_to_application, connect, initialize, reconcile_unassigned_emails

load_dotenv()
DATABASE = os.getenv("RECRUITER_DB_PATH", "data/recruiter_tracker.db")


def query(sql: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        initialize(connection)
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


st.set_page_config(page_title="Recruiter Email Tracker", page_icon="✉️", layout="wide")
st.title("Recruiter Email Tracker")
st.caption("Emails with no confident application match remain unassigned for your review.")

with st.sidebar:
    st.header("Add application")
    with st.form("add_application_form", clear_on_submit=True):
        company = st.text_input("Company", placeholder="Example: Harris Computer")
        role = st.text_input("Role", placeholder="Example: Quality Assurance Analyst")
        applied_at = st.date_input("Applied date", value=None)
        notes = st.text_area("Notes (optional)")
        submitted = st.form_submit_button("Add application")
    if submitted:
        if not company.strip() or not role.strip():
            st.error("Company and role are required.")
        else:
            with connect(DATABASE) as connection:
                add_application(
                    connection,
                    company=company,
                    role=role,
                    applied_at=applied_at.isoformat() if applied_at else None,
                    notes=notes or None,
                )
                matched = reconcile_unassigned_emails(connection)
            st.success(f"Saved application. Matched {matched} previously unassigned email(s).")
            st.rerun()

try:
    applications = [dict(row) for row in query(
        """SELECT a.*, MAX(e.received_at) AS last_email_at
           FROM applications a LEFT JOIN emails e ON e.application_id = a.id
           GROUP BY a.id ORDER BY a.updated_at DESC"""
    )]
except sqlite3.OperationalError:
    st.info("No database yet. Add an application and run the Gmail importer first.")
    st.stop()

if not applications:
    st.info("No applications yet. Run `python -m src.manage_applications add ...` first.")
    st.stop()

counts: dict[str, int] = {}
for app in applications:
    counts[app["status"]] = counts.get(app["status"], 0) + 1
st.bar_chart(counts, horizontal=True)
st.dataframe(
    applications,
    hide_index=True,
    use_container_width=True,
    column_order=("company", "role", "status", "applied_at", "last_email_at", "updated_at", "notes"),
)

selected = st.selectbox(
    "Application timeline", applications,
    format_func=lambda app: f"{app['company']} — {app['role']}",
)
timeline = query(
    f"""SELECT received_at, sender, subject, classification, confidence, classification_reason
        FROM emails WHERE application_id = {int(selected['id'])}
        ORDER BY received_at DESC"""
)
if timeline:
    st.dataframe([dict(row) for row in timeline], hide_index=True, use_container_width=True)
else:
    st.info("No matched emails yet. Ensure the company name appears in the email text.")

unassigned = [dict(row) for row in query(
    """SELECT gmail_message_id, received_at, sender, subject, classification, confidence,
              classification_reason
       FROM emails WHERE application_id IS NULL ORDER BY received_at DESC LIMIT 50"""
)]
with st.expander(f"Unassigned emails ({len(unassigned)})"):
    st.dataframe(unassigned, hide_index=True, use_container_width=True)
    if unassigned:
        chosen_email = st.selectbox(
            "Email to assign", unassigned,
            format_func=lambda email: (
                f"{email['received_at']} — {email['sender']} — {email['subject']}"
            ),
        )
        chosen_application = st.selectbox(
            "Assign to application", applications,
            format_func=lambda app: f"{app['company']} — {app['role']}",
            key="assignment_application",
        )
        apply_status = st.checkbox(
            "Also apply this email's detected status (only if confidence is at least 80%)",
            value=False,
        )
        if st.button("Assign email"):
            with connect(DATABASE) as connection:
                assign_email_to_application(
                    connection,
                    chosen_email["gmail_message_id"],
                    int(chosen_application["id"]),
                    apply_status,
                )
            st.success("Email assigned.")
            st.rerun()

metadata = query(
    """SELECT received_at, sender, subject, relevance, filter_reason
       FROM email_metadata ORDER BY received_at DESC LIMIT 100"""
)
with st.expander(f"Recent metadata-only records ({len(metadata)})"):
    st.caption("Ignored records contain no email body, only metadata and the filter decision.")
    st.dataframe([dict(row) for row in metadata], hide_index=True, use_container_width=True)
