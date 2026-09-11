from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from html import escape
from io import BytesIO
import base64
import urllib.error
import urllib.request
import uuid
import os
import textwrap
import zipfile

# Automatically load .env file from application directory and project root
_app_env_path = Path(__file__).resolve().parent / ".env"
if _app_env_path.exists():
    load_dotenv(dotenv_path=_app_env_path)
load_dotenv(find_dotenv(), override=False)

GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_REQUEST_TIMEOUT_SEC = int(os.getenv("GEMINI_REQUEST_TIMEOUT_SEC", "120"))
GEMINI_REQUEST_TIMEOUT_MS = GEMINI_REQUEST_TIMEOUT_SEC * 1000

import json
import re
import sqlite3
import time
import ipaddress
import httpx
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, flash, has_request_context, jsonify, redirect, render_template, request, send_file, session, url_for
from google import genai
from google.genai import types

try:
    from google.genai._gaos.lib.compat_errors import APITimeoutError
except ImportError:
    class APITimeoutError(Exception):
        pass

from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from proposal_template_registry import (
    template_registry,
    StandardProfessionalPdfRenderer,
    WhitehatsProfessionalPdfRenderer,
    DEFAULT_TEMPLATE_ID,
)

app = Flask(__name__)
flask_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not flask_secret_key:
    raise RuntimeError(
        "FLASK_SECRET_KEY is not set. Please set FLASK_SECRET_KEY in your .env file or environment variables before running the application."
    )
app.secret_key = flask_secret_key
app.config["SECRET_KEY"] = flask_secret_key
DB_NAME = "agentscan.db"
UPLOAD_DIR = Path(app.root_path) / "uploads" / "evidence"


def login_required(view):
    """Protect routes so only logged-in users can access them."""

    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            is_ajax = (
                request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or request.headers.get("X-Fetch-Request") == "true"
                or "application/json" in request.headers.get("Accept", "").lower()
            )
            if is_ajax:
                return jsonify({"success": False, "error": "Authentication required. Please log in."}), 401
            flash("Please log in to access this page.", "error")
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapped_view


def get_db_connection():
    """Create a SQLite connection for the app."""
    db_path = app.config.get("DB_NAME") or DB_NAME
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn


def seed_default_admin():
    """Create a default admin account so the app works without local env setup."""
    username = os.environ.get("AGENTSCAN_ADMIN_USERNAME", "admin")
    email = os.environ.get("AGENTSCAN_ADMIN_EMAIL", "admin@agentscan.local")
    password = os.environ.get("AGENTSCAN_ADMIN_PASSWORD") or "admin"

    conn = get_db_connection()
    existing = conn.execute(
        "SELECT id FROM users WHERE email = ? OR username = ?",
        (email, username),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, generate_password_hash(password)),
        )
        conn.commit()
    conn.close()


def init_db():
    """Create all required tables while keeping the existing app intact."""
    conn = get_db_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT NOT NULL,
            company_size TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            assessment_type TEXT NOT NULL,
            framework TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            pricing TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'In Evaluation',
            user_id INTEGER,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS business_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL UNIQUE,
            client TEXT,
            project_type TEXT,
            deliverables TEXT,
            timeline TEXT,
            budget TEXT,
            additional_context TEXT,
            proposal_json TEXT NOT NULL,
            template_id TEXT DEFAULT 'default_whitehats',
            deal_outcome TEXT DEFAULT 'Open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            type TEXT NOT NULL DEFAULT 'custom',
            file_path TEXT,
            user_id INTEGER,
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assessment_scopes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL UNIQUE,
            scope_description TEXT,
            in_scope_assets TEXT,
            out_of_scope_assets TEXT,
            internal_network TEXT DEFAULT 'No',
            web_applications TEXT DEFAULT 'No',
            cloud_infrastructure TEXT DEFAULT 'No',
            physical_security TEXT DEFAULT 'No',
            hr_systems TEXT DEFAULT 'No',
            security_policies TEXT DEFAULT 'No',
            assessment_activities TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organization_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL UNIQUE,
            legal_name TEXT,
            industry TEXT,
            employee_count TEXT,
            location TEXT,
            business_description TEXT,
            critical_business_functions TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            policy_name TEXT,
            description TEXT,
            owner TEXT,
            status TEXT,
            version TEXT,
            review_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS compliance_frameworks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL UNIQUE,
            framework_name TEXT,
            description TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            asset_name TEXT,
            asset_type TEXT,
            ip_address TEXT,
            operating_system TEXT,
            owner TEXT,
            criticality TEXT,
            status TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS risks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            title TEXT,
            description TEXT,
            category TEXT,
            likelihood INTEGER DEFAULT 1,
            impact INTEGER DEFAULT 1,
            risk_score INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'Low',
            owner TEXT,
            treatment TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            control_name TEXT,
            description TEXT,
            framework TEXT,
            control_category TEXT,
            owner TEXT,
            implementation_status TEXT,
            related_risk_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id),
            FOREIGN KEY(related_risk_id) REFERENCES risks(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS control_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            control_id INTEGER NOT NULL,
            test_description TEXT,
            test_method TEXT,
            result TEXT,
            tester TEXT,
            test_date TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(control_id) REFERENCES controls(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            control_id INTEGER,
            evidence_name TEXT,
            evidence_type TEXT,
            file_path TEXT,
            description TEXT,
            uploaded_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id),
            FOREIGN KEY(control_id) REFERENCES controls(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            vendor_name TEXT,
            service TEXT,
            criticality TEXT,
            risk_level TEXT,
            assessment_status TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            title TEXT,
            description TEXT,
            source TEXT,
            severity TEXT,
            ip_address TEXT,
            port TEXT,
            service TEXT,
            status TEXT,
            recommendation TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS remediation_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assessment_id INTEGER NOT NULL,
            finding_id INTEGER,
            risk_id INTEGER,
            action TEXT,
            owner TEXT,
            priority TEXT,
            due_date TEXT,
            status TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(assessment_id) REFERENCES assessments(id),
            FOREIGN KEY(finding_id) REFERENCES findings(id),
            FOREIGN KEY(risk_id) REFERENCES risks(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            assessment_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proposal_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            assessment_id INTEGER,
            recipient_email TEXT NOT NULL,
            status TEXT NOT NULL,
            message_id TEXT,
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(assessment_id) REFERENCES assessments(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            target TEXT,
            result_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(batch_id) REFERENCES batches(id)
        )
        """
    )

    conn.commit()

    # migrate older SQLite databases to the current schema safely
    tmpl_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(proposal_templates)").fetchall()
    ]
    if "is_deleted" not in tmpl_columns:
        conn.execute("ALTER TABLE proposal_templates ADD COLUMN is_deleted INTEGER DEFAULT 0")

    # migrate older SQLite databases to the current schema safely
    assessment_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(assessments)").fetchall()
    ]
    if "user_id" not in assessment_columns:
        conn.execute("ALTER TABLE assessments ADD COLUMN user_id INTEGER")

    proposal_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(business_proposals)").fetchall()
    ]
    if "template_id" not in proposal_columns:
        conn.execute("ALTER TABLE business_proposals ADD COLUMN template_id TEXT DEFAULT 'default_whitehats'")
    if "submitted_at" not in proposal_columns:
        conn.execute("ALTER TABLE business_proposals ADD COLUMN submitted_at TIMESTAMP")
    if "deal_outcome" not in proposal_columns:
        conn.execute("ALTER TABLE business_proposals ADD COLUMN deal_outcome TEXT DEFAULT 'Open'")
    conn.execute("UPDATE business_proposals SET deal_outcome = 'Open' WHERE deal_outcome IS NULL OR deal_outcome = ''")

    scope_columns = [
        row[1] for row in conn.execute("PRAGMA table_info(assessment_scopes)").fetchall()
    ]
    for column_name, definition in {
        "security_policies": "TEXT DEFAULT 'No'",
        "assessment_activities": "TEXT DEFAULT ''",
    }.items():
        if column_name not in scope_columns:
            conn.execute(f"ALTER TABLE assessment_scopes ADD COLUMN {column_name} {definition}")

    first_user = conn.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
    if first_user is not None:
        conn.execute("UPDATE assessments SET user_id = ? WHERE user_id IS NULL", (first_user[0],))
    conn.commit()
    conn.close()
    seed_default_admin()


def get_user_by_email(email):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_batches_for_user(user_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM batches WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def get_batch_by_id(batch_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    conn.close()
    return row


def get_scan_results_for_batch(batch_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM scan_results WHERE batch_id = ? ORDER BY created_at DESC",
        (batch_id,),
    ).fetchall()
    conn.close()
    return rows


def get_page_numbers(page, total_pages, window=1):
    """Return a condensed list of page numbers with None as ellipsis markers.

    Always includes first and last page, plus `window` pages on either
    side of the current page. e.g. for page=13, total_pages=14, window=1:
    [1, None, 12, 13, 14]
    """
    pages = {1, total_pages}
    for p in range(page - window, page + window + 1):
        if 1 <= p <= total_pages:
            pages.add(p)

    sorted_pages = sorted(pages)
    result = []
    prev = None
    for p in sorted_pages:
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


def save_scan_result(batch_id, target, result_data):
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO scan_results (batch_id, target, result_data) VALUES (?, ?, ?)",
        (batch_id, target, json.dumps(result_data)),
    )
    conn.commit()
    conn.close()


def validate_assessment_form(form_data):
    """Validate the basics required to create an assessment."""
    errors = []
    required = [
        "customer_name",
        "industry",
        "company_size",
        "assessment_type",
        "framework",
        "start_date",
        "end_date",
        "pricing",
    ]
    for field in required:
        value = form_data.get(field, "").strip()
        if not value:
            errors.append(f"{field.replace('_', ' ').title()} is required.")
    if form_data.get("start_date") and form_data.get("end_date"):
        if form_data["start_date"] > form_data["end_date"]:
            errors.append("End date must be after the start date.")
    try:
        if form_data.get("pricing") and float(form_data["pricing"]) < 0:
            errors.append("Pricing must be zero or more.")
    except ValueError:
        errors.append("Pricing must be a valid number.")
    return errors


def create_customer(name, industry, company_size):
    conn = get_db_connection()
    cur = conn.execute(
        "INSERT INTO customers (name, industry, company_size) VALUES (?, ?, ?)",
        (name, industry, company_size),
    )
    conn.commit()
    customer_id = cur.lastrowid
    conn.close()
    return customer_id


def create_assessment(customer_id, assessment_type, framework, start_date, end_date, pricing, status, user_id=None):
    conn = get_db_connection()
    cursor = conn.execute(
        """
        INSERT INTO assessments (
            customer_id, assessment_type, framework, start_date, end_date, pricing, status, user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, assessment_type, framework, start_date, end_date, pricing, status, user_id),
    )
    conn.commit()
    assessment_id = cursor.lastrowid
    conn.close()
    return assessment_id


def get_selected_assessment(requested_id=None):
    """Retrieve the currently selected assessment for the logged in user based on session['assessment_id']
    or explicit query/url parameter.

    Returns None if no assessment is selected or if the selected assessment no longer exists/is unauthorized.
    Does NOT silently fall back to an arbitrary assessment in the database.
    """
    target_id = requested_id
    if not target_id and request and hasattr(request, "args"):
        target_id = request.args.get("assessment_id", type=int)

    if target_id:
        assessment = get_assessment_for_user(target_id)
        if assessment:
            session["assessment_id"] = assessment["id"]
            return assessment

    selected_id = session.get("assessment_id")
    if not selected_id:
        return None
    selected = get_assessment_for_user(selected_id)
    if selected is None:
        session.pop("assessment_id", None)
        return None
    return selected


def get_latest_assessment():
    """Return the currently selected assessment, or None if no assessment is selected.

    Preserves backwards compatibility without silently loading unselected fallback records.
    """
    return get_selected_assessment()


@app.context_processor
def inject_active_assessment():
    """Inject current_assessment into all templates."""
    try:
        return dict(current_assessment=get_selected_assessment())
    except Exception:
        return dict(current_assessment=None)



def get_assessments_for_user(user_id=None):
    """Return only assessments belonging to the current user."""
    user_id = user_id or session.get("user_id")
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT a.*, c.name AS customer_name, c.industry, c.company_size
        FROM assessments a JOIN customers c ON c.id = a.customer_id
        WHERE a.user_id = ? ORDER BY a.id DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def log_audit(action, entity_type, entity_id=None, description="", assessment_id=None):
    """Write an audit event without exposing it outside the current user's data."""
    user_id = session.get("user_id")
    if not user_id:
        return
    try:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO audit_logs (user_id, assessment_id, action, entity_type, entity_id, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, assessment_id, action, entity_type, entity_id, description),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_assessment_for_user(assessment_id=None, user_id=None):
    if user_id is None:
        user_id = session.get("user_id")
    conn = get_db_connection()
    query = """
        SELECT a.*, c.name AS customer_name, c.industry, c.company_size
        FROM assessments a
        JOIN customers c ON c.id = a.customer_id
        WHERE a.user_id = ?
    """
    params = [user_id]
    if assessment_id is not None:
        query += " AND a.id = ?"
        params.append(assessment_id)
    query += " ORDER BY a.id DESC LIMIT 1"
    row = conn.execute(query, tuple(params)).fetchone()
    conn.close()
    return row


def delete_assessment_by_id(assessment_id, user_id=None):
    """Delete an assessment and all its associated GRC data and evidence files.
    
    Verifies that the assessment belongs to user_id before deletion.
    Returns (success_bool, message_str, customer_name_str).
    """
    if user_id is None:
        user_id = session.get("user_id")

    assessment = get_assessment_for_user(assessment_id, user_id)
    if assessment is None:
        return False, "That assessment does not exist or you are not authorized to delete it.", ""

    customer_name = assessment["customer_name"]

    try:
        conn = get_db_connection()

        # 1. Physical evidence file cleanup
        evidence_rows = conn.execute(
            "SELECT file_path FROM evidence WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchall()
        for ev in evidence_rows:
            fpath = ev["file_path"] if isinstance(ev, sqlite3.Row) or isinstance(ev, dict) else ev[0]
            if fpath and os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        # 2. Child tables deletion
        conn.execute("DELETE FROM control_tests WHERE control_id IN (SELECT id FROM controls WHERE assessment_id = ?)", (assessment_id,))
        conn.execute("DELETE FROM remediation_actions WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM findings WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM evidence WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM controls WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM risks WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM assets WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM compliance_frameworks WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM policies WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM assessment_scopes WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM organization_profiles WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM vendors WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM business_proposals WHERE assessment_id = ?", (assessment_id,))

        # 3. Unlink audit logs (preserve audit record history with NULL assessment_id)
        conn.execute("UPDATE audit_logs SET assessment_id = NULL WHERE assessment_id = ?", (assessment_id,))

        # 4. Audit log event for deletion
        conn.execute(
            """
            INSERT INTO audit_logs (user_id, assessment_id, action, entity_type, entity_id, description)
            VALUES (?, NULL, 'DELETE', 'assessment', ?, ?)
            """,
            (user_id, assessment_id, f"Deleted assessment for '{customer_name}'"),
        )

        # 5. Delete assessment row itself
        conn.execute("DELETE FROM assessments WHERE id = ? AND user_id = ?", (assessment_id, user_id))

        conn.commit()
        conn.close()

        # 6. Clear session if active assessment was deleted
        if session.get("assessment_id") == assessment_id:
            session.pop("assessment_id", None)

        return True, f"Assessment for '{customer_name}' deleted successfully.", customer_name

    except sqlite3.Error as exc:
        return False, f"Failed to delete assessment: {exc}", customer_name


def get_table_rows(table_name, assessment_id):
    conn = get_db_connection()
    rows = conn.execute(
        f"SELECT * FROM {table_name} WHERE assessment_id = ? ORDER BY id DESC",
        (assessment_id,),
    ).fetchall()
    conn.close()
    return rows


def get_section_status(assessment_id, section_name):
    conn = get_db_connection()
    status = "Not Started"

    if section_name == "organization_profile":
        row = conn.execute("SELECT * FROM organization_profiles WHERE assessment_id = ?", (assessment_id,)).fetchone()
        if row and row["legal_name"] and row["industry"] and row["location"] and row["business_description"]:
            status = "Completed"
        elif row:
            status = "In Progress"

    elif section_name == "scope":
        row = conn.execute("SELECT * FROM assessment_scopes WHERE assessment_id = ?", (assessment_id,)).fetchone()
        if row and row["scope_description"] and row["in_scope_assets"]:
            status = "Completed"
        elif row:
            status = "In Progress"

    elif section_name in {"policies", "assets", "risks", "controls", "evidence", "vendors", "findings", "remediation"}:
        if section_name == "policies":
            count = conn.execute("SELECT COUNT(*) FROM policies WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "assets":
            count = conn.execute("SELECT COUNT(*) FROM assets WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "risks":
            count = conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "controls":
            count = conn.execute("SELECT COUNT(*) FROM controls WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "evidence":
            count = conn.execute("SELECT COUNT(*) FROM evidence WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "vendors":
            count = conn.execute("SELECT COUNT(*) FROM vendors WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "findings":
            count = conn.execute("SELECT COUNT(*) FROM findings WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        elif section_name == "remediation":
            count = conn.execute("SELECT COUNT(*) FROM remediation_actions WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        status = "Completed" if count > 0 else "Not Started"

    elif section_name == "compliance_framework":
        row = conn.execute("SELECT * FROM compliance_frameworks WHERE assessment_id = ?", (assessment_id,)).fetchone()
        if row and row["framework_name"]:
            status = "Completed"
        elif row:
            status = "In Progress"

    elif section_name == "risk_assessment":
        count = conn.execute(
            "SELECT COUNT(*) FROM risks WHERE assessment_id = ? AND likelihood IS NOT NULL AND impact IS NOT NULL",
            (assessment_id,),
        ).fetchone()[0]
        status = "Completed" if count > 0 else "Not Started"

    elif section_name == "risk_register":
        count = conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ?", (assessment_id,)).fetchone()[0]
        status = "Completed" if count > 0 else "Not Started"

    elif section_name == "control_testing":
        count = conn.execute(
            "SELECT COUNT(*) FROM control_tests ct JOIN controls c ON c.id = ct.control_id WHERE c.assessment_id = ?",
            (assessment_id,),
        ).fetchone()[0]
        status = "Completed" if count > 0 else "Not Started"

    conn.close()
    return status


def risk_score_and_level(likelihood, impact):
    try:
        score = int(likelihood) * int(impact)
    except (TypeError, ValueError):
        score = 0
    if score <= 4:
        level = "Low"
    elif score <= 9:
        level = "Medium"
    elif score <= 16:
        level = "High"
    else:
        level = "Critical"
    return score, level


def get_report_summary(assessment_id):
    conn = get_db_connection()
    summary = {
        "total_risks": conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ?", (assessment_id,)).fetchone()[0],
        "critical_risks": conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ? AND risk_level = 'Critical'", (assessment_id,)).fetchone()[0],
        "high_risks": conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ? AND risk_level = 'High'", (assessment_id,)).fetchone()[0],
        "medium_risks": conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ? AND risk_level = 'Medium'", (assessment_id,)).fetchone()[0],
        "low_risks": conn.execute("SELECT COUNT(*) FROM risks WHERE assessment_id = ? AND risk_level = 'Low'", (assessment_id,)).fetchone()[0],
        "total_controls": conn.execute("SELECT COUNT(*) FROM controls WHERE assessment_id = ?", (assessment_id,)).fetchone()[0],
        "controls_implemented": conn.execute("SELECT COUNT(*) FROM controls WHERE assessment_id = ? AND UPPER(implementation_status) = 'IMPLEMENTED'", (assessment_id,)).fetchone()[0],
        "controls_partially_implemented": conn.execute("SELECT COUNT(*) FROM controls WHERE assessment_id = ? AND UPPER(implementation_status) = 'PARTIALLY IMPLEMENTED'", (assessment_id,)).fetchone()[0],
        "controls_failed": conn.execute("""SELECT COUNT(DISTINCT c.id) FROM controls c
            LEFT JOIN control_tests ct ON ct.control_id = c.id
            WHERE c.assessment_id = ? AND (UPPER(c.implementation_status) = 'FAILED' OR UPPER(ct.result) IN ('FAIL', 'FAILED'))""", (assessment_id,)).fetchone()[0],
        "total_findings": conn.execute("SELECT COUNT(*) FROM findings WHERE assessment_id = ?", (assessment_id,)).fetchone()[0],
        "open_findings": conn.execute("SELECT COUNT(*) FROM findings WHERE assessment_id = ? AND status IN ('Open', 'In Progress')", (assessment_id,)).fetchone()[0],
        "open_remediation": conn.execute("SELECT COUNT(*) FROM remediation_actions WHERE assessment_id = ? AND status IN ('Open', 'In Progress')", (assessment_id,)).fetchone()[0],
        "completed_remediation": conn.execute("SELECT COUNT(*) FROM remediation_actions WHERE assessment_id = ? AND status = 'Completed'", (assessment_id,)).fetchone()[0],
        "evidence_items": conn.execute("SELECT COUNT(*) FROM evidence WHERE assessment_id = ?", (assessment_id,)).fetchone()[0],
    }
    conn.close()
    return summary


def get_completion_percentage(assessment_id):
    section_keys = [
        "organization_profile", "scope", "policies", "compliance_framework", "assets",
        "risks", "risk_assessment", "risk_register", "controls", "control_testing",
        "evidence", "vendors", "findings", "remediation",
    ]
    completed = sum(get_section_status(assessment_id, key) == "Completed" for key in section_keys)
    return round((completed / len(section_keys)) * 100) if section_keys else 0


def get_recommendations(assessment_id, summary):
    """Create evidence-led recommendations from the current assessment records."""
    recommendations = []
    if summary["critical_risks"] or summary["high_risks"]:
        recommendations.append("Prioritize treatment plans for critical and high risks, with accountable owners and target dates.")
    if summary["controls_failed"]:
        recommendations.append("Remediate failed control tests and retain retest evidence before closing related actions.")
    if summary["open_findings"]:
        recommendations.append("Review open findings, confirm severity and assign remediation owners and due dates.")
    if summary["total_controls"] and summary["controls_implemented"] < summary["total_controls"]:
        recommendations.append("Complete implementation plans for controls that are partial or not implemented.")
    if summary["evidence_items"] == 0:
        recommendations.append("Collect evidence for the controls and findings reviewed during this assessment.")
    if not recommendations:
        recommendations.append("Continue periodic review of controls, risks, findings and remediation status to maintain assurance.")
    return recommendations


def assessment_owner_check(assessment_id):
    conn = get_db_connection()
    row = conn.execute(
        "SELECT id FROM assessments WHERE id = ? AND user_id = ?",
        (assessment_id, session.get("user_id")),
    ).fetchone()
    conn.close()
    return row is not None


@app.route("/")
def login_page():
    if "user_id" in session:
        return redirect(url_for("proposal_tracker"))
    return render_template("login.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("proposal_tracker"))
        return render_template("login.html")

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email or not password:
        flash("Please enter both email and password.", "error")
        return redirect(url_for("login_page"))

    user = get_user_by_email(email)
    if user and check_password_hash(user["password_hash"], password):
        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        log_audit("Login", "user", user["id"], f"User {user['username']} logged in.")
        flash("Login successful.", "success")
        return redirect(url_for("proposal_tracker"))

    flash("Invalid email or password.", "error")
    return redirect(url_for("login_page"))


@app.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for("proposal_tracker"))


def get_proposal_expiration_info(submitted_at_raw, created_at_raw, raw_status):
    """
    Calculate dynamic proposal expiration state based on 30-day submission validity rule.
    Returns structured expiration info dict.
    """
    now = datetime.now()
    submitted_dt = None

    if submitted_at_raw:
        if isinstance(submitted_at_raw, datetime):
            submitted_dt = submitted_at_raw
        else:
            s_str = str(submitted_at_raw).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    submitted_dt = datetime.strptime(s_str[:19] if "T" not in s_str else s_str, fmt if "T" not in s_str else "%Y-%m-%dT%H:%M:%S")
                    break
                except ValueError:
                    continue

    raw_status_clean = (raw_status or "In Evaluation").strip()

    # Fallback to created_at if explicit submitted_at is not present
    if not submitted_dt and created_at_raw:
        if isinstance(created_at_raw, datetime):
            submitted_dt = created_at_raw
        else:
            c_str = str(created_at_raw).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    submitted_dt = datetime.strptime(c_str[:19] if "T" not in c_str else c_str, fmt if "T" not in c_str else "%Y-%m-%dT%H:%M:%S")
                    break
                except ValueError:
                    continue

    # Normalize baseline status
    if raw_status_clean in ("In Progress", "Draft", "New"):
        norm_status = "In Evaluation"
    elif raw_status_clean in ("Submitted", "Completed"):
        norm_status = "Submitted"
    elif raw_status_clean in ("Approved", "Accepted"):
        norm_status = "Approved"
    elif raw_status_clean in ("Rejected", "Declined"):
        norm_status = "Rejected"
    else:
        norm_status = raw_status_clean

    if not submitted_dt:
        return {
            "submitted_date_str": "-",
            "expiration_date_str": "-",
            "days_remaining": 30,
            "total_days": 30,
            "is_expired": False,
            "norm_status": norm_status,
            "tooltip_title": "Proposal Expiration",
            "tooltip_line1": "Not Submitted",
            "tooltip_line2": "30 days validity upon submission",
            "tooltip_line3": "Awaiting submission",
        }

    submitted_date_str = submitted_dt.strftime("%Y-%m-%d")
    expiration_dt = submitted_dt + timedelta(days=30)

    elapsed_days = (now.date() - submitted_dt.date()).days
    days_remaining = max(0, 30 - elapsed_days)
    is_expired = elapsed_days > 30

    exp_month = expiration_dt.strftime("%B")
    exp_day = expiration_dt.day
    expiration_date_str = f"{exp_month} {exp_day}, {expiration_dt.year}"

    # Only transition active/submitted proposals to Expired after 30 days
    # Preserve Approved and Rejected semantics!
    if is_expired and norm_status in ("Submitted", "In Evaluation"):
        norm_status = "Expired"

    if is_expired:
        line1 = "Expired"
        line2 = f"Expired on {expiration_date_str}"
        line3 = "Renewal required"
    else:
        line1 = f"{days_remaining} days remaining"
        line2 = f"{days_remaining}/30 days remaining"
        line3 = f"Expires {expiration_date_str}"

    return {
        "submitted_date_str": submitted_date_str,
        "expiration_date_str": expiration_date_str,
        "days_remaining": days_remaining,
        "total_days": 30,
        "is_expired": is_expired,
        "norm_status": norm_status,
        "tooltip_title": "Proposal Expiration",
        "tooltip_line1": line1,
        "tooltip_line2": line2,
        "tooltip_line3": line3,
    }


@app.route("/tracker")
@app.route("/proposal-tracker")
@login_required
def proposal_tracker():
    user_id = session.get("user_id")
    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT 
            p.id AS proposal_table_id,
            p.assessment_id,
            p.client,
            p.project_type,
            p.deliverables,
            p.timeline,
            p.budget,
            p.template_id,
            p.deal_outcome,
            p.created_at,
            p.updated_at,
            p.submitted_at,
            p.proposal_json,
            a.status,
            a.start_date,
            a.end_date,
            c.name AS customer_name,
            c.industry
        FROM business_proposals p
        JOIN assessments a ON a.id = p.assessment_id
        JOIN customers c ON c.id = a.customer_id
        WHERE a.user_id = ?
        ORDER BY p.id DESC
        """,
        (user_id,),
    ).fetchall()

    assessments_without_proposals = conn.execute(
        """
        SELECT 
            a.id AS assessment_id,
            a.assessment_type AS project_type,
            a.pricing AS budget,
            a.status,
            a.start_date AS created_at,
            a.end_date AS updated_at,
            c.name AS client,
            c.name AS customer_name,
            c.industry
        FROM assessments a
        JOIN customers c ON c.id = a.customer_id
        WHERE a.user_id = ? AND a.id NOT IN (SELECT assessment_id FROM business_proposals)
        ORDER BY a.id DESC
        """,
        (user_id,),
    ).fetchall()

    conn.close()

    proposals_list = []
    seen_assessment_ids = set()

    for r in rows:
        r_dict = dict(r)
        ass_id = r_dict["assessment_id"]
        seen_assessment_ids.add(ass_id)

        status = (r_dict.get("status") or "In Progress").strip()
        created_raw = str(r_dict.get("created_at") or "")
        submitted_raw = r_dict.get("submitted_at")

        exp_info = get_proposal_expiration_info(submitted_raw, created_raw, status)
        norm_status = exp_info["norm_status"]

        year = "2026"
        if created_raw and len(created_raw) >= 4 and created_raw[:4].isdigit():
            year = created_raw[:4]

        prop_code = f"PROP-{year}-{ass_id}"

        # Extract canonical contribution & currency from stored proposal_json or DB fields
        proposal_obj = {}
        if r_dict.get("proposal_json"):
            try:
                proposal_obj = json.loads(r_dict["proposal_json"], parse_int=_safe_json_parse_int)
            except Exception:
                proposal_obj = {}

        saved_inputs = proposal_obj.get("_inputs") or {}
        currency = str(
            saved_inputs.get("currency")
            or proposal_obj.get("currency")
            or "INR"
        ).strip().upper()
        if currency not in CURRENCY_SYMBOLS:
            currency = "INR"

        comm_obj = proposal_obj.get("commercials") or proposal_obj.get("pricing") or {}
        raw_comm_amount = comm_obj.get("amount") if isinstance(comm_obj, dict) else comm_obj

        raw_budget = (
            _clean_amount_val(saved_inputs.get("budget"))
            or _clean_amount_val(raw_comm_amount)
            or _clean_amount_val(proposal_obj.get("pricing_amount"))
            or _clean_amount_val(proposal_obj.get("budget"))
            or _clean_amount_val(proposal_obj.get("contribution"))
            or _clean_amount_val(proposal_obj.get("investment"))
            or _clean_amount_val(r_dict.get("budget"))
        )

        budget_val = format_currency_amount(raw_budget, currency)

        tmpl_id = (
            r_dict.get("template_id")
            or saved_inputs.get("template_id")
            or proposal_obj.get("template_id")
            or "default_whitehats"
        )
        tmpl_info = get_template_info(tmpl_id, user_id)

        raw_outcome = (r_dict.get("deal_outcome") or "Open").strip()
        deal_outcome = raw_outcome.title() if raw_outcome.title() in ("Open", "Won", "Lost") else "Open"

        proposals_list.append({
            "id": r_dict["proposal_table_id"],
            "assessment_id": ass_id,
            "code": prop_code,
            "client": r_dict.get("client") or r_dict.get("customer_name") or "Enterprise Client",
            "project_type": r_dict.get("project_type") or "Security Assessment",
            "budget": budget_val,
            "raw_status": status,
            "status": norm_status,
            "deal_outcome": deal_outcome,
            "created_at": created_raw[:10] if created_raw else datetime.now().strftime("%Y-%m-%d"),
            "submitted_at": exp_info["submitted_date_str"],
            "expiration": exp_info,
            "template_id": tmpl_id,
            "template_name": tmpl_info.get("name") or "Standard Professional",
        })

    for a in assessments_without_proposals:
        a_dict = dict(a)
        ass_id = a_dict["assessment_id"]
        if ass_id in seen_assessment_ids:
            continue
        seen_assessment_ids.add(ass_id)

        status = (a_dict.get("status") or "In Progress").strip()
        created_raw = str(a_dict.get("created_at") or "")
        exp_info = get_proposal_expiration_info(None, created_raw, status)
        norm_status = exp_info["norm_status"]

        year = "2026"
        if created_raw and len(created_raw) >= 4 and created_raw[:4].isdigit():
            year = created_raw[:4]

        prop_code = f"PROP-{year}-{ass_id}"

        raw_budget = a_dict.get("budget")
        budget_val = format_currency_amount(raw_budget, "INR")

        proposals_list.append({
            "id": ass_id,
            "assessment_id": ass_id,
            "code": prop_code,
            "client": a_dict.get("client") or "Enterprise Client",
            "project_type": a_dict.get("project_type") or "Security Assessment",
            "budget": budget_val,
            "raw_status": status,
            "status": norm_status,
            "deal_outcome": "Open",
            "created_at": created_raw[:10] if created_raw else datetime.now().strftime("%Y-%m-%d"),
            "submitted_at": exp_info["submitted_date_str"],
            "expiration": exp_info,
        })


    total_count = len(proposals_list)
    submitted_count = sum(1 for p in proposals_list if p["status"] == "Submitted")
    in_eval_count = sum(1 for p in proposals_list if p["status"] == "In Evaluation")
    approved_count = sum(1 for p in proposals_list if p["status"] == "Approved")
    rejected_count = sum(1 for p in proposals_list if p["status"] == "Rejected")

    status_counts = {
        "Submitted": submitted_count,
        "In Evaluation": in_eval_count,
        "Approved": approved_count,
        "Rejected": rejected_count
    }

    project_type_counts = {}
    for p in proposals_list:
        ptype = p["project_type"]
        if "ISO 27001" in ptype:
            key = "ISO 27001 Compliance"
        elif "SOC 2" in ptype:
            key = "SOC 2 Audit"
        elif "NIST" in ptype:
            key = "NIST CSF Risk Framework"
        elif "Network" in ptype or "Cybersecurity" in ptype:
            key = "Cybersecurity & Network"
        else:
            key = ptype[:28] + "..." if len(ptype) > 28 else ptype
        project_type_counts[key] = project_type_counts.get(key, 0) + 1

    monthly_trend = {}
    for p in proposals_list:
        created = p["created_at"]
        month_key = created[:7] if len(created) >= 7 else "Recent"
        monthly_trend[month_key] = monthly_trend.get(month_key, 0) + 1

    monthly_trend_keys = list(monthly_trend.keys())
    monthly_trend_values = list(monthly_trend.values())

    return render_template(
        "tracker.html",
        proposals=proposals_list,
        total_count=total_count,
        submitted_count=submitted_count,
        in_eval_count=in_eval_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        status_counts=status_counts,
        project_type_counts=project_type_counts,
        monthly_trend=monthly_trend,
        monthly_trend_keys=monthly_trend_keys,
        monthly_trend_values=monthly_trend_values,
    )


@app.route("/tracker/delete/<int:assessment_id>", methods=["POST"])
@login_required
def tracker_delete_proposal(assessment_id):
    """Delete a proposal and its underlying assessment permanently from SQLite database."""
    user_id = session.get("user_id")
    if not user_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        flash("Authentication required.", "error")
        return redirect(url_for("login_page"))

    conn = get_db_connection()
    try:
        # Check ownership
        assessment = conn.execute(
            "SELECT a.id, c.name AS customer_name FROM assessments a JOIN customers c ON c.id = a.customer_id WHERE a.id = ? AND a.user_id = ?",
            (assessment_id, user_id),
        ).fetchone()

        if not assessment:
            assessment = conn.execute(
                "SELECT id FROM assessments WHERE id = ? AND user_id = ?",
                (assessment_id, user_id),
            ).fetchone()
            customer_name = "Enterprise Client"
        else:
            customer_name = assessment["customer_name"]

        if not assessment:
            conn.close()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                return jsonify({"success": False, "message": "Proposal not found or access denied."}), 404
            flash("Proposal not found or access denied.", "error")
            return redirect(url_for("proposal_tracker"))

        # Clean up child tables
        conn.execute("DELETE FROM control_tests WHERE control_id IN (SELECT id FROM controls WHERE assessment_id = ?)", (assessment_id,))
        conn.execute("DELETE FROM remediation_actions WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM findings WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM evidence WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM controls WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM risks WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM assets WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM compliance_frameworks WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM policies WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM assessment_scopes WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM organization_profiles WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM vendors WHERE assessment_id = ?", (assessment_id,))
        conn.execute("DELETE FROM business_proposals WHERE assessment_id = ?", (assessment_id,))

        # Unlink audit logs & record deletion event
        conn.execute("UPDATE audit_logs SET assessment_id = NULL WHERE assessment_id = ?", (assessment_id,))
        conn.execute(
            """
            INSERT INTO audit_logs (user_id, assessment_id, action, entity_type, entity_id, description)
            VALUES (?, NULL, 'DELETE', 'proposal', ?, ?)
            """,
            (user_id, assessment_id, f"Deleted proposal assessment ID {assessment_id} for '{customer_name}'"),
        )

        # Delete main assessment row
        conn.execute("DELETE FROM assessments WHERE id = ? AND user_id = ?", (assessment_id, user_id))

        conn.commit()
        conn.close()

        # Clear active assessment session keys if deleted
        if session.get("active_assessment_id") == assessment_id:
            session.pop("active_assessment_id", None)
            session.pop("active_proposal", None)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({
                "success": True,
                "message": "Proposal deleted successfully.",
                "deleted_assessment_id": assessment_id
            })

        flash("Proposal deleted successfully.", "success")
        return redirect(url_for("proposal_tracker"))

    except Exception as exc:
        conn.close()
        app.logger.exception("Failed to delete proposal assessment %s", assessment_id)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Unable to delete proposal. Please try again."}), 500
        flash("Unable to delete proposal. Please try again.", "error")
        return redirect(url_for("proposal_tracker"))


@app.route("/tracker/deal-outcome", methods=["POST"])
@app.route("/tracker/deal-outcome/<int:assessment_id>", methods=["POST"])
@login_required
def tracker_update_deal_outcome(assessment_id=None):
    """Update Deal Outcome (Open / Won / Lost) for a proposal/deal in tracker."""
    user_id = session.get("user_id")
    if not user_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        flash("Authentication required.", "error")
        return redirect(url_for("login_page"))

    data = request.get_json(silent=True) or request.form or {}
    if not assessment_id:
        assessment_id = data.get("assessment_id")

    try:
        assessment_id = int(assessment_id)
    except (ValueError, TypeError):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Invalid assessment ID."}), 400
        flash("Invalid assessment ID.", "error")
        return redirect(url_for("proposal_tracker"))

    raw_outcome = str(data.get("deal_outcome") or data.get("outcome") or "").strip().title()
    if raw_outcome not in ("Open", "Won", "Lost"):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Invalid deal outcome. Allowed values: Open, Won, Lost."}), 400
        flash("Invalid deal outcome. Allowed values: Open, Won, Lost.", "error")
        return redirect(url_for("proposal_tracker"))

    conn = get_db_connection()
    assessment = conn.execute(
        "SELECT id FROM assessments WHERE id = ? AND user_id = ?",
        (assessment_id, user_id),
    ).fetchone()

    if not assessment:
        conn.close()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "message": "Proposal not found or access denied."}), 404
        flash("Proposal not found or access denied.", "error")
        return redirect(url_for("proposal_tracker"))

    prop_row = conn.execute(
        "SELECT id FROM business_proposals WHERE assessment_id = ?",
        (assessment_id,),
    ).fetchone()

    if prop_row:
        conn.execute(
            "UPDATE business_proposals SET deal_outcome = ?, updated_at = CURRENT_TIMESTAMP WHERE assessment_id = ?",
            (raw_outcome, assessment_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO business_proposals (assessment_id, client, project_type, deliverables, timeline, budget, additional_context, proposal_json, template_id, deal_outcome, updated_at)
            VALUES (?, 'Enterprise Client', 'Security Assessment', '', '', '₹0', '', '{}', 'default_whitehats', ?, CURRENT_TIMESTAMP)
            """,
            (assessment_id, raw_outcome),
        )

    conn.commit()
    conn.close()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "assessment_id": assessment_id,
            "deal_outcome": raw_outcome,
            "message": f"Deal outcome updated to {raw_outcome}."
        })

    flash(f"Deal outcome updated to {raw_outcome}.", "success")
    return redirect(url_for("proposal_tracker"))



@app.route("/logout")
def logout():
    log_audit("Logout", "user", session.get("user_id"), "User logged out.")
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login_page"))


@app.route("/grc")
@login_required
def grc():
    assessment = get_selected_assessment()
    if not assessment:
        flash("Please select or create an assessment to view Framework Controls.", "error")
        return redirect(url_for("assessment_list"))

    section_order = [
        ("organization_profile", "organization_profile", "Organization Profile", "Define the company structure, business context and critical functions."),
        ("assessment_scope", "scope", "Assessment Scope", "Define the exact scope and area of review."),
        ("policies", "policies", "Security Policies", "List and monitor the policies in use."),
        ("compliance_framework", "compliance_framework", "Compliance Framework", "Capture the framework and assessment status."),
        ("assets", "assets", "Asset Management", "Track systems, applications and devices."),
        ("risks", "risks", "Risk Identification", "Create risk entries to document issues."),
        ("risk_assessment", "risk_assessment", "Risk Assessment", "Assign likelihood and impact."),
        ("risk_register", "risk_register", "Risk Register", "Track and filter risk scores."),
        ("controls", "controls", "Security Controls", "Document controls linked to risks."),
        ("control_testing", "control_testing", "Control Testing", "Record tests for control review."),
        ("evidence_management", "evidence", "Evidence Management", "Collect files and supporting proof."),
        ("vendors", "vendors", "Third-Party / Vendor Risk", "Assess external supplier risk."),
        ("findings", "findings", "Security Findings", "Document findings discovered in the assessment."),
        ("remediation", "remediation", "Remediation & Reporting", "Track action items and summary reporting."),
    ]

    sections = []
    for route_name, key, title, description in section_order:
        sections.append({
            "route_name": route_name,
            "key": key,
            "title": title,
            "description": description,
            "status": get_section_status(assessment["id"], key),
        })
    completed = sum(1 for s in sections if s["status"] == "Completed")
    summary = get_report_summary(assessment["id"])
    return render_template("grc.html", assessment=assessment, sections=sections, completed_sections=completed, total_sections=len(sections), summary=summary)


@app.route("/grc/assessments")
@login_required
def assessment_list():
    """Customer/assessment selection is the secure entry point to a GRC workflow."""
    return render_template("assessment_list.html", assessments=get_assessments_for_user())


@app.route("/grc/assessments/<int:assessment_id>/select", methods=["GET", "POST"])
@login_required
def select_assessment(assessment_id):
    assessment = get_assessment_for_user(assessment_id)
    if assessment is None:
        flash("That assessment does not exist or you are not authorized to access it.", "error")
        return redirect(url_for("assessment_list"))
    session["assessment_id"] = assessment["id"]
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("grc_introduction"))


@app.route("/grc/assessments/<int:assessment_id>/delete", methods=["POST"])
@login_required
def delete_assessment(assessment_id):
    """Delete an assessment and all associated child data."""
    success, message, customer_name = delete_assessment_by_id(assessment_id, session.get("user_id"))

    # Support AJAX / JSON request header if provided
    is_json = (
        request.headers.get("Accept") == "application/json"
        or request.content_type == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )

    if is_json:
        if success:
            return {"success": True, "message": message, "customer_name": customer_name}, 200
        else:
            status_code = 404 if "exist" in message or "authorized" in message else 500
            return {"success": False, "message": message}, status_code

    if success:
        flash(message, "success")
    else:
        flash(message, "error")

    return redirect(url_for("assessment_list"))


@app.route("/grc/introduction")
@login_required
def grc_introduction():
    assessment = get_selected_assessment()
    if assessment is None:
        flash("Please select or create an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    proposal = get_business_proposal(assessment["id"])
    return render_template("grc_introduction.html", assessment=assessment, proposal=proposal)


@app.route("/grc/engagement", methods=["GET", "POST"])
@login_required
def engagement_overview():
    assessment = get_selected_assessment()
    if assessment is None:
        flash("Please select or create an assessment first.", "error")
        return redirect(url_for("assessment_list"))

    if request.method == "GET":
        proposal = get_business_proposal(assessment["id"])
        return render_template("engagement_overview.html", assessment=assessment, proposal=proposal, editing=request.args.get("edit") == "1")

    form_data = {
        "customer_name": request.form.get("customer_name", "").strip(),
        "industry": request.form.get("industry", "").strip(),
        "company_size": request.form.get("company_size", "").strip(),
        "assessment_type": request.form.get("assessment_type", "").strip(),
        "framework": request.form.get("framework", "").strip(),
        "start_date": request.form.get("start_date", "").strip(),
        "end_date": request.form.get("end_date", "").strip(),
        "pricing": request.form.get("pricing", "").strip(),
    }
    errors = validate_assessment_form(form_data)
    if errors:
        for error in errors:
            flash(error, "error")
        return render_template("engagement_overview.html", assessment=assessment, editing=True)
    status = request.form.get("status", "In Progress").strip() or "In Progress"
    conn = get_db_connection()
    conn.execute(
        "UPDATE customers SET name = ?, industry = ?, company_size = ? WHERE id = ?",
        (form_data["customer_name"], form_data["industry"], form_data["company_size"], assessment["customer_id"]),
    )
    conn.execute(
        """UPDATE assessments SET assessment_type = ?, framework = ?, start_date = ?, end_date = ?,
           pricing = ?, status = ? WHERE id = ? AND user_id = ?""",
        (form_data["assessment_type"], form_data["framework"], form_data["start_date"], form_data["end_date"], form_data["pricing"], status, assessment["id"], session["user_id"]),
    )
    conn.commit()
    conn.close()
    log_audit("Assessment update", "assessment", assessment["id"], "Updated engagement overview details.", assessment["id"])
    flash("Engagement overview updated.", "success")
    return redirect(url_for("ai_grc"))


@app.route("/grc/scope-of-work", methods=["GET", "POST"])
@login_required
def scope_of_work():
    assessment = get_selected_assessment()
    if assessment is None:
        flash("Please select or create an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    assessment_id = assessment["id"]
    conn = get_db_connection()
    scope = conn.execute("SELECT * FROM assessment_scopes WHERE assessment_id = ?", (assessment_id,)).fetchone()
    conn.close()
    if request.method == "GET":
        proposal = get_business_proposal(assessment_id)
        return render_template("scope_of_work.html", assessment=assessment, scope=scope, proposal=proposal, editing=request.args.get("edit") == "1")

    payload = (
        request.form.get("assessment_objective", "").strip(),
        request.form.get("in_scope_areas", "").strip(),
        request.form.get("out_of_scope_areas", "").strip(),
        "Yes" if request.form.get("network_infrastructure") else "No",
        "Yes" if request.form.get("web_applications") else "No",
        "Yes" if request.form.get("cloud_infrastructure") else "No",
        "Yes" if request.form.get("security_policies") else "No",
        "Yes" if request.form.get("physical_security") else "No",
        "Yes" if request.form.get("hr_systems") else "No",
        ", ".join(request.form.getlist("activities")),
        assessment_id,
    )
    conn = get_db_connection()
    if scope is None:
        conn.execute(
            """INSERT INTO assessment_scopes (scope_description, in_scope_assets, out_of_scope_assets,
            internal_network, web_applications, cloud_infrastructure, security_policies, physical_security,
            hr_systems, assessment_activities, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            payload,
        )
    else:
        conn.execute(
            """UPDATE assessment_scopes SET scope_description = ?, in_scope_assets = ?, out_of_scope_assets = ?,
            internal_network = ?, web_applications = ?, cloud_infrastructure = ?, security_policies = ?,
            physical_security = ?, hr_systems = ?, assessment_activities = ?, updated_at = CURRENT_TIMESTAMP
            WHERE assessment_id = ?""",
            payload,
        )
    conn.commit()
    conn.close()
    log_audit("Assessment update", "assessment_scope", assessment_id, "Updated scope of work.", assessment_id)
    flash("Scope of Work saved.", "success")
    return redirect(url_for("ai_grc"))


@app.route("/grc/methodology")
@login_required
def assessment_methodology():
    assessment = get_selected_assessment()
    if assessment is None:
        flash("Please select or create an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    proposal = get_business_proposal(assessment["id"])
    return render_template("assessment_methodology.html", assessment=assessment, proposal=proposal)


@app.route("/grc/new", methods=["GET", "POST"])
@login_required
def new_assessment():
    if request.method == "GET":
        return render_template("new_assessment.html")

    form_data = {
        "customer_name": request.form.get("customer_name", "").strip(),
        "industry": request.form.get("industry", "").strip(),
        "company_size": request.form.get("company_size", "").strip(),
        "assessment_type": request.form.get("assessment_type", "").strip(),
        "framework": request.form.get("framework", "").strip(),
        "start_date": request.form.get("start_date", "").strip(),
        "end_date": request.form.get("end_date", "").strip(),
        "pricing": request.form.get("pricing", "").strip(),
    }

    errors = validate_assessment_form(form_data)
    if errors:
        for error in errors:
            flash(error, "error")
        return render_template("new_assessment.html", form_data=form_data)

    try:
        customer_id = create_customer(form_data["customer_name"], form_data["industry"], form_data["company_size"])
        assessment_id = create_assessment(
            customer_id=customer_id,
            assessment_type=form_data["assessment_type"],
            framework=form_data["framework"],
            start_date=form_data["start_date"],
            end_date=form_data["end_date"],
            pricing=form_data["pricing"],
            status="In Progress",
            user_id=session["user_id"],
        )
        session["assessment_id"] = assessment_id
        new_ass = get_assessment_for_user(assessment_id, session["user_id"])
        if new_ass:
            create_default_proposal_for_assessment(new_ass)
        log_audit("Assessment creation", "assessment", assessment_id, f"Created {form_data['assessment_type']} assessment for {form_data['customer_name']}.", assessment_id)
        flash("Assessment created successfully.", "success")
        return redirect(url_for("grc_introduction"))
    except sqlite3.Error as exc:
        flash(f"Unable to save the assessment: {exc}", "error")
        return render_template("new_assessment.html", form_data=form_data)


@app.route("/grc/organization-profile", methods=["GET", "POST"])
@login_required
def organization_profile():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create a new assessment first.", "error")
        return redirect(url_for("new_assessment"))
    assessment_id = assessment["id"]
    row = get_db_connection().execute("SELECT * FROM organization_profiles WHERE assessment_id = ?", (assessment_id,)).fetchone()

    if request.method == "POST":
        payload = (
            request.form.get("legal_name", "").strip(),
            request.form.get("industry", "").strip(),
            request.form.get("employee_count", "").strip(),
            request.form.get("location", "").strip(),
            request.form.get("business_description", "").strip(),
            request.form.get("critical_business_functions", "").strip(),
            assessment_id,
        )
        conn = get_db_connection()
        if row is None:
            conn.execute(
                "INSERT INTO organization_profiles (legal_name, industry, employee_count, location, business_description, critical_business_functions, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        else:
            conn.execute(
                "UPDATE organization_profiles SET legal_name = ?, industry = ?, employee_count = ?, location = ?, business_description = ?, critical_business_functions = ?, updated_at = CURRENT_TIMESTAMP WHERE assessment_id = ?",
                payload[:6] + (assessment_id,),
            )
        conn.commit(); conn.close()
        flash("Organization profile saved.", "success")
        return redirect(url_for("organization_profile"))
    return render_template("grc_section.html", section_name="Organization Profile", record=row, section_key="organization_profile", assessment_id=assessment_id)


@app.route("/grc/assessment-scope", methods=["GET", "POST"])
@login_required
def assessment_scope():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create a new assessment first.", "error")
        return redirect(url_for("new_assessment"))
    assessment_id = assessment["id"]
    row = get_db_connection().execute("SELECT * FROM assessment_scopes WHERE assessment_id = ?", (assessment_id,)).fetchone()

    if request.method == "POST":
        payload = (
            request.form.get("scope_description", "").strip(),
            request.form.get("in_scope_assets", "").strip(),
            request.form.get("out_of_scope_assets", "").strip(),
            "Yes" if request.form.get("internal_network") else "No",
            "Yes" if request.form.get("web_applications") else "No",
            "Yes" if request.form.get("cloud_infrastructure") else "No",
            "Yes" if request.form.get("physical_security") else "No",
            "Yes" if request.form.get("hr_systems") else "No",
            assessment_id,
        )
        conn = get_db_connection()
        if row is None:
            conn.execute(
                "INSERT INTO assessment_scopes (scope_description, in_scope_assets, out_of_scope_assets, internal_network, web_applications, cloud_infrastructure, physical_security, hr_systems, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        else:
            conn.execute(
                "UPDATE assessment_scopes SET scope_description = ?, in_scope_assets = ?, out_of_scope_assets = ?, internal_network = ?, web_applications = ?, cloud_infrastructure = ?, physical_security = ?, hr_systems = ?, updated_at = CURRENT_TIMESTAMP WHERE assessment_id = ?",
                payload[:8] + (assessment_id,),
            )
        conn.commit(); conn.close()
        flash("Assessment scope saved.", "success")
        return redirect(url_for("assessment_scope"))
    return render_template("grc_section.html", section_name="Assessment Scope", record=row, section_key="scope", assessment_id=assessment_id)


@app.route("/grc/policies", methods=["GET", "POST"])
@login_required
def policies():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create a new assessment first.", "error")
        return redirect(url_for("new_assessment"))
    assessment_id = assessment["id"]

    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            policy_id = request.form.get("policy_id")
            if policy_id:
                conn = get_db_connection(); conn.execute("DELETE FROM policies WHERE id = ? AND assessment_id = ?", (policy_id, assessment_id)); conn.commit(); conn.close()
                flash("Policy deleted.", "success")
            return redirect(url_for("policies"))

        policy_id = request.form.get("policy_id")
        payload = (
            request.form.get("policy_name", "").strip(),
            request.form.get("description", "").strip(),
            request.form.get("owner", "").strip(),
            request.form.get("status", "Draft").strip(),
            request.form.get("version", "").strip(),
            request.form.get("review_date", "").strip(),
            assessment_id,
        )
        conn = get_db_connection()
        if policy_id:
            conn.execute(
                "UPDATE policies SET policy_name = ?, description = ?, owner = ?, status = ?, version = ?, review_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND assessment_id = ?",
                payload[:6] + (policy_id, assessment_id),
            )
        else:
            conn.execute("INSERT INTO policies (policy_name, description, owner, status, version, review_date, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close(); flash("Policy saved.", "success")
        return redirect(url_for("policies"))

    rows = get_table_rows("policies", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row
                break
    return render_template("grc_section.html", section_name="Security Policies", rows=rows, record=selected, section_key="policies", assessment_id=assessment_id)


@app.route("/grc/compliance-framework", methods=["GET", "POST"])
@login_required
def compliance_framework():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    row = get_db_connection().execute("SELECT * FROM compliance_frameworks WHERE assessment_id = ?", (assessment_id,)).fetchone()
    if request.method == "POST":
        conn = get_db_connection();
        framework = request.form.get("framework_name", "").strip() or request.form.get("framework", "").strip()
        description = request.form.get("description", "").strip(); status = request.form.get("status", "In Progress").strip()
        if row is None:
            conn.execute("INSERT INTO compliance_frameworks (assessment_id, framework_name, description, status) VALUES (?, ?, ?, ?)", (assessment_id, framework, description, status))
        else:
            conn.execute("UPDATE compliance_frameworks SET framework_name = ?, description = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE assessment_id = ?", (framework, description, status, assessment_id))
        conn.commit(); conn.close(); flash("Compliance framework saved.", "success"); return redirect(url_for("compliance_framework"))
    return render_template("grc_section.html", section_name="Compliance Framework", row=row, record=row, section_key="compliance_framework", assessment_id=assessment_id, default_framework=assessment["framework"])


@app.route("/grc/assets", methods=["GET", "POST"])
@login_required
def assets():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            asset_id = request.form.get("asset_id")
            if asset_id:
                conn = get_db_connection(); conn.execute("DELETE FROM assets WHERE id = ? AND assessment_id = ?", (asset_id, assessment_id)); conn.commit(); conn.close(); flash("Asset deleted.", "success")
            return redirect(url_for("assets"))
        asset_id = request.form.get("asset_id")
        payload = (
            request.form.get("asset_name", "").strip(), request.form.get("asset_type", "").strip(), request.form.get("ip_address", "").strip(),
            request.form.get("operating_system", "").strip(), request.form.get("owner", "").strip(), request.form.get("criticality", "Medium").strip(),
            request.form.get("status", "Active").strip(), request.form.get("description", "").strip(), assessment_id
        )
        conn = get_db_connection()
        if asset_id:
            conn.execute("UPDATE assets SET asset_name = ?, asset_type = ?, ip_address = ?, operating_system = ?, owner = ?, criticality = ?, status = ?, description = ? WHERE id = ? AND assessment_id = ?", payload[:8] + (asset_id, assessment_id))
        else:
            conn.execute("INSERT INTO assets (asset_name, asset_type, ip_address, operating_system, owner, criticality, status, description, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close(); flash("Asset saved.", "success"); return redirect(url_for("assets"))
    rows = get_table_rows("assets", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    return render_template("grc_section.html", section_name="Asset Management", rows=rows, record=selected, section_key="assets", assessment_id=assessment_id)


@app.route("/grc/risks", methods=["GET", "POST"])
@login_required
def risks():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            risk_id = request.form.get("risk_id")
            if risk_id:
                conn = get_db_connection(); conn.execute("DELETE FROM risks WHERE id = ? AND assessment_id = ?", (risk_id, assessment_id)); conn.commit(); conn.close(); flash("Risk deleted.", "success")
            return redirect(url_for("risks"))
        risk_id = request.form.get("risk_id")
        likelihood = request.form.get("likelihood", "1").strip(); impact = request.form.get("impact", "1").strip(); score, level = risk_score_and_level(likelihood, impact)
        payload = (
            request.form.get("title", "").strip(), request.form.get("description", "").strip(), request.form.get("category", "Other").strip(),
            likelihood, impact, score, level, request.form.get("owner", "").strip(), request.form.get("treatment", "").strip(),
            request.form.get("status", "Open").strip(), assessment_id
        )
        conn = get_db_connection()
        if risk_id:
            conn.execute("UPDATE risks SET title = ?, description = ?, category = ?, likelihood = ?, impact = ?, risk_score = ?, risk_level = ?, owner = ?, treatment = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND assessment_id = ?", payload[:10] + (risk_id, assessment_id))
        else:
            cursor = conn.execute("INSERT INTO risks (title, description, category, likelihood, impact, risk_score, risk_level, owner, treatment, status, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close()
        log_audit("Risk update" if risk_id else "Risk creation", "risk", risk_id or cursor.lastrowid, f"Saved risk: {payload[0] or 'Untitled risk'}.", assessment_id)
        flash("Risk saved.", "success"); return redirect(url_for("risks"))
    rows = get_table_rows("risks", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    return render_template("grc_section.html", section_name="Risk Identification", rows=rows, record=selected, section_key="risks", assessment_id=assessment_id)


@app.route("/grc/risk-assessment", methods=["GET", "POST"])
@login_required
def risk_assessment():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    rows = get_table_rows("risks", assessment_id)
    return render_template("grc_section.html", section_name="Risk Assessment", rows=rows, section_key="risk_assessment", assessment_id=assessment_id)


@app.route("/grc/risk-register")
@login_required
def risk_register():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    rows = get_table_rows("risks", assessment_id)
    return render_template("grc_section.html", section_name="Risk Register", rows=rows, section_key="risk_register", assessment_id=assessment_id)


@app.route("/grc/controls", methods=["GET", "POST"])
@login_required
def controls():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            control_id = request.form.get("control_id")
            if control_id:
                conn = get_db_connection(); conn.execute("DELETE FROM controls WHERE id = ? AND assessment_id = ?", (control_id, assessment_id)); conn.commit(); conn.close(); flash("Control deleted.", "success")
            return redirect(url_for("controls"))
        control_id = request.form.get("control_id")
        payload = (
            request.form.get("control_name", "").strip(), request.form.get("description", "").strip(), request.form.get("framework", "").strip(),
            request.form.get("control_category", "").strip(), request.form.get("owner", "").strip(), request.form.get("implementation_status", "Not Implemented").strip(),
            request.form.get("related_risk_id", "").strip() or None, assessment_id
        )
        conn = get_db_connection()
        if control_id:
            conn.execute("UPDATE controls SET control_name = ?, description = ?, framework = ?, control_category = ?, owner = ?, implementation_status = ?, related_risk_id = ? WHERE id = ? AND assessment_id = ?", payload[:7] + (control_id, assessment_id))
        else:
            cursor = conn.execute("INSERT INTO controls (control_name, description, framework, control_category, owner, implementation_status, related_risk_id, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close()
        log_audit("Control update" if control_id else "Control creation", "control", control_id or cursor.lastrowid, f"Saved control: {payload[0] or 'Untitled control'}.", assessment_id)
        flash("Control saved.", "success"); return redirect(url_for("controls"))
    rows = get_table_rows("controls", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    risk_rows = get_db_connection().execute("SELECT id, title FROM risks WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(); get_db_connection().close()
    return render_template("grc_section.html", section_name="Security Controls", rows=rows, record=selected, section_key="controls", assessment_id=assessment_id, risk_rows=risk_rows)


@app.route("/grc/control-testing", methods=["GET", "POST"])
@login_required
def control_testing():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            test_id = request.form.get("test_id")
            if test_id:
                conn = get_db_connection(); conn.execute("DELETE FROM control_tests WHERE id = ?", (test_id,)); conn.commit(); conn.close(); flash("Test record deleted.", "success")
            return redirect(url_for("control_testing"))
        control_id = request.form.get("control_id")
        conn = get_db_connection()
        control = conn.execute("SELECT id FROM controls WHERE id = ? AND assessment_id = ?", (control_id, assessment_id)).fetchone()
        if control is None:
            conn.close()
            flash("Select a control from the current assessment.", "error")
            return redirect(url_for("control_testing"))
        payload = (
            control_id, request.form.get("test_description", "").strip(), request.form.get("test_method", "").strip(),
            request.form.get("result", "NOT TESTED").strip(), request.form.get("tester", "").strip(), request.form.get("test_date", "").strip(),
            request.form.get("notes", "").strip()
        )
        cursor = conn.execute("INSERT INTO control_tests (control_id, test_description, test_method, result, tester, test_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); test_id = cursor.lastrowid; conn.close()
        log_audit("Control testing", "control_test", test_id, f"Recorded {payload[3]} test result for control {control_id}.", assessment_id)
        flash("Control test saved.", "success"); return redirect(url_for("control_testing"))
    control_rows = get_db_connection().execute("SELECT * FROM controls WHERE assessment_id = ?", (assessment_id,)).fetchall(); get_db_connection().close()
    tests = get_db_connection().execute("SELECT ct.*, c.control_name FROM control_tests ct JOIN controls c ON c.id = ct.control_id WHERE c.assessment_id = ? ORDER BY ct.test_date DESC", (assessment_id,)).fetchall(); get_db_connection().close()
    return render_template("grc_section.html", section_name="Control Testing", control_rows=control_rows, tests=tests, section_key="control_testing", assessment_id=assessment_id)


@app.route("/grc/evidence", methods=["GET", "POST"])
@login_required
def evidence_management():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            evidence_id = request.form.get("evidence_id")
            if evidence_id:
                conn = get_db_connection(); row = conn.execute("SELECT file_path FROM evidence WHERE id = ? AND assessment_id = ?", (evidence_id, assessment_id)).fetchone(); 
                if row and row["file_path"]:
                    path = Path(row["file_path"])
                    if path.exists(): path.unlink()
                conn.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,)); conn.commit(); conn.close(); flash("Evidence deleted.", "success")
            return redirect(url_for("evidence_management"))
        uploaded_file = request.files.get("file")
        file_path = None
        if uploaded_file and uploaded_file.filename:
            filename = secure_filename(uploaded_file.filename)
            safe_ext = os.path.splitext(filename)[1].lower()
            allowed = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".docx", ".log", ".csv", ".zip"}
            if safe_ext not in allowed:
                flash("Unsupported file extension.", "error"); return redirect(url_for("evidence_management"))
            if uploaded_file.content_length and uploaded_file.content_length > 10 * 1024 * 1024:
                flash("File is too large. Maximum size is 10MB.", "error"); return redirect(url_for("evidence_management"))
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            final_name = f"{int(time.time())}_{secure_filename(filename)}"
            target = UPLOAD_DIR / final_name
            uploaded_file.save(target)
            file_path = str(target)
        control_id = request.form.get("control_id") or None
        conn = get_db_connection()
        if control_id and conn.execute("SELECT id FROM controls WHERE id = ? AND assessment_id = ?", (control_id, assessment_id)).fetchone() is None:
            conn.close()
            flash("Select a control from the current assessment.", "error")
            return redirect(url_for("evidence_management"))
        cursor = conn.execute("INSERT INTO evidence (assessment_id, control_id, evidence_name, evidence_type, file_path, description, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?)", (
            assessment_id, control_id, request.form.get("evidence_name", "").strip(), request.form.get("evidence_type", "Other").strip(), file_path, request.form.get("description", "").strip(), request.form.get("uploaded_by", session.get("username", "System")).strip()
        )); conn.commit(); evidence_id = cursor.lastrowid; conn.close()
        log_audit("Evidence upload", "evidence", evidence_id, f"Saved evidence: {request.form.get('evidence_name', '').strip() or 'Unnamed evidence'}.", assessment_id)
        flash("Evidence saved.", "success"); return redirect(url_for("evidence_management"))
    rows = get_table_rows("evidence", assessment_id)
    controls = get_db_connection().execute("SELECT * FROM controls WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(); get_db_connection().close()
    return render_template("grc_section.html", section_name="Evidence Management", rows=rows, controls=controls, section_key="evidence", assessment_id=assessment_id)


@app.route("/grc/evidence/download/<int:evidence_id>")
@login_required
def download_evidence(evidence_id):
    conn = get_db_connection()
    row = conn.execute("SELECT e.* FROM evidence e JOIN assessments a ON a.id = e.assessment_id WHERE e.id = ? AND a.user_id = ?", (evidence_id, session["user_id"])).fetchone()
    conn.close()
    if row is None or not row["file_path"]:
        flash("Evidence not found or not authorized.", "error"); return redirect(url_for("evidence_management"))
    file_path = Path(row["file_path"])
    if not file_path.exists():
        flash("File no longer exists.", "error"); return redirect(url_for("evidence_management"))
    return send_file(file_path, as_attachment=True, download_name=file_path.name)


@app.route("/grc/vendors", methods=["GET", "POST"])
@login_required
def vendors():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            vendor_id = request.form.get("vendor_id")
            if vendor_id:
                conn = get_db_connection(); conn.execute("DELETE FROM vendors WHERE id = ? AND assessment_id = ?", (vendor_id, assessment_id)); conn.commit(); conn.close(); flash("Vendor deleted.", "success")
            return redirect(url_for("vendors"))
        vendor_id = request.form.get("vendor_id")
        payload = (
            request.form.get("vendor_name", "").strip(), request.form.get("service", "").strip(), request.form.get("criticality", "Medium").strip(),
            request.form.get("risk_level", "Medium").strip(), request.form.get("assessment_status", "Not Started").strip(), request.form.get("notes", "").strip(), assessment_id
        )
        conn = get_db_connection()
        if vendor_id:
            conn.execute("UPDATE vendors SET vendor_name = ?, service = ?, criticality = ?, risk_level = ?, assessment_status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND assessment_id = ?", payload[:6] + (vendor_id, assessment_id))
        else:
            conn.execute("INSERT INTO vendors (vendor_name, service, criticality, risk_level, assessment_status, notes, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close(); flash("Vendor saved.", "success"); return redirect(url_for("vendors"))
    rows = get_table_rows("vendors", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    return render_template("grc_section.html", section_name="Third-Party / Vendor Risk", rows=rows, record=selected, section_key="vendors", assessment_id=assessment_id)


@app.route("/grc/findings", methods=["GET", "POST"])
@login_required
def findings():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            finding_id = request.form.get("finding_id")
            if finding_id:
                conn = get_db_connection(); conn.execute("DELETE FROM findings WHERE id = ? AND assessment_id = ?", (finding_id, assessment_id)); conn.commit(); conn.close(); flash("Finding deleted.", "success")
            return redirect(url_for("findings"))
        finding_id = request.form.get("finding_id")
        payload = (
            request.form.get("title", "").strip(), request.form.get("description", "").strip(), request.form.get("source", "").strip(),
            request.form.get("severity", "Low").strip(), request.form.get("ip_address", "").strip(), request.form.get("port", "").strip(),
            request.form.get("service", "").strip(), request.form.get("status", "Open").strip(), request.form.get("recommendation", "").strip(), assessment_id
        )
        conn = get_db_connection()
        if finding_id:
            conn.execute("UPDATE findings SET title = ?, description = ?, source = ?, severity = ?, ip_address = ?, port = ?, service = ?, status = ?, recommendation = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND assessment_id = ?", payload[:9] + (finding_id, assessment_id))
        else:
            cursor = conn.execute("INSERT INTO findings (title, description, source, severity, ip_address, port, service, status, recommendation, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close()
        log_audit("Finding creation" if not finding_id else "Finding update", "finding", finding_id or cursor.lastrowid, f"Saved finding: {payload[0] or 'Untitled finding'}.", assessment_id)
        flash("Finding saved.", "success"); return redirect(url_for("findings"))
    rows = get_table_rows("findings", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    return render_template("grc_section.html", section_name="Security Findings", rows=rows, record=selected, section_key="findings", assessment_id=assessment_id)


@app.route("/grc/remediation", methods=["GET", "POST"])
@login_required
def remediation():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            remediation_id = request.form.get("remediation_id")
            if remediation_id:
                conn = get_db_connection(); conn.execute("DELETE FROM remediation_actions WHERE id = ? AND assessment_id = ?", (remediation_id, assessment_id)); conn.commit(); conn.close(); flash("Remediation action deleted.", "success")
            return redirect(url_for("remediation"))
        remediation_id = request.form.get("remediation_id")
        payload = (
            request.form.get("finding_id") or None, request.form.get("risk_id") or None,
            request.form.get("action_text", "").strip(), request.form.get("owner", "").strip(), request.form.get("priority", "Medium").strip(),
            request.form.get("due_date", "").strip(), request.form.get("status", "Open").strip(), request.form.get("notes", "").strip(), assessment_id
        )
        conn = get_db_connection()
        if remediation_id:
            conn.execute("UPDATE remediation_actions SET finding_id = ?, risk_id = ?, action = ?, owner = ?, priority = ?, due_date = ?, status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND assessment_id = ?", payload[:8] + (remediation_id, assessment_id))
        else:
            cursor = conn.execute("INSERT INTO remediation_actions (finding_id, risk_id, action, owner, priority, due_date, status, notes, assessment_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", payload)
        conn.commit(); conn.close()
        log_audit("Remediation update", "remediation_action", remediation_id or cursor.lastrowid, "Saved remediation action.", assessment_id)
        flash("Remediation action saved.", "success"); return redirect(url_for("remediation"))
    rows = get_table_rows("remediation_actions", assessment_id)
    selected = None
    if request.args.get("edit_id"):
        for row in rows:
            if str(row["id"]) == request.args.get("edit_id"):
                selected = row; break
    findings = get_db_connection().execute("SELECT id, title FROM findings WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(); get_db_connection().close()
    risks = get_db_connection().execute("SELECT id, title FROM risks WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(); get_db_connection().close()
    return render_template("grc_section.html", section_name="Remediation & Reporting", rows=rows, record=selected, section_key="remediation", assessment_id=assessment_id, findings=findings, risks=risks)


@app.route("/grc/report")
@login_required
def grc_report():
    assessment = get_latest_assessment(); assessment_id = assessment["id"] if assessment else None
    if assessment is None:
        flash("Create a new assessment first.", "error"); return redirect(url_for("new_assessment"))
    return redirect(url_for("reporting"))


@app.route("/grc/executive-summary")
@login_required
def executive_summary():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create or select an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    summary = get_report_summary(assessment["id"])
    return render_template(
        "executive_summary.html",
        assessment=assessment,
        summary=summary,
        completion_percentage=get_completion_percentage(assessment["id"]),
        recommendations=get_recommendations(assessment["id"], summary),
    )


@app.route("/grc/reports")
@login_required
def reporting():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create or select an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    assessment_id = assessment["id"]
    summary = get_report_summary(assessment_id)
    conn = get_db_connection()
    report_data = {
        "risks": conn.execute("SELECT * FROM risks WHERE assessment_id = ? ORDER BY risk_score DESC, id DESC", (assessment_id,)).fetchall(),
        "controls": conn.execute("SELECT * FROM controls WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(),
        "findings": conn.execute("SELECT * FROM findings WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(),
        "remediation": conn.execute("SELECT * FROM remediation_actions WHERE assessment_id = ? ORDER BY id DESC", (assessment_id,)).fetchall(),
        "frameworks": conn.execute("SELECT * FROM compliance_frameworks WHERE assessment_id = ?", (assessment_id,)).fetchall(),
    }
    conn.close()
    return render_template(
        "grc_report.html", assessment=assessment, summary=summary, report_data=report_data,
        completion_percentage=get_completion_percentage(assessment_id),
        recommendations=get_recommendations(assessment_id, summary),
    )


@app.route("/grc/audit-logs")
@login_required
def audit_logs():
    assessment = get_latest_assessment()
    if assessment is None:
        flash("Create or select an assessment first.", "error")
        return redirect(url_for("assessment_list"))
    conn = get_db_connection()
    logs = conn.execute(
        """SELECT l.*, u.username FROM audit_logs l JOIN users u ON u.id = l.user_id
           WHERE l.user_id = ? AND (l.assessment_id = ? OR l.assessment_id IS NULL)
           ORDER BY l.timestamp DESC, l.id DESC""",
        (session["user_id"], assessment["id"]),
    ).fetchall()
    conn.close()
    return render_template("audit_logs.html", assessment=assessment, logs=logs)


# Gemini-backed AI review -----------------------------------------------------
#
# Design goals for the AI GRC assistant:
#   1. Gemini understands natural-language requests against the CURRENT
#      assessment.
#   2. The backend, not Gemini, resolves the current assessment/customer IDs.
#   3. Every UPDATE must have a real, non-empty value before SQL runs.
#   4. A malformed LLM response is repaired from the user's request where
#      that repair is deterministic.
#   5. The AI can modify existing GRC records and can create supported
#      assessment-owned records when the user explicitly asks to add something.
#   6. No arbitrary IDs, deletes, credentials, file paths, or cross-assessment
#      writes are allowed.

AI_MAX_SAFE_ID = 9_223_372_036_854_775_807


AI_CHANGE_RULES = {
    "assessments": {
        "assessment_type", "framework", "start_date", "end_date", "pricing", "status"
    },
    "customers": {"name", "industry", "company_size"},
    "organization_profiles": {
        "legal_name", "industry", "employee_count", "location",
        "business_description", "critical_business_functions"
    },
    "assessment_scopes": {
        "scope_description", "in_scope_assets", "out_of_scope_assets",
        "internal_network", "web_applications", "cloud_infrastructure",
        "security_policies", "physical_security", "hr_systems",
        "assessment_activities"
    },
    "policies": {
        "policy_name", "description", "owner", "status", "version", "review_date"
    },
    "compliance_frameworks": {"framework_name", "description", "status"},
    "assets": {
        "asset_name", "asset_type", "ip_address", "operating_system",
        "owner", "criticality", "status", "description"
    },
    "risks": {
        "title", "description", "category", "likelihood", "impact",
        "owner", "treatment", "status"
    },
    "controls": {
        "control_name", "description", "framework", "control_category",
        "owner", "implementation_status", "related_risk_id"
    },
    "control_tests": {
        "test_description", "test_method", "result", "tester", "test_date", "notes"
    },
    "evidence": {
        "evidence_name", "evidence_type", "description"
    },
    "findings": {
        "title", "description", "source", "severity", "ip_address",
        "port", "service", "status", "recommendation"
    },
    "remediation_actions": {
        "finding_id", "risk_id", "action", "owner", "priority",
        "due_date", "status", "notes"
    },
    "vendors": {
        "vendor_name", "service", "criticality", "risk_level",
        "assessment_status", "notes"
    },
}

AI_CREATE_REQUIRED = {
    "policies": {"policy_name", "description"},
    "assets": {"asset_name", "asset_type"},
    "risks": {"title", "description"},
    "controls": {"control_name", "description"},
    "findings": {"title", "description"},
    "remediation_actions": {"action"},
    "vendors": {"vendor_name", "service"},
}


def _safe_ai_record_id(value):
    """Return a normal SQLite integer ID or None."""
    if isinstance(value, bool) or value is None:
        return None

    if isinstance(value, int):
        return value if 1 <= value <= AI_MAX_SAFE_ID else None

    if isinstance(value, str):
        value = value.strip()
        if not value or not value.isdigit() or len(value) > 19:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if 1 <= number <= AI_MAX_SAFE_ID else None

    return None


def _json_safe_ai_context(value):
    """
    Keep database IDs as strings before sending them to Gemini.

    This is intentional: an LLM never needs Python integer IDs, and keeping
    them as strings prevents huge/malformed integer payloads from reaching
    Python's JSON integer parser.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe_ai_context(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_ai_context(v) for v in value]
    if isinstance(value, int):
        return str(value)
    return value


def _safe_json_parse_int(value):
    """Never materialize an arbitrarily large LLM-generated integer."""
    if len(value) > 19:
        return value
    try:
        return int(value)
    except (ValueError, OverflowError):
        return value


def _rows_as_dicts(conn, table_name, assessment_id, limit=250):
    """Return assessment-scoped records for Gemini."""
    if table_name not in AI_CHANGE_RULES:
        return []

    if table_name == "customers":
        rows = conn.execute(
            """
            SELECT c.*
            FROM customers c
            JOIN assessments a ON a.customer_id = c.id
            WHERE a.id = ? AND a.user_id = ?
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (assessment_id, session.get("user_id"), limit),
        ).fetchall()

    elif table_name == "assessments":
        rows = conn.execute(
            """
            SELECT *
            FROM assessments
            WHERE id = ? AND user_id = ?
            LIMIT ?
            """,
            (assessment_id, session.get("user_id"), limit),
        ).fetchall()

    elif table_name == "control_tests":
        rows = conn.execute(
            """
            SELECT ct.*
            FROM control_tests ct
            JOIN controls c ON c.id = ct.control_id
            WHERE c.assessment_id = ?
            ORDER BY ct.id DESC
            LIMIT ?
            """,
            (assessment_id, limit),
        ).fetchall()

    else:
        rows = conn.execute(
            f"""
            SELECT *
            FROM {table_name}
            WHERE assessment_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (assessment_id, limit),
        ).fetchall()

    return [dict(row) for row in rows]


def get_ai_review_context(assessment_id):
    """Build a complete, assessment-scoped AI context."""
    conn = get_db_connection()

    assessment = conn.execute(
        """
        SELECT
            a.*,
            c.name AS customer_name,
            c.industry AS customer_industry,
            c.company_size AS customer_company_size
        FROM assessments a
        JOIN customers c ON c.id = a.customer_id
        WHERE a.id = ? AND a.user_id = ?
        """,
        (assessment_id, session.get("user_id")),
    ).fetchone()

    if assessment is None:
        conn.close()
        return None

    context = {"assessment": dict(assessment)}

    for table_name in AI_CHANGE_RULES:
        if table_name != "assessments":
            context[table_name] = _rows_as_dicts(
                conn, table_name, assessment_id, limit=250
            )

    conn.close()
    return context


def _coerce_ai_value(table_name, field, value):
    """
    Normalize a proposed database value.

    UPDATE values are never allowed to become NULL accidentally. This fixes
    the previous `NOT NULL constraint failed: assessments.status` class of
    errors.
    """
    if value is None:
        raise ValueError(f"A new value is required for {table_name}.{field}.")

    if isinstance(value, str):
        value = value.strip()

    if value == "":
        raise ValueError(f"A non-empty value is required for {table_name}.{field}.")

    if table_name == "risks" and field in {"likelihood", "impact"}:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be an integer from 1 to 5.")
        if number < 1 or number > 5:
            raise ValueError(f"{field} must be between 1 and 5.")
        return number

    if table_name == "assessments" and field == "pricing":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("pricing must be numeric.")
        if number < 0:
            raise ValueError("pricing cannot be negative.")
        return number

    if table_name == "controls" and field == "related_risk_id":
        if value in ("", None):
            return None
        parsed = _safe_ai_record_id(value)
        if parsed is None:
            raise ValueError("related_risk_id must be a valid database ID.")
        return parsed

    if table_name == "remediation_actions" and field in {"finding_id", "risk_id"}:
        if value in ("", None):
            return None
        parsed = _safe_ai_record_id(value)
        if parsed is None:
            raise ValueError(f"{field} must be a valid database ID.")
        return parsed

    if table_name == "organization_profiles" and field == "employee_count":
        return str(value).strip()

    return str(value).strip()


def _recalculate_risk(conn, risk_id):
    row = conn.execute(
        "SELECT likelihood, impact FROM risks WHERE id = ?",
        (risk_id,),
    ).fetchone()
    if row is None:
        return

    likelihood = int(row["likelihood"] or 1)
    impact = int(row["impact"] or 1)
    score = likelihood * impact

    if score >= 15:
        level = "Critical"
    elif score >= 8:
        level = "High"
    elif score >= 4:
        level = "Medium"
    else:
        level = "Low"

    conn.execute(
        """
        UPDATE risks
        SET risk_score = ?, risk_level = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (score, level, risk_id),
    )


def _record_belongs_to_assessment(conn, table_name, record_id, assessment_id):
    """Strict ownership check for every AI database operation."""
    if table_name == "customers":
        return conn.execute(
            """
            SELECT c.id
            FROM customers c
            JOIN assessments a ON a.customer_id = c.id
            WHERE c.id = ? AND a.id = ? AND a.user_id = ?
            """,
            (record_id, assessment_id, session.get("user_id")),
        ).fetchone() is not None

    if table_name == "assessments":
        return conn.execute(
            """
            SELECT id
            FROM assessments
            WHERE id = ? AND user_id = ?
            """,
            (record_id, session.get("user_id")),
        ).fetchone() is not None

    if table_name == "control_tests":
        return conn.execute(
            """
            SELECT ct.id
            FROM control_tests ct
            JOIN controls c ON c.id = ct.control_id
            WHERE ct.id = ? AND c.assessment_id = ?
            """,
            (record_id, assessment_id),
        ).fetchone() is not None

    return conn.execute(
        f"""
        SELECT id
        FROM {table_name}
        WHERE id = ? AND assessment_id = ?
        """,
        (record_id, assessment_id),
    ).fetchone() is not None


def _normalise_change(change):
    """Accept current and older proposal formats."""
    if not isinstance(change, dict):
        return None, "Invalid change object."

    action = str(change.get("action") or "").lower().strip()
    table = str(change.get("table") or "").strip()

    if not table:
        return None, "Missing table."

    if action not in {"update", "create"}:
        if change.get("record_id") is not None and (
            change.get("field") or change.get("new_value") is not None
        ):
            action = "update"
        elif isinstance(change.get("data"), dict):
            action = "create"

    if action not in {"update", "create"}:
        return None, "Unknown or missing action."

    return action, None


def _field_aliases():
    """Natural-language aliases used when Gemini omits an exact field."""
    return {
        "assessments": {
            "assessment_type": ("assessment type", "assessment kind", "type"),
            "framework": ("framework", "compliance framework", "standard"),
            "start_date": ("start date", "assessment start", "starting date"),
            "end_date": ("end date", "assessment end", "ending date"),
            "pricing": ("pricing", "price", "fee", "cost", "assessment fee"),
            "status": ("status", "assessment status", "assessment state"),
        },
        "customers": {
            "name": ("customer name", "company name", "client name", "customer"),
            "industry": ("industry", "sector", "business sector"),
            "company_size": (
                "company size", "team size", "organization size",
                "organisation size", "headcount", "employee count",
                "number of employees", "employees",
            ),
        },
        "organization_profiles": {
            "legal_name": ("legal name", "registered name"),
            "industry": ("industry", "sector"),
            "employee_count": (
                "employee count", "number of employees", "headcount",
                "team size", "company size"
            ),
            "location": ("location", "office location", "headquarters"),
            "business_description": ("business description", "business"),
            "critical_business_functions": (
                "critical business functions", "critical functions"
            ),
        },
        "assessment_scopes": {
            "scope_description": ("scope", "scope description", "objective", "assessment objective"),
            "in_scope_assets": ("in scope", "in-scope", "included assets", "scope assets"),
            "out_of_scope_assets": ("out of scope", "out-of-scope", "excluded assets"),
            "internal_network": ("internal network", "network infrastructure", "network"),
            "web_applications": ("web applications", "web apps"),
            "cloud_infrastructure": ("cloud infrastructure", "cloud"),
            "security_policies": ("security policies",),
            "physical_security": ("physical security",),
            "hr_systems": ("hr systems", "human resources"),
            "assessment_activities": ("assessment activities", "activities"),
        },
        "policies": {
            "policy_name": ("policy name", "policy"),
            "description": ("description",),
            "owner": ("owner",),
            "status": ("policy status", "status"),
            "version": ("version",),
            "review_date": ("review date",),
        },
        "compliance_frameworks": {
            "framework_name": ("framework name", "compliance framework", "framework"),
            "description": ("description",),
            "status": ("framework status", "status"),
        },
        "assets": {
            "asset_name": ("asset name", "asset"),
            "asset_type": ("asset type", "type"),
            "ip_address": ("ip address", "ip"),
            "operating_system": ("operating system", "os"),
            "owner": ("asset owner", "owner"),
            "criticality": ("criticality", "criticality level"),
            "status": ("asset status", "status"),
            "description": ("asset description", "description"),
        },
        "risks": {
            "title": ("risk title", "risk name", "risk"),
            "description": ("risk description", "description"),
            "category": ("risk category", "category"),
            "likelihood": ("likelihood", "probability"),
            "impact": ("impact", "business impact"),
            "owner": ("risk owner", "owner"),
            "treatment": ("treatment", "risk treatment", "mitigation"),
            "status": ("risk status", "status"),
        },
        "controls": {
            "control_name": ("control name", "control"),
            "description": ("control description", "description"),
            "framework": ("control framework", "framework"),
            "control_category": ("control category", "category"),
            "owner": ("control owner", "owner"),
            "implementation_status": (
                "implementation status", "control status", "implemented"
            ),
            "related_risk_id": ("related risk", "related risk id"),
        },
        "control_tests": {
            "test_description": ("test description", "test"),
            "test_method": ("test method", "testing method"),
            "result": ("test result", "result", "pass", "fail"),
            "tester": ("tester", "tested by"),
            "test_date": ("test date", "testing date"),
            "notes": ("test notes", "notes"),
        },
        "evidence": {
            "evidence_name": ("evidence name", "evidence"),
            "evidence_type": ("evidence type",),
            "description": ("evidence description", "description"),
        },
        "findings": {
            "title": ("finding title", "finding name", "finding"),
            "description": ("finding description", "description"),
            "source": ("source",),
            "severity": ("severity",),
            "ip_address": ("ip address", "ip"),
            "port": ("port",),
            "service": ("service",),
            "status": ("finding status", "status"),
            "recommendation": ("recommendation",),
        },
        "remediation_actions": {
            "finding_id": ("finding id",),
            "risk_id": ("risk id",),
            "action": ("remediation action", "action"),
            "owner": ("remediation owner", "owner"),
            "priority": ("priority",),
            "due_date": ("due date",),
            "status": ("remediation status", "status"),
            "notes": ("remediation notes", "notes"),
        },
        "vendors": {
            "vendor_name": ("vendor name", "vendor"),
            "service": ("service",),
            "criticality": ("vendor criticality", "criticality"),
            "risk_level": ("risk level", "vendor risk"),
            "assessment_status": ("assessment status", "vendor status"),
            "notes": ("vendor notes", "notes"),
        },
    }


def _infer_ai_update_field(change, prompt=""):
    """Infer a field from the request when Gemini omitted it."""
    table = str(change.get("table") or "").strip()
    if change.get("field"):
        return str(change["field"]).strip()

    text = (
        f"{prompt} "
        f"{change.get('reason', '')} "
        f"{change.get('data', '')}"
    ).lower()

    aliases = _field_aliases().get(table, {})
    matches = []

    for field, terms in aliases.items():
        for term in terms:
            if term in text:
                matches.append((len(term), field))
                break

    if not matches:
        return ""

    matches.sort(reverse=True)
    return matches[0][1]


def _extract_requested_value(prompt, field=None, current_value=None):
    """
    Extract the user's intended new value for common natural-language
    update requests.

    Examples:
      "change framework from ISO 27001 to SOC 2" -> SOC 2
      "set status to Completed" -> Completed
      "make company size 250-300" -> 250-300
    """
    text = str(prompt or "").strip()
    if not text:
        return None

    # Explicit from -> to is the safest form.
    patterns = [
        r"\bfrom\b\s*[\"']?(.+?)[\"']?\s+\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bset\b.+?\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bchange\b.+?\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bupdate\b.+?\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bswitch\b.+?\bto\b\s*[\"']?(.+?)[\"']?\s*$",
        r"\bmake\b.+?\bto\b\s*[\"']?(.+?)[\"']?\s*$",
    ]

    # Prefer the from/to form.
    match = re.search(patterns[0], text, flags=re.IGNORECASE)
    if match:
        value = match.group(2).strip().strip("\"'")
        if value:
            return value.rstrip(".!?")

    for pattern in patterns[1:]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip().strip("\"'")
            if value:
                return value.rstrip(".!?")

    # "change X: Y" / "set X = Y" / "X should be Y"
    patterns2 = [
        r"\b(?:change|update|set)\b.+?\b(?:to|=|as)\s*[\"']?(.+?)[\"']?\s*$",
        r"\bshould\s+be\s+[\"']?(.+?)[\"']?\s*$",
    ]
    for pattern in patterns2:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip().strip("\"'")
            if value:
                return value.rstrip(".!?")

    # For common numeric fields, look for a value directly in the request.
    if field in {"pricing", "likelihood", "impact", "port"}:
        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
        if numbers:
            return numbers[-1]

    if field == "company_size":
        match = re.search(r"\b\d+\s*-\s*\d+\b", text)
        if match:
            return match.group(0)

    return None


def _guess_table_from_prompt(prompt, context):
    """Best-effort table inference when Gemini omits/garbles the table."""
    text = str(prompt or "").lower()

    explicit = [
        ("remediation", "remediation_actions"),
        ("remediation action", "remediation_actions"),
        ("control test", "control_tests"),
        ("testing", "control_tests"),
        ("evidence", "evidence"),
        ("finding", "findings"),
        ("vendor", "vendors"),
        ("supplier", "vendors"),
        ("control", "controls"),
        ("risk", "risks"),
        ("asset", "assets"),
        ("policy", "policies"),
        ("compliance framework", "compliance_frameworks"),
        ("scope", "assessment_scopes"),
        ("organization profile", "organization_profiles"),
        ("company profile", "organization_profiles"),
        ("customer", "customers"),
        ("company name", "customers"),
        ("company size", "customers"),
        ("assessment", "assessments"),
        ("engagement", "assessments"),
    ]

    for keyword, table in explicit:
        if keyword in text:
            return table

    # If the user clearly names a field, use the table containing that field.
    candidates = []
    for table, fields in AI_CHANGE_RULES.items():
        for field, terms in _field_aliases().get(table, {}).items():
            if any(term in text for term in terms):
                candidates.append(table)
                break

    if len(set(candidates)) == 1:
        return candidates[0]

    return None


def _best_record_id(conn, table, assessment_id, prompt, change):
    """
    Resolve the target record safely.

    For assessments/customers, the current page already determines the record.
    For assessment-owned child tables, use a valid Gemini ID if it belongs to
    this assessment; otherwise try to match a named record from the request.
    """
    if table == "assessments":
        return _safe_ai_record_id(assessment_id)

    if table == "customers":
        row = conn.execute(
            """
            SELECT a.customer_id
            FROM assessments a
            WHERE a.id = ? AND a.user_id = ?
            LIMIT 1
            """,
            (assessment_id, session.get("user_id")),
        ).fetchone()
        return _safe_ai_record_id(row["customer_id"] if row else None)

    candidate = _safe_ai_record_id(change.get("record_id"))
    if candidate is not None and _record_belongs_to_assessment(
        conn, table, candidate, assessment_id
    ):
        return candidate

    # Build a searchable description from the user request and Gemini's reason.
    search_text = (
        f"{prompt} {change.get('reason', '')} "
        f"{change.get('new_value', '')}"
    ).lower()

    if table == "control_tests":
        rows = conn.execute(
            """
            SELECT ct.*
            FROM control_tests ct
            JOIN controls c ON c.id = ct.control_id
            WHERE c.assessment_id = ?
            ORDER BY ct.id DESC
            """,
            (assessment_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE assessment_id = ? ORDER BY id DESC",
            (assessment_id,),
        ).fetchall()

    # If there is exactly one record in that table, it is the unambiguous
    # target for a natural-language update.
    if len(rows) == 1:
        return int(rows[0]["id"])

    # Match names/titles/descriptions mentioned by the user.
    identity_fields = {
        "policies": ("policy_name", "description"),
        "assets": ("asset_name", "description"),
        "risks": ("title", "description"),
        "controls": ("control_name", "description"),
        "findings": ("title", "description"),
        "vendors": ("vendor_name", "service", "notes"),
        "remediation_actions": ("action", "notes"),
        "organization_profiles": ("legal_name", "business_description"),
        "assessment_scopes": ("scope_description", "in_scope_assets"),
        "compliance_frameworks": ("framework_name", "description"),
        "control_tests": ("test_description", "test_method", "notes"),
        "evidence": ("evidence_name", "description"),
    }.get(table, ())

    scored = []
    for row in rows:
        score = 0
        haystack_parts = []
        for field in identity_fields:
            if field in row.keys() and row[field]:
                haystack_parts.append(str(row[field]).lower())

        haystack = " ".join(haystack_parts)
        if not haystack:
            continue

        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", haystack):
            if token in search_text:
                score += 1

        # Exact phrases are much stronger.
        for part in haystack_parts:
            if len(part) >= 4 and part in search_text:
                score += 10

        if score:
            scored.append((score, int(row["id"])))

    if scored:
        scored.sort(reverse=True)
        # Only accept the best match if it is clearly better than the next one.
        if len(scored) == 1 or scored[0][0] > scored[1][0]:
            return scored[0][1]

    return None


def _repair_ai_changes(changes, prompt="", assessment_id=None):
    """
    Normalize and repair Gemini output.

    The repair layer is deliberately deterministic:
    - current assessment/customer IDs are resolved by the server;
    - missing fields are inferred from the request;
    - missing new_value is extracted from the request when possible;
    - oversized IDs never reach SQLite;
    - invalid/empty updates are not executed.
    """
    repaired = []
    prompt_text = str(prompt or "").strip()

    for raw in changes or []:
        if not isinstance(raw, dict):
            continue

        change = dict(raw)
        table = str(change.get("table") or "").strip()

        if table not in AI_CHANGE_RULES:
            guessed = _guess_table_from_prompt(prompt_text, {})
            if guessed:
                change["table"] = guessed
                table = guessed

        action = str(change.get("action") or "").lower().strip()
        if action not in {"update", "create"}:
            action, _ = _normalise_change(change)
            if action:
                change["action"] = action

        if change.get("action") == "update":
            field = change.get("field") or _infer_ai_update_field(
                change, prompt_text
            )
            if field:
                change["field"] = field

            if change.get("new_value") is None or str(change.get("new_value")).strip() == "":
                extracted = _extract_requested_value(
                    prompt_text,
                    field=field,
                    current_value=None,
                )
                if extracted is not None:
                    change["new_value"] = extracted

            # Current assessment/customer IDs are always server-resolved.
            if table == "assessments" and assessment_id is not None:
                change["record_id"] = str(assessment_id)
            elif table == "customers":
                change.pop("record_id", None)
            else:
                normalized = _safe_ai_record_id(change.get("record_id"))
                if normalized is not None:
                    change["record_id"] = str(normalized)

        repaired.append(change)

    # If Gemini returned no executable change, derive a single deterministic
    # update directly from the user's request when the request clearly maps to
    # an existing field/value.
    usable_updates = [
        c for c in repaired
        if isinstance(c, dict)
        and c.get("action") == "update"
        and c.get("table") in AI_CHANGE_RULES
        and c.get("field")
        and c.get("new_value") not in (None, "")
    ]

    if not usable_updates:
        table = _guess_table_from_prompt(prompt_text, {})
        if table:
            field = _infer_ai_update_field({"table": table}, prompt_text)
            new_value = _extract_requested_value(prompt_text, field=field)
            if field and new_value is not None:
                fallback = {
                    "action": "update",
                    "table": table,
                    "field": field,
                    "new_value": new_value,
                    "reason": (
                        f"Deterministic repair of the user's request: "
                        f"{prompt_text}"
                    ),
                }
                if table == "assessments" and assessment_id is not None:
                    fallback["record_id"] = str(assessment_id)
                repaired.insert(0, fallback)

    return repaired


def detect_ai_mode(assessment_id=None, prompt="", mode_override=None):
    """Return the explicit AI mode to use: generate or modify."""
    override = (mode_override or "").strip().lower()
    if override in {"generate", "modify"}:
        return override

    text = (prompt or "").strip()
    if assessment_id is None or not text:
        return "generate"

    if re.search(
        r"\b(create|generate|draft|build|develop)\b.*\b(complete|full|comprehensive|entire|detailed)\b.*\b(assessment|risk assessment|security assessment|grc assessment|cybersecurity assessment|audit)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return "generate"

    if re.search(
        r"\b(create|generate|draft|build|develop)\b.*\b(assessment|risk assessment|security assessment|grc assessment|cybersecurity assessment|audit)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return "generate"

    return "modify"


def _create_gemini_client():
    """Create a Gemini client using the app's existing transport configuration."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured in the .env file.")

    http_options = types.HttpOptions(
        client_args={"trust_env": False},
        async_client_args={"trust_env": False},
        timeout=GEMINI_REQUEST_TIMEOUT_MS,
    )
    return genai.Client(api_key=GEMINI_API_KEY, http_options=http_options)


def _extract_gemini_output_text(response):
    """Read text payload from Gemini SDK response variants."""
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    outputs = getattr(response, "outputs", None) or []
    for output in reversed(outputs):
        text_value = getattr(output, "text", None)
        if text_value:
            return text_value
        if isinstance(output, dict) and output.get("text"):
            return output["text"]

    fallback_text = getattr(response, "text", None)
    if fallback_text:
        return fallback_text
    return ""


def _text_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []

    parts = []
    for chunk in re.split(r"[\n,;]", text):
        cleaned = chunk.strip(" -\t")
        if cleaned:
            parts.append(cleaned)
    return parts


def _valid_timeline_text(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"\b(week|weeks|month|months|day|days|phase|phases|sprint|quarter|q[1-4])\b", text, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"\d", text))


CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "SAR": "﷼",
    "AED": "د.إ",
}


def _clean_amount_val(val):
    if val is None:
        return None
    if isinstance(val, dict):
        val = val.get("amount") or val.get("contribution") or val.get("budget") or val.get("pricing_amount")
    s = str(val).strip()
    if not s or s.lower() in ("none", "to be confirmed", "tbd", "0", "null", "undefined"):
        return None
    return s


def format_currency_amount(amount, currency="INR"):
    """Format numeric budget amount with the specified currency symbol.
    currency: 'INR', 'USD', 'SAR', or 'AED'
    Returns formatted string like '₹450,000', '$15,000', '﷼50,000', or 'د.إ25,000'.
    """
    curr = str(currency or "INR").strip().upper()
    if curr not in CURRENCY_SYMBOLS:
        curr = "INR"

    symbol = CURRENCY_SYMBOLS[curr]

    if amount is None or str(amount).strip() in ("", "None", "To be confirmed"):
        return "To be confirmed"

    raw_str = str(amount).strip()

    # Check if raw_str already starts with a currency symbol
    for c, sym in CURRENCY_SYMBOLS.items():
        if raw_str.startswith(sym):
            if c == curr:
                num_part = raw_str[len(sym):].strip()
                if num_part and re.match(r"^[\d,]+(\.\d+)?$", num_part):
                    return raw_str
            else:
                raw_str = raw_str[len(sym):].strip()
            break

    cleaned = re.sub(r"[^\d.]", "", raw_str)
    if not cleaned:
        return "To be confirmed"

    try:
        val = float(cleaned)
        if val.is_integer():
            formatted_num = f"{int(val):,}"
        else:
            formatted_num = f"{val:,.2f}"
        return f"{symbol}{formatted_num}"
    except (ValueError, TypeError):
        return f"{symbol}{cleaned}"


def normalize_commercials_content(content_or_dict, currency="INR"):
    """Normalize any Commercials section representation (dict, stringified dict, JSON, HTML, text)
    into clean, human-readable HTML for user-facing rendering, editors, and exports.
    """
    curr = str(currency or "INR").strip().upper()
    if curr not in CURRENCY_SYMBOLS:
        curr = "INR"

    raw_amount = None
    description = None
    model = "Fixed Price"

    if isinstance(content_or_dict, dict):
        raw_amount = content_or_dict.get("amount") or content_or_dict.get("contribution") or content_or_dict.get("pricing_amount")
        description = content_or_dict.get("description") or content_or_dict.get("pricing_description")
        model = content_or_dict.get("commercial_model") or content_or_dict.get("model") or "Fixed Price"
        if content_or_dict.get("currency") and str(content_or_dict["currency"]).strip().upper() in CURRENCY_SYMBOLS:
            curr = str(content_or_dict["currency"]).strip().upper()
    elif isinstance(content_or_dict, str):
        text = content_or_dict.strip()
        if (text.startswith("{") and text.endswith("}")) or ("'amount':" in text or '"amount":' in text or "'contribution':" in text or '"contribution":' in text):
            try:
                parsed = json.loads(text)
            except Exception:
                try:
                    import ast
                    parsed = ast.literal_eval(text)
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                return normalize_commercials_content(parsed, curr)

        if "<p>" in text or "<strong>" in text or "Total Contribution:" in text or "Total Investment:" in text:
            clean_text = text.replace("Total Investment:", "Total Contribution:")
            clean_text = clean_text.replace("Investment:", "Contribution:")
            return clean_text

        description = text

    formatted_amount = format_currency_amount(raw_amount or "To be confirmed", curr)
    if not description:
        description = "Fixed-price consulting engagement encompassing all assessment phases, automated scans, evidence collection, and executive deliverables."

    return (
        f"<p><strong>Total Contribution:</strong> {formatted_amount}</p>"
        f"<p><strong>Commercial Model:</strong> {model}</p>"
        f"<p><strong>Description:</strong><br>{description}</p>"
    )



def _valid_budget_text(value):
    text = str(value or "").strip()
    if not text:
        return True  # Empty values handled appropriately
    if "-" in text:  # Reject negative budget amounts
        return False
    cleaned = re.sub(r"[^\d.]", "", text)
    return bool(cleaned)



DEFAULT_TEMPLATE_ID = "default_whitehats"
DEFAULT_TEMPLATE_NAME = "Standard Professional"


def get_available_templates(user_id=None):
    """Retrieve all available proposal templates (registered system templates + user uploaded custom templates)."""
    excluded_ids = {"business_proposal_test_template", "business_proposal_alternative_design"}
    raw_templates = template_registry.list_templates()
    templates = [t for t in raw_templates if t["template_id"] not in excluded_ids]
    seen_ids = {t["template_id"] for t in templates}

    try:
        conn = get_db_connection()
        if user_id:
            rows = conn.execute(
                "SELECT template_id, name, description, type, file_path, created_at FROM proposal_templates WHERE (user_id = ? OR user_id IS NULL) AND (is_deleted IS NULL OR is_deleted = 0) ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT template_id, name, description, type, file_path, created_at FROM proposal_templates WHERE (user_id IS NULL) AND (is_deleted IS NULL OR is_deleted = 0) ORDER BY created_at DESC"
            ).fetchall()
        conn.close()
        for r in rows:
            r_dict = dict(r)
            t_id = r_dict["template_id"]
            name = r_dict.get("name") or ""
            if t_id not in seen_ids and t_id not in excluded_ids and name not in {"Business Proposal Test Template", "Business Proposal Alternative Design"}:
                seen_ids.add(t_id)
                templates.append({
                    "template_id": t_id,
                    "name": name,
                    "description": r_dict.get("description") or "Custom uploaded proposal template",
                    "type": r_dict.get("type") or "custom",
                    "file_path": r_dict.get("file_path"),
                    "is_default": False,
                    "created_at": str(r_dict.get("created_at") or ""),
                })
    except Exception as exc:
        app.logger.warning("Error fetching proposal templates: %s", exc)

    return templates


def get_templates_usage_data(user_id=None):
    """Retrieve real template usage counts and last used timestamps from business_proposals DB table."""
    usage = {}
    try:
        conn = get_db_connection()
        if user_id:
            rows = conn.execute(
                """
                SELECT p.template_id, COUNT(p.id) as cnt, MAX(p.updated_at) as last_used
                FROM business_proposals p
                JOIN assessments a ON p.assessment_id = a.id
                WHERE a.user_id = ?
                GROUP BY p.template_id
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT template_id, COUNT(id) as cnt, MAX(updated_at) as last_used
                FROM business_proposals
                GROUP BY template_id
                """
            ).fetchall()
        conn.close()
        for r in rows:
            r_dict = dict(r)
            t_id = r_dict.get("template_id") or DEFAULT_TEMPLATE_ID
            usage[t_id] = {
                "count": r_dict.get("cnt", 0),
                "last_used": str(r_dict.get("last_used") or ""),
            }
    except Exception as exc:
        app.logger.warning("Error fetching template usage data: %s", exc)
    return usage



def get_template_info(template_id, user_id=None):
    """Get metadata for a specific template. Fall back to default_whitehats if missing."""
    clean_id = str(template_id or DEFAULT_TEMPLATE_ID).strip().lower()

    # Check registered system templates first
    for sys_t in template_registry.list_templates(include_all=True):
        if sys_t["template_id"].lower() == clean_id:
            return sys_t

    try:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT template_id, name, description, type, file_path FROM proposal_templates WHERE template_id = ?",
            (template_id,),
        ).fetchone()
        conn.close()
        if row:
            r_dict = dict(row)
            return {
                "template_id": r_dict["template_id"],
                "name": r_dict["name"],
                "description": r_dict.get("description") or "Custom uploaded template",
                "type": r_dict.get("type") or "custom",
                "file_path": r_dict.get("file_path"),
                "is_default": False,
            }
    except Exception as exc:
        app.logger.warning("Error loading template info for %s: %s", template_id, exc)

    app.logger.info("Template '%s' not found. Falling back to default_whitehats.", template_id)
    return {
        "template_id": DEFAULT_TEMPLATE_ID,
        "name": DEFAULT_TEMPLATE_NAME,
        "description": "Clean, modern enterprise proposal layout (Fallback)",
        "type": "system",
        "file_path": None,
        "is_default": True,
        "fallback_used": True,
    }


def save_custom_template(user_id, template_file, template_name=None):
    """Save an uploaded custom DOCX or PDF proposal template."""
    if not template_file or not template_file.filename:
        raise ValueError("Please select a template file to upload.")

    filename = secure_filename(template_file.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("docx", "pdf"):
        raise ValueError("This template format isn't supported. Please upload a valid .docx or .pdf document template.")

    template_file.seek(0, os.SEEK_END)
    size_bytes = template_file.tell()
    template_file.seek(0)
    if size_bytes > 10 * 1024 * 1024:
        raise ValueError("Template file size exceeds the 10MB limit.")

    if ext == "pdf":
        header = template_file.read(1024)
        template_file.seek(0)
        if not header.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF file header. The uploaded file is not a valid PDF document.")
        try:
            import pypdf
            reader = pypdf.PdfReader(template_file)
            template_file.seek(0)
            if len(reader.pages) == 0:
                raise ValueError("The uploaded PDF document has no pages.")
        except (ImportError, ModuleNotFoundError):
            template_file.seek(0)
        except ValueError:
            raise
        except Exception as exc:
            template_file.seek(0)
            raise ValueError(f"Invalid or unreadable PDF document: {str(exc)}")

    template_id = f"custom_{uuid.uuid4().hex[:8]}"
    upload_dir = Path(app.root_path) / "uploads" / "templates"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_filename = f"{template_id}.{ext}"
    file_path = upload_dir / saved_filename
    template_file.save(str(file_path))

    name = (template_name or "").strip() or filename.rsplit(".", 1)[0].replace("_", " ").title()
    desc_type = "PDF" if ext == "pdf" else "DOCX"

    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO proposal_templates (template_id, name, description, type, file_path, user_id)
        VALUES (?, ?, ?, 'custom', ?, ?)
        """,
        (template_id, name, f"Custom {desc_type} template ({filename})", str(file_path), user_id),
    )
    conn.commit()
    conn.close()

    return {
        "template_id": template_id,
        "name": name,
        "file_path": str(file_path),
        "type": "custom",
        "format": ext,
    }


def save_business_proposal(assessment_id, proposal_dict, inputs_dict=None):
    """Save or update a generated business proposal for an assessment."""
    if not assessment_id or not proposal_dict:
        return
    inputs_dict = inputs_dict or proposal_dict.get("_inputs") or {}
    currency = str(inputs_dict.get("currency") or proposal_dict.get("currency") or "INR").strip().upper()
    if currency not in CURRENCY_SYMBOLS:
        currency = "INR"

    template_id = (
        inputs_dict.get("template_id")
        or proposal_dict.get("template_id")
        or "default_whitehats"
    )

    comm_obj = proposal_dict.get("commercials") or proposal_dict.get("pricing") or {}
    raw_comm_amount = comm_obj.get("amount") if isinstance(comm_obj, dict) else comm_obj

    raw_budget = (
        _clean_amount_val(inputs_dict.get("budget"))
        or _clean_amount_val(raw_comm_amount)
        or _clean_amount_val(proposal_dict.get("pricing_amount"))
        or _clean_amount_val(proposal_dict.get("budget"))
        or _clean_amount_val(proposal_dict.get("contribution"))
        or _clean_amount_val(proposal_dict.get("investment"))
    )

    formatted_budget = format_currency_amount(raw_budget, currency)

    if isinstance(proposal_dict.get("commercials"), dict):
        proposal_dict["commercials"]["amount"] = formatted_budget
        proposal_dict["commercials"]["currency"] = currency
    if isinstance(proposal_dict.get("pricing"), dict):
        proposal_dict["pricing"]["amount"] = formatted_budget
        proposal_dict["pricing"]["currency"] = currency

    proposal_dict["currency"] = currency
    proposal_dict["template_id"] = template_id
    inputs_dict["template_id"] = template_id
    proposal_dict["_inputs"] = inputs_dict

    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO business_proposals (assessment_id, client, project_type, deliverables, timeline, budget, additional_context, proposal_json, template_id, deal_outcome, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', CURRENT_TIMESTAMP)
        ON CONFLICT(assessment_id) DO UPDATE SET
            client = excluded.client,
            project_type = excluded.project_type,
            deliverables = excluded.deliverables,
            timeline = excluded.timeline,
            budget = excluded.budget,
            additional_context = excluded.additional_context,
            proposal_json = excluded.proposal_json,
            template_id = excluded.template_id,
            deal_outcome = COALESCE(business_proposals.deal_outcome, 'Open'),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            assessment_id,
            str(inputs_dict.get("client") or proposal_dict.get("client") or ""),
            str(inputs_dict.get("project_type") or proposal_dict.get("project_type") or ""),
            str(inputs_dict.get("deliverables") or ""),
            str(inputs_dict.get("timeline") or ""),
            formatted_budget,
            str(inputs_dict.get("additional_context") or ""),
            json.dumps(proposal_dict),
            template_id,
        ),
    )
    conn.execute(
        "UPDATE assessments SET pricing = ? WHERE id = ?",
        (formatted_budget, assessment_id),
    )
    conn.commit()
    conn.close()


DEFAULT_VENDOR_PROFILE = (
    "Our organization provides comprehensive enterprise technology solutions, consulting services, "
    "and professional advisory tailored to organizational requirements. We deliver strategic planning, "
    "technical architecture, system implementation, quality assurance, and ongoing operational support "
    "to help clients achieve operational excellence, security compliance, and scalable business growth."
)
WHITEHATS_VENDOR_PROFILE = DEFAULT_VENDOR_PROFILE


def _normalize_proposal(proposal):
    """Normalize any proposal dictionary into canonical dynamic section collection with legacy key aliases."""
    if not isinstance(proposal, dict):
        return proposal

    def get_val(keys, default=""):
        for k in keys:
            v = proposal.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return default

    def get_list(keys, default_list=None):
        for k in keys:
            v = proposal.get(k)
            if isinstance(v, list) and len(v) > 0:
                return [str(item).strip() for item in v if str(item).strip()]
            elif isinstance(v, str) and v.strip():
                return [v.strip()]
        return default_list or []

    inputs = proposal.get("_inputs") or {}
    currency = str(inputs.get("currency") or proposal.get("currency") or "INR").strip().upper()
    if currency not in CURRENCY_SYMBOLS:
        currency = "INR"

    pricing_raw = proposal.get("commercials") or proposal.get("pricing") or {}
    pricing_desc = "Fixed-price consulting engagement encompassing all assessment phases, automated scans, evidence collection, and executive deliverables."
    comm_amount_raw = pricing_raw.get("amount") if isinstance(pricing_raw, dict) else pricing_raw
    if isinstance(pricing_raw, dict) and pricing_raw.get("description"):
        pricing_desc = str(pricing_raw.get("description")).strip()
    elif proposal.get("pricing_description"):
        pricing_desc = str(proposal.get("pricing_description")).strip()

    raw_amount = (
        _clean_amount_val(comm_amount_raw)
        or _clean_amount_val(proposal.get("pricing_amount"))
        or _clean_amount_val(inputs.get("budget"))
        or _clean_amount_val(proposal.get("budget"))
        or _clean_amount_val(proposal.get("contribution"))
        or _clean_amount_val(proposal.get("investment"))
    )

    formatted_pricing_amount = format_currency_amount(raw_amount, currency)

    norm = {
        "_proposal_type": "business",
        "template_id": proposal.get("template_id") or (proposal.get("_inputs") or {}).get("template_id") or "default_whitehats",
        "template": proposal.get("template"),
        "_inputs": proposal.get("_inputs") or {},
        "currency": currency,
        "title": get_val(["title"], "AI Business Proposal"),
        "client": get_val(["client", "customer"], "Client Organization"),
        "project_type": get_val(["project_type", "assessment_type"], "Cybersecurity Assessment"),
        "executiveSummary": get_val(["executiveSummary", "executive_summary", "introduction"], "Executive summary overview of the engagement."),
        "aboutVendor": get_val(["aboutVendor", "about_vendor"], DEFAULT_VENDOR_PROFILE),
        "understandingOfRequirement": get_val(["understandingOfRequirement", "understanding_requirement", "project_overview"], "Evaluation of cybersecurity posture, framework compliance, and asset risk."),
        "proposedSolution": get_list(["proposedSolution", "proposed_solution", "deliverables"], ["Turnkey security assessment, automated scan discovery, and executive roadmap."]),
        "scopeOfWorkInScope": get_list(["scopeOfWorkInScope", "scope_of_work", "scope_in"], ["Internal and external network subnet scanning", "Access control evaluation", "Compliance gap mapping"]),
        "outOfScope": get_list(["outOfScope", "out_of_scope"], ["Third-party code remediation", "Hardware replacement procurement"]),
        "deploymentAndImplementationApproach": get_list(["deploymentAndImplementationApproach", "deployment_approach", "methodology"], ["Phase 1: Reconnaissance & Discovery", "Phase 2: Risk & Gap Analysis", "Phase 3: Executive Reporting"]),
        "prerequisites": get_list(["prerequisites"], ["Authorized IP whitelist confirmation", "Designated technical point of contact"]),
        "infrastructureRequirements": get_list(["infrastructureRequirements", "infrastructure_requirements"], ["Subnet CIDR range specification", "Standard auditor workstation access"]),
        "informationSecurityAndCompliance": get_list(["informationSecurityAndCompliance", "information_security"], ["ISO 27001 & NIST CSF control alignment", "Encrypted evidence storage"]),
        "trainingAndChangeManagement": get_list(["trainingAndChangeManagement", "training_change"], ["Post-assessment remediation workshop for engineering teams"]),
        "supportAndSLA": get_list(["supportAndSLA", "support_sla"], ["8/5 technical support during engagement lifecycle", "24-hour response for critical findings"]),
        "commercials": {
            "amount": formatted_pricing_amount,
            "description": pricing_desc,
            "currency": currency,
        },
        "licenseTermAndRenewal": get_val(["licenseTermAndRenewal", "license_renewal"], "12-month platform assessment license included."),
        "paymentTerms": get_list(["paymentTerms", "payment_terms"], ["50% upon engagement kickoff", "50% upon final deliverable acceptance"]),
        "legalCommercialTerms": get_list(["legalCommercialTerms", "legal_terms"], ["Mutual Non-Disclosure Agreement (NDA) applies", "Standard liability limit"]),
        "projectGovernanceAndEscalation": get_list(["projectGovernanceAndEscalation", "project_governance", "next_steps"], ["Weekly progress review meetings", "Designated escalation matrix"]),
        "acceptanceCriteria": get_list(["acceptanceCriteria", "acceptance_criteria"], ["Delivery of final executive security report and remediation roadmap"]),
        "risksAndAssumptions": get_list(["risksAndAssumptions", "risks_assumptions", "closing"], ["Target systems will remain accessible during scheduled scan windows"]),
    }

    # Maintain snake_case aliases for legacy callers
    norm["executive_summary"] = norm["executiveSummary"]
    norm["about_vendor"] = norm["aboutVendor"]
    norm["understanding_requirement"] = norm["understandingOfRequirement"]
    norm["proposed_solution"] = norm["proposedSolution"]
    norm["scope_of_work"] = norm["scopeOfWorkInScope"]
    norm["out_of_scope"] = norm["outOfScope"]
    norm["deployment_approach"] = norm["deploymentAndImplementationApproach"]
    norm["infrastructure_requirements"] = norm["infrastructureRequirements"]
    norm["information_security"] = norm["informationSecurityAndCompliance"]
    norm["training_change"] = norm["trainingAndChangeManagement"]
    norm["support_sla"] = norm["supportAndSLA"]
    norm["pricing"] = norm["commercials"]
    norm["budget"] = formatted_pricing_amount
    norm["contribution"] = formatted_pricing_amount
    norm["pricing_amount"] = formatted_pricing_amount
    norm["license_renewal"] = norm["licenseTermAndRenewal"]
    norm["payment_terms"] = norm["paymentTerms"]
    norm["legal_terms"] = norm["legalCommercialTerms"]
    norm["project_governance"] = norm["projectGovernanceAndEscalation"]
    norm["acceptance_criteria"] = norm["acceptanceCriteria"]
    norm["risks_assumptions"] = norm["risksAndAssumptions"]

    # Build canonical dynamic section objects collection
    default_section_defs = [
        ("section-1", "executiveSummary", "EXECUTIVE SUMMARY", "executive_summary", False, False),
        ("section-2", "aboutVendor", "ABOUT THE VENDOR", "about_vendor", False, False),
        ("section-3", "understandingOfRequirement", "UNDERSTANDING OF REQUIREMENT", "understanding_requirement", False, False),
        ("section-4", "proposedSolution", "PROPOSED SOLUTION", "proposed_solution", True, False),
        ("section-5", "scopeOfWorkInScope", "SCOPE OF WORK (IN SCOPE)", "scope_of_work", True, False),
        ("section-6", "outOfScope", "OUT OF SCOPE", "out_of_scope", True, False),
        ("section-7", "deploymentAndImplementationApproach", "DEPLOYMENT & IMPLEMENTATION APPROACH", "deployment_approach", True, True),
        ("section-8", "prerequisites", "PREREQUISITES", "prerequisites", True, False),
        ("section-9", "infrastructureRequirements", "INFRASTRUCTURE REQUIREMENTS", "infrastructure_requirements", True, False),
        ("section-10", "informationSecurityAndCompliance", "INFORMATION SECURITY & COMPLIANCE", "information_security", True, False),
        ("section-11", "trainingAndChangeManagement", "TRAINING & CHANGE MANAGEMENT", "training_change", True, False),
        ("section-12", "supportAndSLA", "SUPPORT & SLA", "support_sla", True, False),
        ("section-13", "commercials", "COMMERCIALS", "pricing", False, False),
        ("section-14", "licenseTermAndRenewal", "LICENSE TERM & RENEWAL", "license_renewal", False, False),
        ("section-15", "paymentTerms", "PAYMENT TERMS", "payment_terms", True, False),
        ("section-16", "legalCommercialTerms", "LEGAL / COMMERCIAL TERMS", "legal_terms", True, False),
        ("section-17", "projectGovernanceAndEscalation", "PROJECT GOVERNANCE & ESCALATION", "project_governance", True, False),
        ("section-18", "acceptanceCriteria", "ACCEPTANCE CRITERIA", "acceptance_criteria", True, False),
        ("section-19", "risksAndAssumptions", "RISKS & ASSUMPTIONS", "risks_assumptions", True, False),
    ]

    existing_sections = proposal.get("sections")
    sections_list = []

    if isinstance(existing_sections, list) and len(existing_sections) > 0:
        for idx, sec in enumerate(existing_sections, start=1):
            if isinstance(sec, dict):
                sec_id = str(sec.get("id") or f"section-{idx}")
                sec_title = str(sec.get("title") or f"SECTION {idx}").strip()
                sec_content = sec.get("content")
                if sec_content is None:
                    sec_content = sec.get("val") or ""
                sec_type = str(sec.get("type") or ("default" if idx <= 19 else "custom"))
                sec_key = sec.get("key") or sec.get("alt_key") or f"section_{idx}"
                alt_key = sec.get("alt_key")
                sec_obj = {
                    "id": sec_id,
                    "key": sec_key,
                    "title": sec_title,
                    "content": sec_content,
                    "type": sec_type,
                    "is_list": bool(sec.get("is_list", False)),
                    "is_ordered": bool(sec.get("is_ordered", False)),
                    "order": idx
                }
                if alt_key:
                    sec_obj["alt_key"] = alt_key
                sections_list.append(sec_obj)

                if sec_key and sec_key in norm:
                    norm[sec_key] = sec_content
                if alt_key and alt_key in norm:
                    norm[alt_key] = sec_content
    else:
        for idx, (sec_id, key, title, alt_key, is_list, is_ordered) in enumerate(default_section_defs, start=1):
            sections_list.append({
                "id": sec_id,
                "key": key,
                "title": title,
                "alt_key": alt_key,
                "content": norm.get(key) or norm.get(alt_key) or "",
                "type": "default",
                "is_list": is_list,
                "is_ordered": is_ordered,
                "order": idx
            })

    # Ensure Section 13 (Commercials) content is normalized to human-readable HTML
    for sec in sections_list:
        sec_id_str = str(sec.get("id") or "")
        sec_key_str = str(sec.get("key") or "")
        sec_title_str = str(sec.get("title") or "").upper()
        if sec_id_str == "section-13" or sec_key_str == "commercials" or "COMMERCIALS" in sec_title_str:
            raw_sec_content = sec.get("content")
            if not raw_sec_content or isinstance(raw_sec_content, dict) or (isinstance(raw_sec_content, str) and ("'amount':" in raw_sec_content or '"amount":' in raw_sec_content or raw_sec_content.strip().startswith("{"))):
                raw_sec_content = {
                    "amount": formatted_pricing_amount,
                    "description": pricing_desc,
                    "currency": currency,
                }
            sec["content"] = normalize_commercials_content(raw_sec_content, currency)

    norm["sections"] = sections_list

    # Ensure default vendor profile for aboutVendor
    if "AgentScan" in str(norm["aboutVendor"]) or "GRC" in str(norm["aboutVendor"]) or not norm["aboutVendor"]:
        norm["aboutVendor"] = DEFAULT_VENDOR_PROFILE
        norm["about_vendor"] = DEFAULT_VENDOR_PROFILE

    return norm


def get_business_proposal(assessment_id):
    """Fetch the saved business proposal for an assessment."""
    if not assessment_id:
        return None
    conn = get_db_connection()
    row = conn.execute(
        "SELECT proposal_json, client, project_type, deliverables, timeline, budget, additional_context, template_id, deal_outcome FROM business_proposals WHERE assessment_id = ?",
        (assessment_id,),
    ).fetchone()
    
    assessment_row = conn.execute(
        "SELECT status, start_date FROM assessments WHERE id = ?",
        (assessment_id,),
    ).fetchone()
    conn.close()

    if row:
        row_dict = dict(row) if isinstance(row, sqlite3.Row) else row
        if row_dict.get("proposal_json"):
            try:
                proposal = json.loads(row_dict["proposal_json"])
                saved_inputs = proposal.get("_inputs") or {}
                currency = str(saved_inputs.get("currency") or proposal.get("currency") or "INR").strip().upper()
                if currency not in CURRENCY_SYMBOLS:
                    currency = "INR"
                template_id = (
                    row_dict.get("template_id")
                    or saved_inputs.get("template_id")
                    or proposal.get("template_id")
                    or "default_whitehats"
                )
                proposal["template_id"] = template_id
                proposal["_inputs"] = {
                    "client": row_dict.get("client", ""),
                    "project_type": row_dict.get("project_type", ""),
                    "deliverables": row_dict.get("deliverables", ""),
                    "timeline": row_dict.get("timeline", ""),
                    "currency": currency,
                    "budget": row_dict.get("budget", ""),
                    "additional_context": row_dict.get("additional_context", ""),
                    "template_id": template_id,
                }
                proposal = _normalize_proposal(proposal)

                # Attach assessment metadata & canonical status
                raw_status = (dict(assessment_row).get("status") if assessment_row else "In Evaluation") or "In Evaluation"
                raw_status = raw_status.strip()
                if raw_status in ("In Progress", "Draft", "New"):
                    norm_status = "In Evaluation"
                elif raw_status in ("Submitted", "Completed"):
                    norm_status = "Submitted"
                elif raw_status in ("Approved", "Accepted"):
                    norm_status = "Approved"
                elif raw_status in ("Rejected", "Declined"):
                    norm_status = "Rejected"
                else:
                    norm_status = raw_status

                start_date_raw = str(dict(assessment_row).get("start_date") or "") if assessment_row else ""
                year = start_date_raw[:4] if (start_date_raw and len(start_date_raw) >= 4 and start_date_raw[:4].isdigit()) else "2026"

                proposal["assessment_id"] = assessment_id
                proposal["id"] = assessment_id
                proposal["code"] = f"PROP-{year}-{assessment_id}"
                proposal["status"] = norm_status
                proposal["raw_status"] = raw_status
                proposal["template_id"] = template_id

                raw_outcome = (row_dict.get("deal_outcome") or "Open").strip()
                deal_outcome = raw_outcome.title() if raw_outcome.title() in ("Open", "Won", "Lost") else "Open"
                proposal["deal_outcome"] = deal_outcome
                return proposal
            except Exception:
                return None
    return None


def create_default_proposal_for_assessment(assessment):
    """Generate and persist a canonical 19-section proposal for an active assessment."""
    if not assessment:
        return None
    if isinstance(assessment, sqlite3.Row):
        assessment = dict(assessment)
    client_name = str(assessment.get("customer_name") or "Client Organization").strip()
    framework = str(assessment.get("framework") or "ISO 27001 / NIST CSF").strip()
    project_type = f"Enterprise Security Assessment & {framework} Compliance"
    raw_pricing = str(assessment.get("pricing") or "").strip()
    if raw_pricing and raw_pricing.isdigit():
        pricing_amount = f"${int(raw_pricing):,}"
    elif raw_pricing:
        pricing_amount = raw_pricing
    else:
        pricing_amount = "To be confirmed"

    raw_proposal = {
        "_proposal_type": "business",
        "title": f"Enterprise Security & Risk Assessment Proposal — {client_name}",
        "client": client_name,
        "project_type": project_type,
        "executiveSummary": (
            f"This proposal presents a comprehensive cybersecurity and framework compliance assessment for {client_name}. "
            "Our team will evaluate organizational security posture, conduct automated scan discovery, "
            "audit control effectiveness, and deliver a prioritized executive risk mitigation roadmap."
        ),
        "aboutVendor": DEFAULT_VENDOR_PROFILE,
        "understandingOfRequirement": (
            f"{client_name} requires a rigorous evaluation of technical security controls, network asset vulnerabilities, "
            "policy frameworks, and compliance alignment to mitigate threat exposure and satisfy enterprise security standards."
        ),
        "proposedSolution": [
            "Turnkey automated network discovery and asset risk auditing",
            "Comprehensive framework gap analysis and security control testing",
            "Executive risk register with prioritized remediation guidance",
            "Post-assessment technical handover workshop and compliance reporting"
        ],
        "scopeOfWorkInScope": [
            "Internal and external network subnet discovery and port auditing",
            "Security policy alignment and compliance control verification",
            "Vulnerability identification, threat classification, and risk scoring",
            "Consolidated executive audit reporting and evidence archiving"
        ],
        "outOfScope": [
            "Direct remediation code edits or infrastructure re-configurations",
            "Third-party software license procurement or hardware replacements",
            "Physical security guard force operational audits"
        ],
        "deploymentAndImplementationApproach": [
            "Phase 1: Kickoff, Scope Definition & Automated Discovery Scanning",
            "Phase 2: Vulnerability Analysis, Policy Mapping & Control Testing",
            "Phase 3: Executive Reporting, Risk Matrix Finalization & Roadmap Handover"
        ],
        "prerequisites": [
            "Authorized target IP range whitelist for scanner agent",
            "Designated primary technical point of contact for coordination",
            "Execution of mutual Non-Disclosure Agreement (NDA)"
        ],
        "infrastructureRequirements": [
            "Subnet CIDR range specifications for discovery scanning",
            "Standard auditor HTTPS/SSH access for verification workflows"
        ],
        "informationSecurityAndCompliance": [
            "Strict adherence to ISO/IEC 27001 & NIST CSF security benchmarks",
            "End-to-end encrypted evidence storage and transmission",
            "Zero permanent retention of target credentials"
        ],
        "trainingAndChangeManagement": [
            "Post-assessment remediation workshop for internal IT and security teams to review findings and coordinate patching."
        ],
        "supportAndSLA": [
            "8/5 technical support during engagement lifecycle",
            "24-hour response SLA for critical vulnerability findings"
        ],
        "commercials": {
            "amount": pricing_amount,
            "description": "Fixed-price consulting engagement encompassing all assessment phases, automated scans, evidence collection, and executive deliverables."
        },
        "licenseTermAndRenewal": "Includes 12-month platform assessment access with annual renewal options.",
        "paymentTerms": [
            "50% upon engagement kickoff",
            "50% upon final executive report handover and acceptance"
        ],
        "legalCommercialTerms": [
            "Mutual Non-Disclosure Agreement (NDA) applies",
            "Standard limitation of liability capped at total engagement fee"
        ],
        "projectGovernanceAndEscalation": [
            "Weekly progress review meetings between Lead Auditor and Client IT Manager",
            "Direct escalation path to Practice Director"
        ],
        "acceptanceCriteria": [
            "Formal delivery of final executive security report and remediation roadmap",
            "Successful completion of executive handover presentation"
        ],
        "risksAndAssumptions": [
            "Target systems remain accessible during authorized scanning windows",
            "Client technical stakeholders provide timely input during audit reviews"
        ]
    }
    proposal_inputs = {
        "client": client_name,
        "project_type": project_type,
        "deliverables": "Security assessment, vulnerability report, executive risk roadmap",
        "timeline": f"{assessment.get('start_date', '')} to {assessment.get('end_date', '')}",
        "budget": pricing_amount,
        "additional_context": ""
    }
    proposal = _normalize_proposal(raw_proposal)
    proposal["_inputs"] = proposal_inputs
    if assessment.get("id"):
        save_business_proposal(assessment["id"], proposal, proposal_inputs)
    return proposal


def _business_proposal_defaults(assessment=None, form_data=None):
    form_data = form_data or {}
    if assessment is not None and isinstance(assessment, sqlite3.Row):
        assessment = dict(assessment)
    assessment_project = ""
    if assessment is not None:
        assessment_project = " - ".join(
            part for part in [
                str(assessment.get("assessment_type", "") if isinstance(assessment, dict) else assessment["assessment_type"]).strip(),
                str(assessment.get("framework", "") if isinstance(assessment, dict) else assessment["framework"]).strip(),
            ]
            if part
        )

    customer_name = (
        assessment.get("customer_name", "")
        if isinstance(assessment, dict)
        else (assessment["customer_name"] if assessment else "")
    )
    pricing_val = (
        assessment.get("pricing", "")
        if isinstance(assessment, dict)
        else (assessment["pricing"] if assessment else "")
    )

    currency_val = str(form_data.get("proposal_currency") or "INR").strip().upper()
    if currency_val not in CURRENCY_SYMBOLS:
        currency_val = "INR"

    template_id_val = str(
        form_data.get("template_id")
        or session.get("selected_template_id")
        or DEFAULT_TEMPLATE_ID
    ).strip()

    return {
        "client": str(form_data.get("proposal_client") or customer_name).strip(),
        "project_type": str(form_data.get("proposal_project_type") or assessment_project).strip(),
        "deliverables": str(form_data.get("proposal_deliverables") or "").strip(),
        "timeline": str(form_data.get("proposal_timeline") or "").strip(),
        "currency": currency_val,
        "budget": str(form_data.get("proposal_budget") or pricing_val).strip(),
        "additional_context": str(form_data.get("request_text") or "").strip(),
        "template_id": template_id_val,
    }


def validate_business_proposal(payload):
    """Validate the structured business-proposal response before rendering."""
    if not isinstance(payload, dict):
        return False, ["AI returned an unexpected proposal format."]

    required_text_fields = [
        "title",
        "client",
    ]
    errors = []

    for field in required_text_fields:
        if not str(payload.get(field, "")).strip():
            errors.append(f"Missing required proposal field: {field}.")

    return len(errors) == 0, errors


def _business_proposal_schema():
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "client": {"type": "string"},
            "executiveSummary": {"type": "string"},
            "aboutVendor": {"type": "string"},
            "understandingOfRequirement": {"type": "string"},
            "proposedSolution": {"type": "array", "items": {"type": "string"}},
            "scopeOfWorkInScope": {"type": "array", "items": {"type": "string"}},
            "outOfScope": {"type": "array", "items": {"type": "string"}},
            "deploymentAndImplementationApproach": {"type": "array", "items": {"type": "string"}},
            "prerequisites": {"type": "array", "items": {"type": "string"}},
            "infrastructureRequirements": {"type": "array", "items": {"type": "string"}},
            "informationSecurityAndCompliance": {"type": "array", "items": {"type": "string"}},
            "trainingAndChangeManagement": {"type": "array", "items": {"type": "string"}},
            "supportAndSLA": {"type": "array", "items": {"type": "string"}},
            "commercials": {
                "type": "object",
                "properties": {
                    "amount": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["amount", "description"],
            },
            "licenseTermAndRenewal": {"type": "string"},
            "paymentTerms": {"type": "array", "items": {"type": "string"}},
            "legalCommercialTerms": {"type": "array", "items": {"type": "string"}},
            "projectGovernanceAndEscalation": {"type": "array", "items": {"type": "string"}},
            "acceptanceCriteria": {"type": "array", "items": {"type": "string"}},
            "risksAndAssumptions": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "title",
            "client",
            "executiveSummary",
            "aboutVendor",
            "understandingOfRequirement",
            "proposedSolution",
            "scopeOfWorkInScope",
            "outOfScope",
            "deploymentAndImplementationApproach",
            "prerequisites",
            "infrastructureRequirements",
            "informationSecurityAndCompliance",
            "trainingAndChangeManagement",
            "supportAndSLA",
            "commercials",
            "licenseTermAndRenewal",
            "paymentTerms",
            "legalCommercialTerms",
            "projectGovernanceAndEscalation",
            "acceptanceCriteria",
            "risksAndAssumptions",
        ],
    }


def generate_business_proposal(client, project_type, deliverables, timeline, budget, additional_context, currency="INR"):
    """Generate a full business proposal document from project-level inputs."""
    start_total_time = time.time()
    schema = _business_proposal_schema()
    normalized_deliverables = _text_list(deliverables)

    curr = str(currency or "INR").strip().upper()
    if curr not in CURRENCY_SYMBOLS:
        curr = "INR"

    formatted_budget = format_currency_amount(budget, curr)

    pricing_instruction = (
        f"Use this exact pricing amount in commercials.amount: {formatted_budget} (Currency: {curr})."
        if str(budget or "").strip()
        else "If budget is not provided, set commercials.amount to 'To be confirmed'."
    )

    instruction = f"""
You are drafting a complete, professional, enterprise-grade business proposal document with all 19 sections.

Write a BUSINESS PROPOSAL for:
- Client: {client}
- Project type: {project_type}
- Deliverables requested by the user: {json.dumps(normalized_deliverables, ensure_ascii=True)}
- Timeline input: {timeline}
- Additional context: {additional_context or 'Not provided'}
- Currency selected: {curr}

Rules:
1. Return JSON only and match the schema exactly.
2. Write a complete, polished, high-impact enterprise proposal suitable for formal executive presentation.
3. Base all proposal content, solutions, scope, and implementation methodology on the client requirements provided above. Do NOT invent specific company names or headquarters that were not provided.
4. For 'aboutVendor', describe the vendor as a professional enterprise technology and consulting solutions provider delivering high-quality implementation, domain expertise, scalable architecture, and managed services tailored to the client's domain.
5. Populate all required fields defined in the JSON schema.
6. {pricing_instruction}
"""

    prompt_chars = len(instruction)
    app.logger.info(
        "[PROPOSAL] Generation started for client: %s (prompt size: %d chars, sections: 19, timeout: %ds, model: %s)",
        client,
        prompt_chars,
        GEMINI_REQUEST_TIMEOUT_SEC,
        GEMINI_MODEL,
    )
    client_obj = _create_gemini_client()
    output_text = None

    gemini_start_time = time.time()
    response = None

    req_http_options = types.HttpOptions(
        client_args={"trust_env": False},
        async_client_args={"trust_env": False},
        timeout=GEMINI_REQUEST_TIMEOUT_MS,
    )

    gen_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0.3,
        max_output_tokens=4096,
        thinking_config=types.ThinkingConfig(thinking_budget=1024),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=req_http_options,
    )

    for attempt in (1, 2, 3):
        try:
            app.logger.info("[PROPOSAL] Gemini request started using model=%s (attempt %d)", GEMINI_MODEL, attempt)
            response = client_obj.models.generate_content(
                model=GEMINI_MODEL,
                contents=instruction,
                config=gen_config,
            )
            gemini_duration = time.time() - gemini_start_time
            app.logger.info("[PROPOSAL] Gemini request completed in %.2fs (attempt %d)", gemini_duration, attempt)
            break
        except Exception as exc:
            gemini_duration = time.time() - gemini_start_time
            exc_str = str(exc).lower()
            is_timeout = (
                isinstance(exc, (APITimeoutError, TimeoutError, httpx.TimeoutException))
                or "timeout" in exc_str
                or "504" in exc_str
                or "deadline" in exc_str
            )

            if is_timeout:
                app.logger.error("[PROPOSAL] Gemini request failed after %.2fs | Error type: Timeout | Fallback used: no | Client: %s: %s", gemini_duration, client, exc)
                raise APITimeoutError(f"AI business proposal generation timed out after {GEMINI_REQUEST_TIMEOUT_SEC} seconds.") from exc

            is_503 = "503" in str(exc) or "UNAVAILABLE" in str(exc) or "high demand" in exc_str
            if attempt < 3 and is_503:
                backoff_time = attempt * 2.0
                app.logger.warning("[PROPOSAL] Gemini model high demand 503 spike encountered, retrying in %.1fs (attempt %d/3)...", backoff_time, attempt)
                time.sleep(backoff_time)
                continue

            app.logger.error("[PROPOSAL] Gemini request failed after %.2fs | Error type: %s | Fallback used: no | Client: %s: %s", gemini_duration, type(exc).__name__, client, exc)
            raise RuntimeError(f"Business proposal generation failed: {exc}") from exc

    output_text = _extract_gemini_output_text(response)
    if not output_text:
        raise RuntimeError("Gemini returned an empty business proposal response.")

    parse_start_time = time.time()
    try:
        raw_proposal = json.loads(output_text, parse_int=_safe_json_parse_int)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON for the business proposal: {exc}") from exc

    raw_proposal["client"] = str(client).strip()
    clean_budget_input = _clean_amount_val(budget)
    if clean_budget_input:
        raw_proposal.setdefault("commercials", {})
        if isinstance(raw_proposal["commercials"], dict):
            raw_proposal["commercials"]["amount"] = clean_budget_input
            raw_proposal["commercials"]["currency"] = curr
        raw_proposal["pricing_amount"] = clean_budget_input

    raw_proposal["_inputs"] = {
        "client": str(client).strip(),
        "project_type": str(project_type).strip(),
        "deliverables": deliverables,
        "timeline": str(timeline).strip(),
        "currency": curr,
        "budget": str(budget or "").strip(),
        "additional_context": str(additional_context or "").strip(),
    }

    proposal = _normalize_proposal(raw_proposal)
    proposal["client"] = str(client).strip()
    proposal["title"] = f"Business Proposal for {client}"
    proposal["_proposal_type"] = "business"

    valid, errors = validate_business_proposal(proposal)
    if not valid:
        raise ValueError("AI output failed validation: " + "; ".join(errors))

    processing_duration = time.time() - parse_start_time
    total_duration = time.time() - start_total_time
    app.logger.info("[PROPOSAL] Proposal processing completed in %.2fs", processing_duration)
    app.logger.info("[PROPOSAL] Total generation time: %.2fs", total_duration)

    return proposal


def _clean_generated_text(value, field_name=None):
    if value is None:
        return ""
    text = str(value).strip()
    if field_name in {"likelihood", "impact", "pricing"}:
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return 1
    if field_name in {"customer_size", "company_size", "employee_count"}:
        return text
    return text


def validate_generated_assessment(payload):
    """Validate a generated assessment object against the app's schema."""
    errors = []

    if not isinstance(payload, dict):
        return False, ["Generated assessment output is not a JSON object."]

    required_top = [
        "customer",
        "assessment",
        "organization_profile",
        "assessment_scope",
        "risks",
        "controls",
        "findings",
    ]
    for key in required_top:
        if key not in payload:
            errors.append(f"Missing required section: {key}.")

    if errors:
        return False, errors

    customer = payload.get("customer")
    if not isinstance(customer, dict):
        errors.append("customer must be an object.")
    else:
        for field in ["name", "industry", "company_size"]:
            if not str(customer.get(field, "")).strip():
                errors.append(f"customer.{field} is required.")

    assessment = payload.get("assessment")
    if not isinstance(assessment, dict):
        errors.append("assessment must be an object.")
    else:
        for field in ["assessment_type", "framework", "start_date", "end_date", "pricing", "status"]:
            if not str(assessment.get(field, "")).strip():
                errors.append(f"assessment.{field} is required.")

    organization_profile = payload.get("organization_profile")
    if not isinstance(organization_profile, dict):
        errors.append("organization_profile must be an object.")
    else:
        for field in ["legal_name", "industry", "employee_count", "location", "business_description"]:
            if not str(organization_profile.get(field, "")).strip():
                errors.append(f"organization_profile.{field} is required.")

    assessment_scope = payload.get("assessment_scope")
    if not isinstance(assessment_scope, dict):
        errors.append("assessment_scope must be an object.")
    else:
        for field in ["scope_description", "in_scope_assets", "internal_network", "web_applications", "cloud_infrastructure", "physical_security", "hr_systems"]:
            if field in assessment_scope and not str(assessment_scope.get(field, "")).strip():
                errors.append(f"assessment_scope.{field} is required.")

    for list_name, required_fields in {
        "risks": ["title", "description", "category", "likelihood", "impact", "owner", "treatment", "status"],
        "controls": ["control_name", "description", "framework", "control_category", "owner", "implementation_status"],
        "findings": ["title", "description", "source", "severity", "recommendation", "status"],
    }.items():
        items = payload.get(list_name, [])
        if not isinstance(items, list):
            errors.append(f"{list_name} must be an array.")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{list_name}[{index}] must be an object.")
                continue
            for field in required_fields:
                if not str(item.get(field, "")).strip():
                    errors.append(f"{list_name}[{index}].{field} is required.")

    if payload.get("risks"):
        for index, risk in enumerate(payload["risks"]):
            try:
                likelihood = int(risk.get("likelihood", 1))
                impact = int(risk.get("impact", 1))
                if not 1 <= likelihood <= 5:
                    errors.append(f"risks[{index}].likelihood must be between 1 and 5.")
                if not 1 <= impact <= 5:
                    errors.append(f"risks[{index}].impact must be between 1 and 5.")
            except (TypeError, ValueError):
                errors.append(f"risks[{index}].likelihood and impact must be integers.")

    if payload.get("controls"):
        for index, control in enumerate(payload["controls"]):
            if control.get("implementation_status", "").strip() not in {"Implemented", "Partially Implemented", "Not Implemented", "Planned"}:
                errors.append(f"controls[{index}].implementation_status must be a valid governance status.")

    if payload.get("findings"):
        for index, finding in enumerate(payload["findings"]):
            if finding.get("severity", "").strip() not in {"Low", "Medium", "High", "Critical"}:
                errors.append(f"findings[{index}].severity must be Low, Medium, High, or Critical.")

    return not errors, errors


def save_generated_assessment(payload, user_id=None):
    """Persist a generated assessment using the app's existing schema and tables."""
    if not isinstance(payload, dict):
        raise ValueError("Generated assessment payload must be a JSON object.")

    valid, errors = validate_generated_assessment(payload)
    if not valid:
        raise ValueError("AI generated an invalid assessment: " + "; ".join(errors))

    user_id = user_id if user_id is not None else session.get("user_id")
    if user_id is None:
        raise RuntimeError("A logged-in user is required to save an AI-generated assessment.")

    customer = payload.get("customer", {}) or {}
    assessment = payload.get("assessment", {}) or {}

    customer_id = create_customer(
        str(customer.get("name", "")).strip(),
        str(customer.get("industry", "")).strip(),
        str(customer.get("company_size", "")).strip(),
    )

    assessment_id = create_assessment(
        customer_id=customer_id,
        assessment_type=str(assessment.get("assessment_type", "")).strip(),
        framework=str(assessment.get("framework", "")).strip(),
        start_date=str(assessment.get("start_date", "")).strip(),
        end_date=str(assessment.get("end_date", "")).strip(),
        pricing=str(assessment.get("pricing", 0)).strip() or "0",
        status=str(assessment.get("status", "In Progress")).strip() or "In Progress",
        user_id=user_id,
    )

    conn = get_db_connection()
    try:
        profile = payload.get("organization_profile", {}) or {}
        if profile:
            conn.execute(
                """
                INSERT INTO organization_profiles (
                    assessment_id, legal_name, industry, employee_count, location,
                    business_description, critical_business_functions
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    str(profile.get("legal_name", "")).strip(),
                    str(profile.get("industry", "")).strip(),
                    str(profile.get("employee_count", "")).strip(),
                    str(profile.get("location", "")).strip(),
                    str(profile.get("business_description", "")).strip(),
                    str(profile.get("critical_business_functions", "")).strip(),
                ),
            )

        scope = payload.get("assessment_scope", {}) or {}
        if scope:
            conn.execute(
                """
                INSERT INTO assessment_scopes (
                    assessment_id, scope_description, in_scope_assets, out_of_scope_assets,
                    internal_network, web_applications, cloud_infrastructure,
                    security_policies, physical_security, hr_systems,
                    assessment_activities
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    str(scope.get("scope_description", "")).strip(),
                    str(scope.get("in_scope_assets", "")).strip(),
                    str(scope.get("out_of_scope_assets", "")).strip(),
                    str(scope.get("internal_network", "No")).strip() or "No",
                    str(scope.get("web_applications", "No")).strip() or "No",
                    str(scope.get("cloud_infrastructure", "No")).strip() or "No",
                    str(scope.get("security_policies", "Yes")).strip() or "Yes",
                    str(scope.get("physical_security", "No")).strip() or "No",
                    str(scope.get("hr_systems", "No")).strip() or "No",
                    str(scope.get("assessment_activities", "")).strip(),
                ),
            )

        for table_name, field_names in {
            "policies": ["policy_name", "description", "owner", "status", "version", "review_date"],
            "assets": ["asset_name", "asset_type", "ip_address", "operating_system", "owner", "criticality", "status", "description"],
            "vendors": ["vendor_name", "service", "criticality", "risk_level", "assessment_status", "notes"],
        }.items():
            for item in payload.get(table_name, []) or []:
                if not isinstance(item, dict):
                    continue
                row = {field: str(item.get(field, "")).strip() for field in field_names}
                if table_name == "policies":
                    conn.execute(
                        """
                        INSERT INTO policies (assessment_id, policy_name, description, owner, status, version, review_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            assessment_id,
                            row.get("policy_name", ""),
                            row.get("description", ""),
                            row.get("owner", ""),
                            row.get("status", ""),
                            row.get("version", ""),
                            row.get("review_date", ""),
                        ),
                    )
                elif table_name == "assets":
                    conn.execute(
                        """
                        INSERT INTO assets (assessment_id, asset_name, asset_type, ip_address, operating_system, owner, criticality, status, description)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            assessment_id,
                            row.get("asset_name", ""),
                            row.get("asset_type", ""),
                            row.get("ip_address", ""),
                            row.get("operating_system", ""),
                            row.get("owner", ""),
                            row.get("criticality", ""),
                            row.get("status", ""),
                            row.get("description", ""),
                        ),
                    )
                elif table_name == "vendors":
                    conn.execute(
                        """
                        INSERT INTO vendors (assessment_id, vendor_name, service, criticality, risk_level, assessment_status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            assessment_id,
                            row.get("vendor_name", ""),
                            row.get("service", ""),
                            row.get("criticality", ""),
                            row.get("risk_level", ""),
                            row.get("assessment_status", ""),
                            row.get("notes", ""),
                        ),
                    )

        for risk in payload.get("risks", []) or []:
            if not isinstance(risk, dict):
                continue
            likelihood = int(risk.get("likelihood", 1)) if str(risk.get("likelihood", "")).strip() else 1
            impact = int(risk.get("impact", 1)) if str(risk.get("impact", "")).strip() else 1
            cursor = conn.execute(
                """
                INSERT INTO risks (
                    assessment_id, title, description, category, likelihood, impact,
                    risk_score, risk_level, owner, treatment, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    str(risk.get("title", "")).strip(),
                    str(risk.get("description", "")).strip(),
                    str(risk.get("category", "")).strip(),
                    likelihood,
                    impact,
                    likelihood * impact,
                    "Medium" if likelihood * impact >= 4 else "Low",
                    str(risk.get("owner", "")).strip(),
                    str(risk.get("treatment", "")).strip(),
                    str(risk.get("status", "Open")).strip() or "Open",
                ),
            )
            _recalculate_risk(conn, cursor.lastrowid)

        for control in payload.get("controls", []) or []:
            if not isinstance(control, dict):
                continue
            conn.execute(
                """
                INSERT INTO controls (
                    assessment_id, control_name, description, framework, control_category,
                    owner, implementation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    str(control.get("control_name", "")).strip(),
                    str(control.get("description", "")).strip(),
                    str(control.get("framework", "")).strip(),
                    str(control.get("control_category", "")).strip(),
                    str(control.get("owner", "")).strip(),
                    str(control.get("implementation_status", "")).strip() or "Not Implemented",
                ),
            )

        for finding in payload.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            conn.execute(
                """
                INSERT INTO findings (
                    assessment_id, title, description, source, severity, ip_address,
                    port, service, status, recommendation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    str(finding.get("title", "")).strip(),
                    str(finding.get("description", "")).strip(),
                    str(finding.get("source", "")).strip(),
                    str(finding.get("severity", "")).strip(),
                    str(finding.get("ip_address", "")).strip(),
                    str(finding.get("port", "")).strip(),
                    str(finding.get("service", "")).strip(),
                    str(finding.get("status", "Open")).strip() or "Open",
                    str(finding.get("recommendation", "")).strip(),
                ),
            )

        for action in payload.get("remediation_actions", []) or []:
            if not isinstance(action, dict):
                continue
            conn.execute(
                """
                INSERT INTO remediation_actions (
                    assessment_id, finding_id, risk_id, action, owner, priority,
                    due_date, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    None,
                    None,
                    str(action.get("action", "")).strip(),
                    str(action.get("owner", "")).strip(),
                    str(action.get("priority", "Medium")).strip() or "Medium",
                    str(action.get("due_date", "")).strip(),
                    str(action.get("status", "Open")).strip() or "Open",
                    str(action.get("notes", "")).strip(),
                ),
            )

        conn.commit()
        session["assessment_id"] = assessment_id
        log_audit("AI assessment generation", "assessment", assessment_id, "AI generated and saved a complete assessment.", assessment_id)
        return assessment_id
    finally:
        conn.close()


def call_gemini_assessment_generation(prompt, assessment_id=None):
    """Generate a complete assessment payload from a natural-language prompt."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured in the .env file.")

    mode = detect_ai_mode(assessment_id, prompt)
    context_payload = {
        "assessment_id": assessment_id,
        "existing_assessment": get_ai_review_context(assessment_id) if assessment_id else None,
        "instruction": "Generate a complete assessment from scratch when no existing assessment is supplied.",
    }
    if assessment_id is None:
        context_payload = {
            "assessment_id": None,
            "existing_assessment": None,
            "instruction": "No current assessment exists. Generate a full assessment from scratch.",
        }

    schema = {
        "type": "object",
        "properties": {
            "customer": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "industry": {"type": "string"},
                    "company_size": {"type": "string"},
                },
                "required": ["name", "industry", "company_size"],
            },
            "assessment": {
                "type": "object",
                "properties": {
                    "assessment_type": {"type": "string"},
                    "framework": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "pricing": {"type": "number"},
                    "status": {"type": "string"},
                },
                "required": ["assessment_type", "framework", "start_date", "end_date", "pricing", "status"],
            },
            "organization_profile": {
                "type": "object",
                "properties": {
                    "legal_name": {"type": "string"},
                    "industry": {"type": "string"},
                    "employee_count": {"type": "string"},
                    "location": {"type": "string"},
                    "business_description": {"type": "string"},
                    "critical_business_functions": {"type": "string"},
                },
                "required": ["legal_name", "industry", "employee_count", "location", "business_description", "critical_business_functions"],
            },
            "assessment_scope": {
                "type": "object",
                "properties": {
                    "scope_description": {"type": "string"},
                    "in_scope_assets": {"type": "string"},
                    "out_of_scope_assets": {"type": "string"},
                    "internal_network": {"type": "string"},
                    "web_applications": {"type": "string"},
                    "cloud_infrastructure": {"type": "string"},
                    "security_policies": {"type": "string"},
                    "physical_security": {"type": "string"},
                    "hr_systems": {"type": "string"},
                    "assessment_activities": {"type": "string"},
                },
                "required": ["scope_description", "in_scope_assets", "internal_network", "web_applications", "cloud_infrastructure", "security_policies", "physical_security", "hr_systems", "assessment_activities"],
            },
            "policies": {"type": "array", "items": {"type": "object", "properties": {"policy_name": {"type": "string"}, "description": {"type": "string"}, "owner": {"type": "string"}, "status": {"type": "string"}, "version": {"type": "string"}, "review_date": {"type": "string"}}, "required": ["policy_name", "description", "owner", "status", "version", "review_date"]}},
            "assets": {"type": "array", "items": {"type": "object", "properties": {"asset_name": {"type": "string"}, "asset_type": {"type": "string"}, "ip_address": {"type": "string"}, "operating_system": {"type": "string"}, "owner": {"type": "string"}, "criticality": {"type": "string"}, "status": {"type": "string"}, "description": {"type": "string"}}, "required": ["asset_name", "asset_type", "owner", "criticality", "status", "description"]}},
            "risks": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "category": {"type": "string"}, "likelihood": {"type": "integer", "minimum": 1, "maximum": 5}, "impact": {"type": "integer", "minimum": 1, "maximum": 5}, "owner": {"type": "string"}, "treatment": {"type": "string"}, "status": {"type": "string"}}, "required": ["title", "description", "category", "likelihood", "impact", "owner", "treatment", "status"]}},
            "controls": {"type": "array", "items": {"type": "object", "properties": {"control_name": {"type": "string"}, "description": {"type": "string"}, "framework": {"type": "string"}, "control_category": {"type": "string"}, "owner": {"type": "string"}, "implementation_status": {"type": "string"}}, "required": ["control_name", "description", "framework", "control_category", "owner", "implementation_status"]}},
            "findings": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}, "source": {"type": "string"}, "severity": {"type": "string"}, "recommendation": {"type": "string"}, "status": {"type": "string"}}, "required": ["title", "description", "source", "severity", "recommendation", "status"]}},
            "remediation_actions": {"type": "array", "items": {"type": "object", "properties": {"action": {"type": "string"}, "owner": {"type": "string"}, "priority": {"type": "string"}, "due_date": {"type": "string"}, "status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["action", "owner", "priority", "status"]}},
            "vendors": {"type": "array", "items": {"type": "object", "properties": {"vendor_name": {"type": "string"}, "service": {"type": "string"}, "criticality": {"type": "string"}, "risk_level": {"type": "string"}, "assessment_status": {"type": "string"}, "notes": {"type": "string"}}, "required": ["vendor_name", "service", "criticality", "risk_level", "assessment_status", "notes"]}},
        },
        "required": ["customer", "assessment", "organization_profile", "assessment_scope", "risks", "controls", "findings"],
    }

    schema_example = {
        "customer": {"name": "string", "industry": "string", "company_size": "string"},
        "assessment": {"assessment_type": "string", "framework": "string", "start_date": "string", "end_date": "string", "pricing": 1000, "status": "string"},
        "organization_profile": {"legal_name": "string", "industry": "string", "employee_count": "string", "location": "string", "business_description": "string", "critical_business_functions": "string"},
        "assessment_scope": {"scope_description": "string", "in_scope_assets": "string", "out_of_scope_assets": "string", "internal_network": "Yes|No", "web_applications": "Yes|No", "cloud_infrastructure": "Yes|No", "security_policies": "Yes|No", "physical_security": "Yes|No", "hr_systems": "Yes|No", "assessment_activities": "string"},
        "policies": [{"policy_name": "string", "description": "string", "owner": "string", "status": "string", "version": "string", "review_date": "string"}],
        "assets": [{"asset_name": "string", "asset_type": "string", "ip_address": "string", "operating_system": "string", "owner": "string", "criticality": "string", "status": "string", "description": "string"}],
        "risks": [{"title": "string", "description": "string", "category": "string", "likelihood": 3, "impact": 5, "owner": "string", "treatment": "string", "status": "string"}],
        "controls": [{"control_name": "string", "description": "string", "framework": "string", "control_category": "string", "owner": "string", "implementation_status": "string"}],
        "findings": [{"title": "string", "description": "string", "source": "string", "severity": "Low|Medium|High|Critical", "recommendation": "string", "status": "string"}],
        "remediation_actions": [{"action": "string", "owner": "string", "priority": "string", "due_date": "string", "status": "string", "notes": "string"}],
        "vendors": [{"vendor_name": "string", "service": "string", "criticality": "string", "risk_level": "string", "assessment_status": "string", "notes": "string"}],
    }

    instruction = f"""
You are the GRC assessment generator inside a cybersecurity assessment application.
MODE: {mode}

The backend is explicitly telling you which generation mode to use.
- If mode is "generate": create a complete assessment from scratch from the user's prompt.
- If mode is "modify": update the existing assessment provided in the context.

USER PROMPT:
{prompt}

CONTEXT:
{json.dumps(context_payload, default=str, indent=2)}

PRIMARY RULES:
1. Return valid JSON only.
2. Use the exact structure below.
3. Do not return Markdown or extra narrative.
4. Do not invent credentials, system names, or sensitive data. If facts are not provided, state clear assumptions in the generated content instead of pretending to know facts.
5. Generate complete, internally consistent assessment content rather than just a summary.
6. Preserve the application schema exactly and keep values practical and realistic.
7. Use integers 1-5 for likelihood and impact.
8. The assessment must be useful to populate the application's existing UI and database tables.

SCHEMA:
{json.dumps(schema_example, indent=2)}
"""

    try:
        client = _create_gemini_client()
        interaction = client.interactions.create(
            model=GEMINI_MODEL,
            input=instruction,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            timeout=float(GEMINI_REQUEST_TIMEOUT_SEC),
        )
        output_text = _extract_gemini_output_text(interaction)
        if not output_text:
            raise RuntimeError("Gemini returned an empty assessment response.")

        result = json.loads(output_text, parse_int=_safe_json_parse_int)
        if not isinstance(result, dict):
            raise RuntimeError("Gemini returned an unexpected assessment format.")

        valid, errors = validate_generated_assessment(result)
        if not valid:
            raise ValueError("AI output failed validation: " + "; ".join(errors))

        return result
    except Exception:
        app.logger.exception("AI assessment generation failed")
        raise


def call_gemini_ai_review(prompt, assessment_id):
    """Ask Gemini for a proposal against the current assessment."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured in the .env file.")

    app.logger.info(
        "AI review diagnostic: stage 1 get_ai_review_context start "
        "assessment_id=%s",
        assessment_id,
    )
    try:
        context = get_ai_review_context(assessment_id)
    except Exception:
        app.logger.exception(
            "AI review diagnostic: stage 1 get_ai_review_context failed"
        )
        raise

    app.logger.info(
        "AI review diagnostic: stage 1 get_ai_review_context ok "
        "context_keys=%s",
        sorted(context.keys()) if isinstance(context, dict) else type(context).__name__,
    )

    if context is None:
        raise RuntimeError(
            "The selected assessment could not be found for the current user."
        )

    app.logger.info(
        "AI review diagnostic: stage 2 building Gemini instruction/schema start"
    )
    change_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["update", "create"],
            },
            "table": {"type": "string"},
            "record_id": {"type": "string"},
            "field": {"type": "string"},
            "new_value": {"type": "string"},
            "data": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "reason": {"type": "string"},
        },
        "required": ["action", "table", "reason"],
    }

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "recommendations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "changes": {
                "type": "array",
                "items": change_schema,
            },
        },
        "required": ["summary", "recommendations", "changes"],
    }

    instruction = f"""
You are the GRC modification engine inside a cybersecurity assessment
application.

The user is asking you to modify the CURRENT assessment shown below.

USER REQUEST:
{prompt}

CURRENT ASSESSMENT DATA:
{json.dumps(_json_safe_ai_context(context), default=str, indent=2)}

PRIMARY RULE:
If the user's request refers to something that exists in the supplied current
assessment/GRC data, produce a concrete proposal that modifies that existing
record. Do not merely explain what should be changed.

You must:
1. Understand natural language, including informal wording and synonyms.
2. Find the relevant table and existing record in CURRENT ASSESSMENT DATA.
3. Return an UPDATE for an existing record whenever the requested thing exists.
4. Return a CREATE only when the user explicitly asks to add/create a new GRC item.
5. Never delete anything.
6. Never modify a record outside the current assessment.
7. Never invent a record.
8. For UPDATE, provide:
   action="update", table, field, new_value, reason.
9. new_value MUST be the actual requested new value. NEVER return null or omit
   it for an UPDATE.
10. For UPDATE record_id:
    - for "assessments" and "customers", record_id may be omitted because the
      backend resolves the current record;
    - for other tables, copy the existing record ID exactly as a STRING.
11. Never invent, calculate, concatenate, hash, or generate record IDs.
12. Use only the allowed fields listed below.
13. Do not update system fields such as id, assessment_id, created_at,
    updated_at, risk_score, or risk_level.
14. For risk likelihood and impact, use integers 1-5.
15. Do not expose credentials, API keys, passwords, session secrets, or file
    paths.
16. If the request is already satisfied, return an empty changes array and
    explain why.
17. Keep the summary and recommendations concise.
18. Return valid JSON matching the supplied schema.

Examples:
- "Change the assessment framework from ISO 27001 to SOC 2"
  -> update assessments.framework to "SOC 2".
- "Change the assessment status from In Progress to Completed"
  -> update assessments.status to "Completed".
- "Make the company size 250-300"
  -> update customers.company_size to "250-300".
- "Change the risk 'Weak password policy' to High impact"
  -> find that risk and update risks.impact to 5.
- "Mark the control 'MFA' as Implemented"
  -> find the existing control and update controls.implementation_status.
- "Change the finding severity to Critical"
  -> find the relevant existing finding and update findings.severity.

ALLOWED TABLES AND FIELDS:
{json.dumps({k: sorted(v) for k, v in AI_CHANGE_RULES.items()}, indent=2)}
"""

    app.logger.info(
        "AI review diagnostic: stage 2 building Gemini instruction/schema ok "
        "instruction_chars=%s",
        len(instruction),
    )

    app.logger.info("AI review diagnostic: stage 3 genai.Client start")

    try:
        client = _create_gemini_client()
    except Exception:
        app.logger.exception("AI review diagnostic: stage 3 genai.Client failed")
        raise
    app.logger.info("AI review diagnostic: stage 3 genai.Client ok")

    app.logger.info(
        "AI review diagnostic: stage 4 interactions.create start model=%s",
        GEMINI_MODEL,
    )
    try:
        interaction = client.interactions.create(
            model=GEMINI_MODEL,
            input=instruction,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            timeout=float(GEMINI_REQUEST_TIMEOUT_SEC),
        )
        output_text = _extract_gemini_output_text(interaction)
        if not output_text:
            raise RuntimeError("Gemini returned an empty response.")
    except Exception as exc:
        app.logger.exception(
            "AI review diagnostic: stage 4 interactions.create failed"
        )

        # The Interactions API is the primary path. Fall back to the standard
        # generate_content endpoint if the installed SDK/model rejects the
        # interaction request. This keeps the GRC feature usable across SDK
        # versions while preserving the same JSON schema.
        app.logger.warning(
            "AI review: trying generate_content fallback after Interactions failure: %s",
            exc,
        )
        try:
            fallback_response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=instruction,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
                ),
            )
            output_text = _extract_gemini_output_text(fallback_response)
            if not output_text:
                raise RuntimeError("Gemini fallback returned an empty response.")
            app.logger.info("AI review diagnostic: generate_content fallback ok")
        except Exception as fallback_exc:
            app.logger.exception("AI review generate_content fallback failed")
            raise RuntimeError(
                "Gemini could not be reached. "
                "If you see WinError 10061, a Windows/system proxy or firewall "
                "is blocking the connection. The app now forces direct HTTP "
                "connections; restart the app after replacing app.py."
            ) from fallback_exc

    app.logger.info("AI review diagnostic: stage 4 Gemini response received")

    try:
        result = json.loads(
            output_text,
            parse_int=_safe_json_parse_int,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Gemini returned JSON in an unexpected format.")

    return result


def apply_ai_changes(assessment_id, changes):
    """
    Execute only validated AI proposals.

    The current assessment/customer IDs are resolved here, never trusted from
    the browser or Gemini.
    """
    applied = []
    rejected = []
    conn = get_db_connection()

    try:
        for original_change in changes:
            if not isinstance(original_change, dict):
                rejected.append({"reason": "Invalid change object.", "change": original_change})
                continue

            change = dict(original_change)
            action, error = _normalise_change(change)

            if error:
                rejected.append({"reason": error, "change": change})
                continue

            table = str(change.get("table") or "").strip()
            if table not in AI_CHANGE_RULES:
                rejected.append({
                    "reason": f"Table '{table}' is not allowed.",
                    "change": change,
                })
                continue

            allowed_fields = AI_CHANGE_RULES[table]

            # ---------------------------
            # UPDATE EXISTING RECORD
            # ---------------------------
            if action == "update":
                field = str(change.get("field") or "").strip()
                if not field:
                    field = _infer_ai_update_field(change)
                    change["field"] = field

                if field not in allowed_fields:
                    rejected.append({
                        "reason": f"Field '{field}' is not allowed for table '{table}'.",
                        "change": change,
                    })
                    continue

                # Never trust Gemini/browser for these two IDs.
                if table == "assessments":
                    record_id = _safe_ai_record_id(assessment_id)
                elif table == "customers":
                    row = conn.execute(
                        """
                        SELECT customer_id
                        FROM assessments
                        WHERE id = ? AND user_id = ?
                        LIMIT 1
                        """,
                        (assessment_id, session.get("user_id")),
                    ).fetchone()
                    record_id = _safe_ai_record_id(
                        row["customer_id"] if row else None
                    )
                else:
                    record_id = _best_record_id(
                        conn,
                        table,
                        assessment_id,
                        str(change.get("_user_prompt") or ""),
                        change,
                    )

                if record_id is None:
                    rejected.append({
                        "reason": (
                            "Could not safely resolve the existing record "
                            "for this request."
                        ),
                        "change": change,
                    })
                    continue

                if not _record_belongs_to_assessment(
                    conn, table, record_id, assessment_id
                ):
                    rejected.append({
                        "reason": "The target record does not belong to the current assessment.",
                        "change": change,
                    })
                    continue

                blocked = {
                    "id", "assessment_id", "created_at", "updated_at",
                    "risk_score", "risk_level"
                }
                if field in blocked:
                    rejected.append({
                        "reason": f"Field '{field}' is system-managed.",
                        "change": change,
                    })
                    continue

                try:
                    value = _coerce_ai_value(
                        table, field, change.get("new_value")
                    )
                except ValueError as exc:
                    # One last deterministic recovery from the actual user
                    # request stored in the proposal, if available.
                    value = None
                    try:
                        value = _coerce_ai_value(
                            table,
                            field,
                            _extract_requested_value(
                                change.get("_user_prompt", ""),
                                field=field,
                            ),
                        )
                    except ValueError:
                        rejected.append({
                            "reason": str(exc),
                            "change": change,
                        })
                        continue

                change["record_id"] = str(record_id)
                change["new_value"] = value

                if table == "customers":
                    sql = f"""
                        UPDATE customers
                        SET {field} = ?
                        WHERE id = ?
                        AND id IN (
                            SELECT customer_id
                            FROM assessments
                            WHERE id = ? AND user_id = ?
                        )
                    """
                    params = (
                        value, record_id, assessment_id, session.get("user_id")
                    )

                elif table == "assessments":
                    sql = f"""
                        UPDATE assessments
                        SET {field} = ?
                        WHERE id = ? AND user_id = ?
                    """
                    params = (value, record_id, session.get("user_id"))

                elif table == "control_tests":
                    sql = f"""
                        UPDATE control_tests
                        SET {field} = ?
                        WHERE id = ?
                        AND control_id IN (
                            SELECT id FROM controls WHERE assessment_id = ?
                        )
                    """
                    params = (value, record_id, assessment_id)

                else:
                    sql = f"""
                        UPDATE {table}
                        SET {field} = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND assessment_id = ?
                    """
                    params = (value, record_id, assessment_id)

                try:
                    cursor = conn.execute(sql, params)
                except sqlite3.IntegrityError as exc:
                    rejected.append({
                        "reason": f"Database rejected the proposed value: {exc}",
                        "change": change,
                    })
                    continue

                if cursor.rowcount != 1:
                    rejected.append({
                        "reason": "Database update did not affect exactly one record.",
                        "change": change,
                    })
                    continue

                if table == "risks" and field in {"likelihood", "impact"}:
                    _recalculate_risk(conn, record_id)

                applied.append({
                    "action": "updated",
                    "table": table,
                    "record_id": record_id,
                    "field": field,
                    "new_value": value,
                    "reason": change.get("reason", ""),
                })
                continue

            # ---------------------------
            # CREATE NEW RECORD
            # ---------------------------
            data = change.get("data")
            if not isinstance(data, dict):
                data = {}

            # Do not let AI create customers/assessments.
            if table in {"customers", "assessments", "evidence", "control_tests"}:
                rejected.append({
                    "reason": f"Creating records in '{table}' through AI is disabled.",
                    "change": change,
                })
                continue

            if not data:
                rejected.append({
                    "reason": f"Create operation for '{table}' has no data.",
                    "change": change,
                })
                continue

            safe_data = {}
            blocked = {
                "id", "assessment_id", "created_at", "updated_at",
                "risk_score", "risk_level"
            }

            for field, raw_value in data.items():
                if field in blocked or field not in allowed_fields:
                    continue
                try:
                    safe_data[field] = _coerce_ai_value(
                        table, field, raw_value
                    )
                except ValueError:
                    continue

            missing = AI_CREATE_REQUIRED.get(table, set()) - set(safe_data)
            if missing:
                rejected.append({
                    "reason": (
                        "Create operation is missing required field(s): "
                        + ", ".join(sorted(missing))
                    ),
                    "change": change,
                })
                continue

            # Validate foreign keys that AI is allowed to reference.
            if table == "controls" and safe_data.get("related_risk_id") is not None:
                if not _record_belongs_to_assessment(
                    conn, "risks", safe_data["related_risk_id"], assessment_id
                ):
                    rejected.append({
                        "reason": "related_risk_id does not belong to this assessment.",
                        "change": change,
                    })
                    continue

            if table == "remediation_actions":
                if safe_data.get("finding_id") is not None and not _record_belongs_to_assessment(
                    conn, "findings", safe_data["finding_id"], assessment_id
                ):
                    rejected.append({
                        "reason": "finding_id does not belong to this assessment.",
                        "change": change,
                    })
                    continue

                if safe_data.get("risk_id") is not None and not _record_belongs_to_assessment(
                    conn, "risks", safe_data["risk_id"], assessment_id
                ):
                    rejected.append({
                        "reason": "risk_id does not belong to this assessment.",
                        "change": change,
                    })
                    continue

            duplicate_field = None
            for candidate in (
                "control_name", "policy_name", "title",
                "asset_name", "vendor_name"
            ):
                if candidate in safe_data:
                    duplicate_field = candidate
                    break

            if duplicate_field:
                duplicate = conn.execute(
                    f"""
                    SELECT id
                    FROM {table}
                    WHERE assessment_id = ?
                    AND LOWER(COALESCE({duplicate_field}, '')) =
                        LOWER(COALESCE(?, ''))
                    LIMIT 1
                    """,
                    (assessment_id, safe_data[duplicate_field]),
                ).fetchone()

                if duplicate:
                    rejected.append({
                        "reason": (
                            f"An equivalent '{table}' record already exists "
                            f"(ID {duplicate['id']})."
                        ),
                        "change": change,
                    })
                    continue

            columns = ["assessment_id"] + list(safe_data.keys())
            values = [assessment_id] + list(safe_data.values())
            placeholders = ", ".join(["?"] * len(values))

            cursor = conn.execute(
                f"""
                INSERT INTO {table} ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                values,
            )
            new_id = cursor.lastrowid

            if table == "risks":
                _recalculate_risk(conn, new_id)

            applied.append({
                "action": "created",
                "table": table,
                "record_id": new_id,
                "name": (
                    safe_data.get("control_name")
                    or safe_data.get("policy_name")
                    or safe_data.get("title")
                    or safe_data.get("asset_name")
                    or safe_data.get("vendor_name")
                    or table
                ),
                "reason": change.get("reason", ""),
            })

        conn.commit()

        for item in applied:
            log_audit(
                "AI GRC modification",
                item["table"],
                item["record_id"],
                (
                    f"AI {item['action']} operation applied. "
                    f"Field: {item.get('field', 'new record')}. "
                    f"Reason: {item.get('reason', '')}"
                ),
                assessment_id,
            )

        return applied, rejected

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _load_proposal_from_request():
    # 1. Direct raw JSON payload from form or query parameters
    raw = request.form.get("proposal_json", "").strip() or request.args.get("proposal_json", "").strip()
    if raw:
        try:
            proposal = json.loads(raw, parse_int=_safe_json_parse_int)
            if isinstance(proposal, dict):
                return proposal
        except json.JSONDecodeError as exc:
            app.logger.warning("Failed to decode proposal_json from request: %s", exc)

    # 2. Check explicit assessment_id passed in form or query parameters
    assessment_id = request.form.get("assessment_id") or request.args.get("assessment_id")
    if assessment_id:
        try:
            saved = get_business_proposal(int(assessment_id))
            if saved:
                return saved
        except (ValueError, TypeError):
            pass

    # 3. Check active_proposal stored in session
    if "active_proposal" in session and isinstance(session["active_proposal"], dict):
        return session["active_proposal"]

    # 4. Fallback to latest assessment's business proposal
    assessment = get_latest_assessment()
    if assessment:
        saved = get_business_proposal(assessment["id"])
        if saved:
            return saved

    raise ValueError("No proposal data was found to generate the document download.")


def _is_business_proposal_payload(proposal):
    if not isinstance(proposal, dict):
        return False
    if proposal.get("_proposal_type") == "business" or "executiveSummary" in proposal or "executive_summary" in proposal or "commercials" in proposal or "aboutVendor" in proposal or "about_vendor" in proposal or "sections" in proposal:
        return True
    required = {
        "introduction",
        "project_overview",
        "scope_of_work",
        "deliverables",
        "methodology",
        "timeline",
        "pricing",
        "next_steps",
        "closing",
    }
    return required.issubset(set(proposal.keys()))


def _proposal_document_context(proposal, assessment=None):
    def clean(value, default="Not specified"):
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def clean_list(value, default_list=None):
        if isinstance(value, list):
            res = [clean(item, "") for item in value if clean(item, "")]
            if res:
                return res
        elif isinstance(value, str) and value.strip():
            return [value.strip()]
        return default_list or ["Details to be finalized during engagement onboarding."]

    if _is_business_proposal_payload(proposal):
        proposal = _normalize_proposal(proposal)
        pricing_raw = proposal.get("commercials") or proposal.get("pricing") or {}
        pricing = pricing_raw if isinstance(pricing_raw, dict) else {}
        inputs = proposal.get("_inputs") or {}
        currency = str(inputs.get("currency") or proposal.get("currency") or "INR").strip().upper()
        if currency not in CURRENCY_SYMBOLS:
            currency = "INR"

        raw_amount = pricing.get("amount") or proposal.get("pricing_amount")
        formatted_pricing_amount = format_currency_amount(raw_amount, currency)

        template_id = (
            proposal.get("template_id")
            or (proposal.get("_inputs") or {}).get("template_id")
            or "default_whitehats"
        )
        current_user_id = session.get("user_id") if (has_request_context() and "user_id" in session) else None
        current_username = session.get("username") if (has_request_context() and "username" in session) else None
        template_info = proposal.get("template") or get_template_info(template_id, current_user_id)

        return {
            "kind": "business",
            "template_id": template_id,
            "template": template_info,
            "title": clean(proposal.get("title"), "AI Business Proposal"),
            "client": clean(proposal.get("client") or (assessment["customer_name"] if assessment else "")),
            "currency": currency,
            "project_type": clean(
                proposal.get("project_type")
                or (proposal.get("_inputs") or {}).get("project_type")
                or (assessment["assessment_type"] if assessment else "")
            ),
            "sections": proposal.get("sections") or [],
            "executive_summary": clean(proposal.get("executive_summary") or proposal.get("introduction"), "Executive summary overview of the engagement."),
            "about_vendor": clean(proposal.get("about_vendor"), DEFAULT_VENDOR_PROFILE),
            "understanding_requirement": clean(proposal.get("understanding_requirement") or proposal.get("project_overview"), "Comprehensive evaluation of security posture, framework compliance, and asset risk."),
            "proposed_solution": clean_list(proposal.get("proposed_solution") or proposal.get("deliverables"), ["Turnkey security assessment, automated scan discovery, and executive roadmap."]),
            "scope_of_work": clean_list(proposal.get("scope_of_work"), ["Internal and external network subnet scanning", "Access control evaluation", "Compliance gap mapping"]),
            "out_of_scope": clean_list(proposal.get("out_of_scope"), ["Third-party code remediation", "Hardware replacement procurement"]),
            "deployment_approach": clean_list(proposal.get("deployment_approach") or proposal.get("methodology"), ["Phase 1: Reconnaissance & Discovery", "Phase 2: Risk & Gap Analysis", "Phase 3: Executive Reporting"]),
            "prerequisites": clean_list(proposal.get("prerequisites"), ["Authorized IP whitelist confirmation", "Designated technical point of contact"]),
            "infrastructure_requirements": clean_list(proposal.get("infrastructure_requirements"), ["Subnet CIDR range specification", "Standard auditor workstation access"]),
            "information_security": clean_list(proposal.get("information_security"), ["ISO 27001 & NIST CSF control alignment", "Encrypted evidence storage"]),
            "training_change": clean_list(proposal.get("training_change"), ["Post-assessment remediation workshop for engineering teams"]),
            "support_sla": clean_list(proposal.get("support_sla"), ["8/5 technical support during engagement lifecycle", "24-hour response for critical findings"]),
            "pricing_amount": clean(formatted_pricing_amount, "To be confirmed"),
            "pricing_description": clean(pricing.get("description"), "Fixed engagement investment including all deliverables and reports."),
            "license_renewal": clean(proposal.get("license_renewal"), "12-month platform assessment license included."),
            "payment_terms": clean_list(proposal.get("payment_terms"), ["50% upon engagement kickoff", "50% upon final deliverable acceptance"]),
            "legal_terms": clean_list(proposal.get("legal_terms"), ["Mutual Non-Disclosure Agreement (NDA) applies", "Standard liability limit"]),
            "project_governance": clean_list(proposal.get("project_governance") or proposal.get("next_steps"), ["Weekly progress review meetings", "Designated escalation matrix"]),
            "acceptance_criteria": clean_list(proposal.get("acceptance_criteria"), ["Delivery of final executive security report and remediation roadmap"]),
            "risks_assumptions": clean_list(proposal.get("risks_assumptions") or [proposal.get("closing")], ["Target systems will remain accessible during scheduled scan windows"]),
            "prepared_by": clean(current_username, "Proposal Lead"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    recommendations = proposal.get("recommendations") or []
    if not isinstance(recommendations, list):
        recommendations = [recommendations]

    changes = []
    for change in proposal.get("changes") or []:
        if not isinstance(change, dict):
            continue

        action = clean(change.get("action"), "update").title()
        table = clean(change.get("table"), "record")
        field = clean(change.get("field"), "new record")
        data = change.get("data")
        if isinstance(data, dict) and not change.get("new_value"):
            value = "; ".join(
                f"{clean(key)}: {clean(val)}" for key, val in data.items()
            )
        else:
            value = clean(change.get("new_value"))

        changes.append(
            {
                "action": action,
                "target": f"{table}.{field}",
                "value": value,
                "reason": clean(change.get("reason"), ""),
            }
        )

    return {
        "kind": "grc",
        "title": f"{clean(assessment['customer_name'])} AI Proposal",
        "customer": clean(proposal.get("customer") or assessment["customer_name"]),
        "framework": clean(proposal.get("framework") or assessment["framework"]),
        "assessment_type": clean(assessment["assessment_type"]),
        "dates": f"{clean(assessment['start_date'])} to {clean(assessment['end_date'])}",
        "reason": clean(proposal.get("reason")),
        "summary": clean(proposal.get("summary"), ""),
        "recommendations": [clean(item) for item in recommendations if clean(item, "")],
        "changes": changes,
        "prepared_by": clean(session.get("username"), "Proposal Lead"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _proposal_filename(context, extension):
    base_name = context.get("client") if context.get("kind") == "business" else context.get("customer")
    base = secure_filename(str(base_name or "").lower().replace(" ", "-"))
    if not base:
        base = "proposal"
    suffix = "business-proposal" if context.get("kind") == "business" else "ai-proposal"
    return f"{base}-{suffix}.{extension}"


def _proposal_lines(context):
    if context.get("kind") == "business":
        lines = [
            context["title"],
            "",
            f"Client: {context['client']}",
            f"Project: {context['project_type']}",
            f"Prepared by: {context['prepared_by']}",
            f"Generated: {context['generated_at']}",
            "",
        ]
        sections = context.get("sections") or []
        if sections:
            for idx, sec in enumerate(sections, start=1):
                sec_title = str(sec.get("title") or f"SECTION {idx}").upper()
                lines.append(f"{idx}. {sec_title}")
                content = sec.get("content") or ""
                if isinstance(content, dict) and "amount" in content:
                    lines.append(f"Total Contribution: {content.get('amount')}")
                    if content.get("description"):
                        lines.append(str(content.get("description")))
                elif isinstance(content, list):
                    for item in content:
                        lines.append(f"- {item}")
                else:
                    clean_text = re.sub(r'<li[^>]*>', '- ', str(content))
                    clean_text = re.sub(r'<br\s*/?>', '\n', clean_text)
                    clean_text = re.sub(r'</p>', '\n\n', clean_text)
                    clean_text = re.sub(r'<[^>]+>', '', clean_text)
                    for l in clean_text.strip().split("\n"):
                        if l.strip():
                            lines.append(l.strip())
                lines.append("")
            return lines

        lines.extend([
            "1. EXECUTIVE SUMMARY",
            context["executive_summary"],
            "",
            "2. ABOUT THE VENDOR",
            context["about_vendor"],
            "",
            "3. UNDERSTANDING OF REQUIREMENT",
            context["understanding_requirement"],
            "",
            "4. PROPOSED SOLUTION",
        ])
        lines.extend(f"- {item}" for item in context["proposed_solution"])
        lines.extend(["", "5. SCOPE OF WORK (IN SCOPE)"])
        lines.extend(f"- {item}" for item in context["scope_of_work"])
        lines.extend(["", "6. OUT OF SCOPE"])
        lines.extend(f"- {item}" for item in context["out_of_scope"])
        lines.extend(["", "7. DEPLOYMENT & IMPLEMENTATION APPROACH"])
        lines.extend(f"- {item}" for item in context["deployment_approach"])
        lines.extend(["", "8. PREREQUISITES"])
        lines.extend(f"- {item}" for item in context["prerequisites"])
        lines.extend(["", "9. INFRASTRUCTURE REQUIREMENTS"])
        lines.extend(f"- {item}" for item in context["infrastructure_requirements"])
        lines.extend(["", "10. INFORMATION SECURITY & COMPLIANCE"])
        lines.extend(f"- {item}" for item in context["information_security"])
        lines.extend(["", "11. TRAINING & CHANGE MANAGEMENT"])
        lines.extend(f"- {item}" for item in context["training_change"])
        lines.extend(["", "12. SUPPORT & SLA"])
        lines.extend(f"- {item}" for item in context["support_sla"])
        lines.extend([
            "",
            "13. COMMERCIALS",
            f"Total Contribution: {context['pricing_amount']}",
            context["pricing_description"],
            "",
            "14. LICENSE TERM & RENEWAL",
            context["license_renewal"],
            "",
            "15. PAYMENT TERMS",
        ])
        lines.extend(f"- {item}" for item in context["payment_terms"])
        lines.extend(["", "16. LEGAL / COMMERCIAL TERMS"])
        lines.extend(f"- {item}" for item in context["legal_terms"])
        lines.extend(["", "17. PROJECT GOVERNANCE & ESCALATION"])
        lines.extend(f"- {item}" for item in context["project_governance"])
        lines.extend(["", "18. ACCEPTANCE CRITERIA"])
        lines.extend(f"- {item}" for item in context["acceptance_criteria"])
        lines.extend(["", "19. RISKS & ASSUMPTIONS"])
        lines.extend(f"- {item}" for item in context["risks_assumptions"])
        return lines

    lines = [
        "AgentScan AI Proposal",
        "",
        f"Customer: {context['customer']}",
        f"Framework: {context['framework']}",
        f"Assessment type: {context['assessment_type']}",
        f"Assessment dates: {context['dates']}",
        f"Prepared by: {context['prepared_by']}",
        f"Generated: {context['generated_at']}",
        "",
        "Reason",
        context["reason"],
    ]

    if context["summary"]:
        lines.extend(["", "AI Summary", context["summary"]])

    lines.append("")
    lines.append("Recommendations")
    if context["recommendations"]:
        lines.extend(f"- {item}" for item in context["recommendations"])
    else:
        lines.append("No recommendations were included.")

    lines.append("")
    lines.append("Proposed Changes")
    if context["changes"]:
        for index, change in enumerate(context["changes"], start=1):
            lines.append(
                f"{index}. {change['action']} {change['target']} -> {change['value']}"
            )
            if change["reason"]:
                lines.append(f"   Reason: {change['reason']}")
    else:
        lines.append("No database changes were proposed.")

    return lines


def _pdf_escape(text):
    text = str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return text.encode("latin-1", "replace").decode("latin-1")


def _build_fallback_pdf(context):
    wrapped_lines = []
    for line in _proposal_lines(context):
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(textwrap.wrap(str(line), width=92) or [""])

    per_page = 48
    pages = [
        wrapped_lines[index:index + per_page]
        for index in range(0, len(wrapped_lines), per_page)
    ] or [[]]

    objects = []

    def add_object(data):
        objects.append(data)
        return len(objects)

    catalog_id = add_object("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object("")
    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []

    for page_number, page_lines in enumerate(pages, start=1):
        commands = [
            "BT",
            "/F1 16 Tf",
            "50 750 Td",
            f"({_pdf_escape(context['title'])}) Tj",
            "/F1 10 Tf",
            "0 -22 Td",
            f"(Page {page_number} of {len(pages)}) Tj",
            "/F1 11 Tf",
            "0 -22 Td",
            "14 TL",
        ]
        for line in page_lines:
            commands.append(f"({_pdf_escape(line)}) Tj")
            commands.append("T*")
        commands.append("ET")
        stream = "\n".join(commands)
        content_id = add_object(
            f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"
        )
        page_id = add_object(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    )

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, data in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_id} 0 obj\n".encode("latin-1"))
        pdf.extend(data.encode("latin-1", "replace"))
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 0000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)


def _build_reportlab_fallback_pdf(context):
    """Render a clean, professional A4 PDF document using the registered template renderer."""
    try:
        tmpl_info = context.get("template") or {}
        tmpl_id = context.get("template_id") or tmpl_info.get("template_id") or DEFAULT_TEMPLATE_ID
        renderer = template_registry.get_renderer(tmpl_id)
        return renderer.render_pdf(context)
    except Exception as exc:
        app.logger.exception("ReportLab PDF generation failed, falling back to basic stream generator: %s", exc)
        return _build_fallback_pdf(context)


def _populate_custom_docx_template(doc, context):
    """
    Populate a custom python-docx Document template with proposal context.
    Strict Rule: Custom Template = Design + Structure ONLY.
    Current Proposal = Source of Truth for all proposal content.
    Sample business data from uploaded template files (such as 'Orion Industrial Systems',
    'Digital Operations Transformation', 'WH-ORION-026', '₹7,50,000') is scrubbed & replaced.
    """
    import re
    from datetime import datetime

    # Canonical current proposal values
    curr_client = str(context.get("client") or "Client Organization").strip()
    curr_title = str(context.get("title") or "Business Proposal").strip()
    curr_project = str(context.get("project_type") or "Cybersecurity Assessment").strip()
    curr_amount = str(context.get("pricing_amount") or "To be confirmed").strip()
    curr_code = str(context.get("code") or context.get("proposal_code") or context.get("id") or "PROP-2026").strip()
    curr_date = str(context.get("generated_at") or datetime.now().strftime("%Y-%m-%d")).strip()
    curr_vendor = str(context.get("prepared_by") or "Proposal Team").strip()
    curr_desc = str(context.get("pricing_description") or "Fixed engagement investment").strip()

    # Format helper for list/str fields
    def format_ctx_field(val):
        if isinstance(val, list):
            items = [re.sub(r'<[^>]+>', '', str(item)).strip() for item in val if str(item).strip()]
            if items:
                return "\n".join(f"• {item}" if not item.startswith("•") else item for item in items)
        if isinstance(val, dict) and "amount" in val:
            amt = val.get("amount") or ""
            desc = val.get("description") or ""
            return f"Total Contribution: {amt}\n{desc}".strip()
        return re.sub(r'<[^>]+>', '', str(val or "")).strip()

    ctx_summary = format_ctx_field(context.get("executive_summary") or context.get("introduction"))
    ctx_vendor = format_ctx_field(context.get("about_vendor"))
    ctx_req = format_ctx_field(context.get("understanding_requirement") or context.get("project_overview"))
    ctx_sol = format_ctx_field(context.get("proposed_solution") or context.get("deliverables"))
    ctx_scope = format_ctx_field(context.get("scope_of_work"))
    ctx_deploy = format_ctx_field(context.get("deployment_approach") or context.get("methodology"))
    ctx_terms = format_ctx_field(context.get("payment_terms") or context.get("legal_terms"))
    ctx_assumptions = format_ctx_field(context.get("risks_assumptions"))

    # 1. Explicit Mustache Tag Replacements
    tag_map = {
        "{{proposal_title}}": curr_title,
        "{{title}}": curr_title,
        "{proposal_title}": curr_title,
        "{title}": curr_title,
        "{{proposal_code}}": curr_code,
        "{{proposal_id}}": curr_code,
        "{{code}}": curr_code,
        "{proposal_code}": curr_code,
        "{proposal_id}": curr_code,
        "{code}": curr_code,
        "{{client_name}}": curr_client,
        "{{client}}": curr_client,
        "{client_name}": curr_client,
        "{client}": curr_client,
        "{{project_name}}": curr_project,
        "{{project_type}}": curr_project,
        "{{project}}": curr_project,
        "{project_name}": curr_project,
        "{project_type}": curr_project,
        "{project}": curr_project,
        "{{prepared_by}}": curr_vendor,
        "{prepared_by}": curr_vendor,
        "{{generated_date}}": curr_date,
        "{{generated_at}}": curr_date,
        "{generated_date}": curr_date,
        "{generated_at}": curr_date,
        "{{executive_summary}}": ctx_summary,
        "{{introduction}}": ctx_summary,
        "{{vendor_overview}}": ctx_vendor,
        "{{about_vendor}}": ctx_vendor,
        "{{requirements}}": ctx_req,
        "{{understanding_requirement}}": ctx_req,
        "{{project_overview}}": ctx_req,
        "{{proposed_solution}}": ctx_sol,
        "{{deliverables}}": ctx_sol,
        "{{scope_of_work}}": ctx_scope,
        "{{deployment_approach}}": ctx_deploy,
        "{{commercials_amount}}": curr_amount,
        "{{pricing_amount}}": curr_amount,
        "{{budget}}": curr_amount,
        "{{commercials}}": curr_amount,
        "{{commercials_description}}": curr_desc,
        "{{pricing_description}}": curr_desc,
        "{{payment_terms}}": ctx_terms,
        "{{legal_terms}}": ctx_terms,
        "{{assumptions}}": ctx_assumptions,
        "{{risks_assumptions}}": ctx_assumptions,
    }

    def replace_paragraph_text(p, new_text):
        """Replace text of paragraph p while preserving first run formatting."""
        if not p.runs:
            p.text = new_text
            return
        p.runs[0].text = new_text
        for r in p.runs[1:]:
            r.text = ""

    def process_run_replacements(p, map_dict):
        for k, v in map_dict.items():
            if k in p.text:
                if not p.runs:
                    p.text = p.text.replace(k, str(v))
                    continue
                full_text = "".join(r.text for r in p.runs)
                if k in full_text:
                    replaced = full_text.replace(k, str(v))
                    replace_paragraph_text(p, replaced)

    for p in doc.paragraphs:
        process_run_replacements(p, tag_map)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    process_run_replacements(p, tag_map)

    # 2. Key-Value Metadata Card / Cover Page Subtitle Transformation
    meta_patterns = [
        (re.compile(r'^(Client|Prepared For|Proposal For|Client Name)\s*:\s*(.*)', re.I), curr_client),
        (re.compile(r'^(Project|Project Scope|Project Name|Scope)\s*:\s*(.*)', re.I), curr_project),
        (re.compile(r'^(Proposal ID|Proposal Ref|Ref|ID|Code)\s*:\s*(.*)', re.I), curr_code),
        (re.compile(r'^(Total proposed fee|Proposed Fee|Budget|Investment|Total Contribution|Fee|Commercials)\s*:\s*(.*)', re.I), curr_amount),
        (re.compile(r'^(Prepared By|Proposed By|Author|Vendor)\s*:\s*(.*)', re.I), curr_vendor),
        (re.compile(r'^(Date|Generated Date)\s*:\s*(.*)', re.I), curr_date),
    ]

    for p in doc.paragraphs:
        text = p.text.strip()
        for rx, val in meta_patterns:
            m = rx.match(text)
            if m:
                lbl = m.group(1)
                replace_paragraph_text(p, f"{lbl}: {val}")
                break

    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    text = p.text.strip()
                    for rx, val in meta_patterns:
                        m = rx.match(text)
                        if m:
                            lbl = m.group(1)
                            replace_paragraph_text(p, f"{lbl}: {val}")
                            break

    # 3. Section Heading & Body Paragraph Mapping
    heading_section_map = [
        (["executive summary", "at a glance", "strategic case", "overview", "introduction"], ctx_summary),
        (["opportunity", "problem statement", "requirement", "client need", "background"], ctx_req),
        (["recommendation", "proposed solution", "solution architecture", "deliverables"], ctx_sol),
        (["scope", "workstreams", "scope of work", "in-scope"], ctx_scope),
        (["delivery model", "timeline", "implementation roadmap", "methodology"], ctx_deploy),
        (["investment", "commercials", "pricing", "financial model", "proposed fee"], f"Total Contribution: {curr_amount}\n{curr_desc}"),
        (["assumptions", "commercial assumptions", "terms", "terms and conditions"], ctx_assumptions or ctx_terms),
    ]

    current_mapped_content = None
    for p in doc.paragraphs:
        style_name = (p.style.name.lower() if p.style else "")
        p_text_lower = p.text.strip().lower()
        is_heading = (
            "heading" in style_name
            or "title" in style_name
            or any(p_text_lower.startswith(prefix) for prefix in ["01", "02", "03", "04", "05", "06", "07", "1.", "2.", "3.", "4.", "5."])
        )

        if is_heading:
            current_mapped_content = None
            for keywords, content in heading_section_map:
                if any(kw in p_text_lower for kw in keywords):
                    current_mapped_content = content
                    break
        elif current_mapped_content and p.text.strip():
            replace_paragraph_text(p, current_mapped_content)
            current_mapped_content = ""

    # 4. Commercial Pricing Table Row Transformation
    for tbl in doc.tables:
        table_text = " ".join(cell.text.lower() for row in tbl.rows for cell in row.cells)
        if any(w in table_text for w in ["fee", "pricing", "investment", "commercial", "amount", "total proposed fee"]):
            for row in tbl.rows:
                row_str = " ".join(c.text.lower() for c in row.cells)
                if not any(hdr in row_str for hdr in ["service", "deliverable", "description", "item", "commercial model"]):
                    if len(row.cells) >= 3:
                        replace_paragraph_text(row.cells[0].paragraphs[0] if row.cells[0].paragraphs else row.cells[0], curr_project)
                        replace_paragraph_text(row.cells[1].paragraphs[0] if row.cells[1].paragraphs else row.cells[1], "Fixed Price Engagement")
                        replace_paragraph_text(row.cells[2].paragraphs[0] if row.cells[2].paragraphs else row.cells[2], curr_amount)
                    elif len(row.cells) == 2:
                        replace_paragraph_text(row.cells[0].paragraphs[0] if row.cells[0].paragraphs else row.cells[0], curr_project)
                        replace_paragraph_text(row.cells[1].paragraphs[0] if row.cells[1].paragraphs else row.cells[1], curr_amount)

    # 5. Signature / Acceptance Block Update
    for p in doc.paragraphs:
        if any(term in p.text.lower() for term in ["authorized signature", "client signature", "prepared for client"]):
            if "(" in p.text:
                replace_paragraph_text(p, f"Authorized Signature ({curr_client})")
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if any(term in p.text.lower() for term in ["authorized signature", "client signature", "prepared for client"]):
                        if "(" in p.text:
                            replace_paragraph_text(p, f"Authorized Signature ({curr_client})")

    # 6. Hard Safety Net Scrubbing Pass for Sample Content Strings
    sample_scrub_rules = [
        ("Orion Industrial Systems", curr_client),
        ("Nova Retail Group", curr_client),
        ("Haven Manufacturing Group", curr_client),
        ("Alpha Performance Solutions", curr_vendor),
        ("Professional Consulting", curr_vendor),
        ("Digital Operations Transformation", curr_project),
        ("WH-ORION-026", curr_code),
        ("APS-HMG-20XX-0214", curr_code),
        ("₹7,50,000", curr_amount),
        ("7,50,000", curr_amount),
        ("₹6,25,000", curr_amount),
        ("6,25,000", curr_amount),
        ("24 August 2026", curr_date),
        ("24 AUGUST 2026", curr_date),
        ("February 14, 20XX", curr_date),
    ]

    def scrub_container(paragraphs):
        for p in paragraphs:
            for sample_str, target_val in sample_scrub_rules:
                if sample_str in p.text:
                    process_run_replacements(p, {sample_str: target_val})

    scrub_container(doc.paragraphs)

    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                scrub_container(cell.paragraphs)

    for section in doc.sections:
        if section.header:
            scrub_container(section.header.paragraphs)
            for tbl in section.header.tables:
                for r in tbl.rows:
                    for c in r.cells:
                        scrub_container(c.paragraphs)
        if section.footer:
            scrub_container(section.footer.paragraphs)
            for tbl in section.footer.tables:
                for r in tbl.rows:
                    for c in r.cells:
                        scrub_container(c.paragraphs)


def _build_proposal_docx(context):
    """Render DOCX document using python-docx according to selected template."""
    import docx
    from docx.shared import Inches, Pt, RGBColor

    template_id = str(context.get("template_id") or "default_whitehats").strip().lower()
    user_id = session.get("user_id") if (has_request_context() and "user_id" in session) else None
    template_info = context.get("template") or get_template_info(template_id, user_id)

    # Custom template processing
    if template_info.get("type") == "custom" and template_info.get("file_path"):
        custom_path = Path(template_info["file_path"])
        if custom_path.exists():
            try:
                doc = docx.Document(str(custom_path))
                _populate_custom_docx_template(doc, context)
                buffer = BytesIO()
                doc.save(buffer)
                return buffer.getvalue()
            except Exception as exc:
                app.logger.exception("Failed to render custom template DOCX: %s", exc)

    # Built-in System Templates
    doc = docx.Document()

    # Determine Template Design Tokens
    is_alternative = template_id in ("business_proposal_alternative_design", "alternative_design", "editorial_design")
    is_corporate = template_id in ("business_proposal_test_template", "test_template", "test_template_corporate")

    font_family = "Times New Roman" if is_alternative else "Calibri"
    title_color = RGBColor(15, 23, 42) if is_alternative else (RGBColor(30, 41, 59) if is_corporate else RGBColor(15, 23, 42))
    sub_color = RGBColor(79, 70, 229) if is_alternative else (RGBColor(2, 132, 199) if is_corporate else RGBColor(37, 99, 235))
    header_color = RGBColor(30, 27, 75) if is_alternative else (RGBColor(15, 23, 42) if is_corporate else RGBColor(30, 58, 138))

    header_text = (
        "EDITORIAL DESIGN — BUSINESS PROPOSAL" if is_alternative
        else ("CORPORATE PROPOSAL — TEST TEMPLATE A" if is_corporate else "BUSINESS PROPOSAL — PROFESSIONAL EDITION")
    )
    footer_text = (
        "EDITORIAL EDITION • CONFIDENTIAL PROPOSAL" if is_alternative
        else ("BUSINESS PROPOSAL TEST TEMPLATE — ENTERPRISE EDITION" if is_corporate else "CONFIDENTIAL — PREPARED FOR CLIENT REVIEW")
    )

    for section in doc.sections:
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
        section.top_margin = Inches(0.7 if is_alternative else 0.8)
        section.bottom_margin = Inches(0.7 if is_alternative else 0.8)
        section.left_margin = Inches(0.75 if is_alternative else 0.85)
        section.right_margin = Inches(0.75 if is_alternative else 0.85)

        # Header setup
        hdr_p = section.header.paragraphs[0]
        hdr_p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.RIGHT
        hdr_run = hdr_p.add_run(header_text)
        hdr_run.font.name = font_family
        hdr_run.font.size = Pt(8.5)
        hdr_run.font.bold = True
        hdr_run.font.color.rgb = sub_color if is_alternative else RGBColor(100, 116, 139)

        # Footer setup
        ftr_p = section.footer.paragraphs[0]
        ftr_run = ftr_p.add_run(footer_text)
        ftr_run.font.name = font_family
        ftr_run.font.size = Pt(8.5)
        ftr_run.font.color.rgb = RGBColor(100, 116, 139)

    # Document Title & Subtitle
    title_p = doc.add_paragraph()
    title_run = title_p.add_run(str(context.get("title") or "Business Proposal").upper())
    title_run.bold = True
    title_run.font.name = font_family
    title_run.font.size = Pt(22 if is_alternative else 20)
    title_run.font.color.rgb = title_color
    title_p.paragraph_format.space_after = Pt(2)

    sub_p = doc.add_paragraph()
    sub_text = f"PREPARED EXCLUSIVELY FOR {str(context.get('client') or 'CLIENT ORGANIZATION').upper()}" if is_alternative else f"PREPARED FOR {str(context.get('client') or 'CLIENT ORGANIZATION').upper()}"
    sub_run = sub_p.add_run(sub_text)
    sub_run.bold = True
    sub_run.font.name = font_family
    sub_run.font.size = Pt(11)
    sub_run.font.color.rgb = sub_color
    sub_p.paragraph_format.space_after = Pt(12)

    # Render Metadata or KPI Stat Highlight Card
    if is_alternative:
        # KPI Stat Callout Table for Alternative Design
        kpi_table = doc.add_table(rows=2, cols=3)
        kpi_items = [
            ("TOTAL CONTRIBUTION", str(context.get("pricing_amount") or "Contribution-Based")),
            ("PROJECT TIMELINE", "4–6 Weeks"),
            ("ENGAGEMENT TYPE", str(context.get("project_type") or "Cybersecurity Engagement")),
        ]
        for col_idx, (lbl, val) in enumerate(kpi_items):
            cell_lbl = kpi_table.cell(0, col_idx).paragraphs[0]
            r_l = cell_lbl.add_run(lbl)
            r_l.bold = True
            r_l.font.name = font_family
            r_l.font.size = Pt(8)
            r_l.font.color.rgb = RGBColor(100, 116, 139)

            cell_val = kpi_table.cell(1, col_idx).paragraphs[0]
            r_v = cell_val.add_run(val)
            r_v.bold = True
            r_v.font.name = font_family
            r_v.font.size = Pt(11)
            r_v.font.color.rgb = sub_color
        doc.add_paragraph().paragraph_format.space_after = Pt(10)
    else:
        # Structured Metadata Card Table
        table = doc.add_table(rows=3, cols=2)
        meta_pairs = [
            ("Client Name:", str(context.get("client") or "Client Organization")),
            ("Project Scope:", str(context.get("project_type") or "Cybersecurity Assessment")),
            ("Prepared By:", str(context.get("prepared_by") or "Proposal Team")),
            ("Generated Date:", str(context.get("generated_at") or "")),
            ("Template:", str(template_info.get("name") or "Standard Professional")),
            ("Proposal Status:", "Final Proposal"),
        ]
        for idx, (lbl, val) in enumerate(meta_pairs):
            row_idx = idx // 2
            col_idx = idx % 2
            cell = table.cell(row_idx, col_idx)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            r_lbl = p.add_run(f"{lbl} ")
            r_lbl.bold = True
            r_lbl.font.name = font_family
            r_lbl.font.size = Pt(9.5)
            r_lbl.font.color.rgb = RGBColor(100, 116, 139)
            r_val = p.add_run(val)
            r_val.font.name = font_family
            r_val.font.size = Pt(9.5)
            r_val.font.color.rgb = RGBColor(15, 23, 42)
        doc.add_paragraph().paragraph_format.space_after = Pt(10)

    # Proposal Sections
    sections = context.get("sections") or []
    if sections:
        for idx, sec in enumerate(sections, start=1):
            sec_title = str(sec.get("title") or f"SECTION {idx}").upper()
            h = doc.add_paragraph()
            h_run = h.add_run(f"{idx}. {sec_title}")
            h_run.bold = True
            h_run.font.name = font_family
            h_run.font.size = Pt(12)
            h_run.font.color.rgb = header_color
            h.paragraph_format.space_before = Pt(14)
            h.paragraph_format.space_after = Pt(4)

            content = sec.get("content") or ""
            if isinstance(content, dict) and "amount" in content:
                p = doc.add_paragraph()
                p.paragraph_format.space_after = Pt(4)
                r = p.add_run(f"Total Contribution: {content.get('amount')}")
                r.bold = True
                r.font.name = font_family
                r.font.size = Pt(10)
                if content.get("description"):
                    dp = doc.add_paragraph(str(content.get("description")))
                    dp.paragraph_format.space_after = Pt(6)
            elif isinstance(content, list):
                for item in content:
                    clean_item = re.sub(r'<[^>]+>', '', str(item)).strip()
                    if clean_item:
                        bp = doc.add_paragraph(f"• {clean_item}")
                        bp.paragraph_format.space_after = Pt(3)
                        bp.paragraph_format.left_indent = Inches(0.2)
            else:
                raw_text = str(content)
                from proposal_template_registry import resolve_image_path
                img_pattern = re.compile(r'(<img\s+[^>]*?>)', re.IGNORECASE)
                img_src_pattern = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
                parts = img_pattern.split(raw_text)

                for part in parts:
                    if not part or not part.strip():
                        continue
                    if part.lower().startswith("<img"):
                        src_m = img_src_pattern.search(part)
                        if src_m:
                            img_path = resolve_image_path(src_m.group(1))
                            if img_path:
                                try:
                                    img_p = doc.add_paragraph()
                                    img_p.alignment = docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
                                    run = img_p.add_run()
                                    run.add_picture(img_path, width=Inches(5.0))
                                except Exception as img_exc:
                                    app.logger.warning("Failed to embed image in DOCX: %s", img_exc)
                    else:
                        clean_block = part
                        clean_block = re.sub(r'<li[^>]*>', '• ', clean_block)
                        clean_block = re.sub(r'<br\s*/?>', '\n', clean_block)
                        clean_block = re.sub(r'</p>', '\n\n', clean_block)
                        clean_block = re.sub(r'<[^>]+>', '', clean_block)
                        for line in clean_block.strip().split("\n"):
                            l_str = line.strip()
                            if l_str:
                                p = doc.add_paragraph(l_str if not l_str.startswith("-") else f"• {l_str[1:].strip()}")
                                p.paragraph_format.space_after = Pt(4)
                                p.paragraph_format.line_spacing = 1.15
                                if l_str.startswith("•") or l_str.startswith("-"):
                                    p.paragraph_format.left_indent = Inches(0.2)


    # Acceptance Section for Alternative Design
    if is_alternative:
        doc.add_paragraph().paragraph_format.space_after = Pt(12)
        h_acc = doc.add_paragraph()
        r_acc = h_acc.add_run("PROPOSAL ACCEPTANCE & AUTHORIZATION")
        r_acc.bold = True
        r_acc.font.name = font_family
        r_acc.font.size = Pt(12)
        r_acc.font.color.rgb = header_color

        p_acc = doc.add_paragraph("By signing below, authorized representatives of both organizations accept this proposal.")
        p_acc.paragraph_format.space_after = Pt(8)

        sign_table = doc.add_table(rows=2, cols=2)
        c1 = sign_table.cell(0, 0).paragraphs[0]
        c1.add_run(f"Client Signature ({str(context.get('client') or 'Client')})\n\n___________________________")
        c2 = sign_table.cell(0, 1).paragraphs[0]
        c2.add_run(f"Vendor Signature ({curr_vendor})\n\n___________________________")
        c3 = sign_table.cell(1, 0).paragraphs[0]
        c3.add_run("Date: ____ / ____ / 2026")
        c4 = sign_table.cell(1, 1).paragraphs[0]
        c4.add_run("Date: ____ / ____ / 2026")

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _convert_docx_to_pdf(docx_bytes):
    """
    Convert rendered DOCX byte stream to PDF byte stream.
    Tries MS Word COM on Windows first, then LibreOffice CLI. Returns None if converter unavailable.
    """
    import tempfile
    import subprocess
    import sys
    import shutil

    temp_dir = Path(tempfile.mkdtemp(prefix="proposal_pdf_"))
    docx_path = temp_dir / "proposal.docx"
    pdf_path = temp_dir / "proposal.pdf"

    try:
        docx_path.write_bytes(docx_bytes)

        # 1. MS Word COM on Windows
        if sys.platform == "win32":
            try:
                import win32com.client
                import pythoncom
                pythoncom.CoInitialize()

                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0

                doc_obj = word.Documents.Open(str(docx_path.resolve()))
                doc_obj.SaveAs(str(pdf_path.resolve()), FileFormat=17) # 17 = wdFormatPDF
                doc_obj.Close(False)
                word.Quit()

                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    app.logger.info("[PDF CONVERTER] Successfully converted DOCX to PDF using MS Word COM.")
                    return pdf_path.read_bytes()
            except Exception as exc:
                app.logger.warning("[PDF CONVERTER] MS Word COM conversion failed: %s", exc)

        # 2. LibreOffice / soffice CLI fallback
        soffice_cmd = None
        for cmd_name in ["soffice", "libreoffice"]:
            if shutil.which(cmd_name):
                soffice_cmd = cmd_name
                break

        if soffice_cmd:
            try:
                subprocess.run(
                    [soffice_cmd, "--headless", "--convert-to", "pdf", str(docx_path), "--outdir", str(temp_dir)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=True,
                )
                if pdf_path.exists() and pdf_path.stat().st_size > 0:
                    app.logger.info("[PDF CONVERTER] Successfully converted DOCX to PDF using LibreOffice.")
                    return pdf_path.read_bytes()
            except Exception as exc:
                app.logger.warning("[PDF CONVERTER] LibreOffice conversion failed: %s", exc)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    app.logger.warning("[PDF CONVERTER] Conversion engines unavailable or failed.")
    return None


def _build_custom_pdf_template_proposal(context, pdf_template_path):
    """
    Render proposal document content onto a custom uploaded PDF template.
    Generates standard ReportLab document flowables, then overlays each page onto the uploaded PDF template pages.
    """
    try:
        import pypdf
    except (ImportError, ModuleNotFoundError):
        pypdf = None

    from proposal_template_registry import StandardProfessionalPdfRenderer
    content_pdf_bytes = StandardProfessionalPdfRenderer().render_pdf(context)

    if not pypdf or not pdf_template_path or not os.path.exists(pdf_template_path):
        return content_pdf_bytes

    try:
        tmpl_reader = pypdf.PdfReader(pdf_template_path)
        content_reader = pypdf.PdfReader(BytesIO(content_pdf_bytes))
        writer = pypdf.PdfWriter()

        tmpl_num_pages = len(tmpl_reader.pages)
        if tmpl_num_pages == 0:
            return content_pdf_bytes

        for i, c_page in enumerate(content_reader.pages):
            tmpl_idx = min(i, tmpl_num_pages - 1)
            t_page = tmpl_reader.pages[tmpl_idx]

            # Make a clean copy of the template page
            page_writer = pypdf.PdfWriter()
            page_writer.add_page(t_page)
            page_buf = BytesIO()
            page_writer.write(page_buf)
            page_buf.seek(0)
            t_page_copy = pypdf.PdfReader(page_buf).pages[0]

            # Merge proposal content onto template page
            t_page_copy.merge_page(c_page)
            writer.add_page(t_page_copy)

        out_buf = BytesIO()
        writer.write(out_buf)
        return out_buf.getvalue()
    except Exception as exc:
        app.logger.exception("[PROPOSAL PDF PIPELINE] PDF template merge failed: %s. Returning default proposal PDF.", exc)
        return content_pdf_bytes


def _build_proposal_pdf(context):
    """
    Render PDF proposal from selected template.
    If a custom uploaded DOCX or PDF template is selected, handles template-specific rendering.
    For built-in system templates (and fallback cases), executes the template-specific PDF renderer registered in template_registry.
    """
    start_time = time.time()
    tmpl_info = context.get("template") or {}
    tmpl_id = context.get("template_id") or tmpl_info.get("template_id") or DEFAULT_TEMPLATE_ID
    tmpl_name = tmpl_info.get("name") or "Standard Professional"
    tmpl_type = tmpl_info.get("type") or "built_in"
    file_path = tmpl_info.get("file_path") or ""

    app.logger.info(
        "[PROPOSAL PDF PIPELINE] Starting PDF generation | Proposal ID: %s | Template ID: %s | Template Name: '%s' | Type: %s | File Path: '%s'",
        context.get("code") or context.get("id") or "N/A",
        tmpl_id,
        tmpl_name,
        tmpl_type,
        file_path,
    )

    docx_bytes = None
    if tmpl_type == "custom" and file_path:
        if file_path.lower().endswith(".pdf"):
            try:
                pdf_bytes = _build_custom_pdf_template_proposal(context, file_path)
                duration = time.time() - start_time
                app.logger.info(
                    "[PROPOSAL PDF PIPELINE] PDF generated successfully from custom PDF template in %.2fs | Output size: %d bytes",
                    duration,
                    len(pdf_bytes),
                )
                return pdf_bytes
            except Exception as exc:
                app.logger.exception("[PROPOSAL PDF PIPELINE] Failed to generate PDF from custom PDF template: %s", exc)
        else:
            try:
                docx_bytes = _build_proposal_docx(context)
                pdf_bytes = _convert_docx_to_pdf(docx_bytes)
                if pdf_bytes:
                    duration = time.time() - start_time
                    app.logger.info(
                        "[PROPOSAL PDF PIPELINE] PDF generated successfully from custom DOCX template in %.2fs | Output size: %d bytes",
                        duration,
                        len(pdf_bytes),
                    )
                    return pdf_bytes
            except Exception as exc:
                app.logger.exception("[PROPOSAL PDF PIPELINE] Failed to generate PDF from custom DOCX template via COM/LibreOffice: %s", exc)

    # Resolve renderer from template registry
    renderer = template_registry.get_renderer(tmpl_id, file_path=tmpl_info.get("file_path"))
    ctx_with_docx = dict(context)
    if docx_bytes:
        ctx_with_docx["docx_bytes"] = docx_bytes

    pdf_bytes = renderer.render_pdf(ctx_with_docx)
    duration = time.time() - start_time
    app.logger.info(
        "[PROPOSAL PDF PIPELINE] PDF generated successfully using template renderer '%s' in %.2fs | Output size: %d bytes",
        renderer.name,
        duration,
        len(pdf_bytes),
    )
    return pdf_bytes



def _render_ai_grc_with_proposal(proposal, assessment, mode="modify", prompt="", proposal_inputs=None):
    if proposal and isinstance(proposal, dict):
        proposal = _normalize_proposal(proposal)
    user_id = session.get("user_id")
    available_templates = get_available_templates(user_id)
    inputs_obj = proposal_inputs or (proposal.get("_inputs") if (proposal and isinstance(proposal, dict)) else None) or _business_proposal_defaults(assessment)
    active_template_id = (
        (proposal and isinstance(proposal, dict) and proposal.get("template_id"))
        or (inputs_obj and inputs_obj.get("template_id"))
        or session.get("selected_template_id")
        or DEFAULT_TEMPLATE_ID
    )
    active_template = get_template_info(active_template_id, user_id)
    return render_template(
        "ai_grc.html",
        proposal=proposal,
        assessment=assessment,
        mode=mode,
        prompt=prompt,
        proposal_inputs=inputs_obj,
        available_templates=available_templates,
        active_template=active_template,
    )


@app.route("/grc/ai/proposal/download/<file_type>", methods=["GET", "POST"])
@login_required
def download_ai_proposal(file_type):
    start_time = time.time()
    assessment = get_latest_assessment()
    proposal = None

    try:
        proposal = _load_proposal_from_request()
        if assessment is None and not _is_business_proposal_payload(proposal):
            raise ValueError("No active assessment or proposal details were found.")

        context = _proposal_document_context(proposal, assessment)
        if file_type == "pdf":
            payload = _build_proposal_pdf(context)
            mimetype = "application/pdf"
            doc_type_name = "PDF"
        elif file_type == "docx":
            payload = _build_proposal_docx(context)
            mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            doc_type_name = "Word"
        else:
            raise ValueError(f"Unsupported document download format: '{file_type}'.")

        duration = time.time() - start_time
        client_name = context.get("proposal", {}).get("client") or context.get("client") or "Client"
        app.logger.info("[PROPOSAL] %s generation completed in %.2fs for client: %s", doc_type_name, duration, client_name)

        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=_proposal_filename(context, file_type),
            mimetype=mimetype,
        )
    except Exception as exc:
        app.logger.exception("Proposal download failed")

        is_ajax = (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.is_json
            or "json" in request.headers.get("Accept", "").lower()
            or request.headers.get("X-Fetch-Request") == "true"
        )

        if is_ajax:
            return jsonify({"success": False, "error": f"Proposal download failed: {str(exc)}"}), 400

        flash(f"Proposal download failed: {exc}", "error")
        return _render_ai_grc_with_proposal(
            proposal,
            assessment,
            mode="business_proposal" if _is_business_proposal_payload(proposal) else "modify",
            proposal_inputs=_business_proposal_defaults(assessment),
        ), 400


@app.route("/grc/ai/proposal/delete", methods=["POST"])
@login_required
def delete_ai_proposal():
    """Delete the active business proposal and reset workspace to clean State A."""
    assessment = get_latest_assessment()
    assessment_id = assessment["id"] if assessment else None

    if assessment_id:
        try:
            conn = get_db_connection()
            conn.execute("DELETE FROM business_proposals WHERE assessment_id = ?", (assessment_id,))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            app.logger.exception("Failed to delete business proposal")

    session.pop("active_proposal", None)
    session["ai_grc_new_mode"] = True
    flash("Proposal deleted successfully.", "success")
    return redirect(url_for("ai_grc_new"))


@app.route("/grc/ai/proposal/update", methods=["POST"])
@login_required
def update_ai_proposal():
    """Manually update business proposal content without invoking AI/Gemini."""
    try:
        data = request.get_json(silent=True)
        if not data:
            proposal_raw = request.form.get("proposal_json")
            if proposal_raw:
                try:
                    data = json.loads(proposal_raw, parse_int=_safe_json_parse_int)
                except json.JSONDecodeError:
                    data = None
        if not data:
            return jsonify({"success": False, "error": "No proposal data provided."}), 400

        proposal_payload = data.get("proposal") if isinstance(data, dict) and "proposal" in data else data
        if not isinstance(proposal_payload, dict):
            return jsonify({"success": False, "error": "Invalid proposal format."}), 400

        normalized = _normalize_proposal(proposal_payload)
        assessment = get_latest_assessment()
        assessment_id = assessment["id"] if assessment else None

        if assessment_id:
            inputs = normalized.get("_inputs") or {}
            save_business_proposal(assessment_id, normalized, inputs)
        else:
            session["active_proposal"] = normalized

        return jsonify({"success": True, "proposal": normalized}), 200
    except Exception as exc:
        app.logger.exception("Failed to update proposal manually")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/grc/ai/proposal/upload-image", methods=["POST"])
@login_required
def upload_proposal_image():
    """Upload an image for insertion into a proposal section."""
    try:
        image_file = request.files.get("image")
        if not image_file:
            data = request.get_json(silent=True) or {}
            b64_str = data.get("image_data") or ""
            if b64_str and "," in b64_str:
                header, encoded = b64_str.split(",", 1)
                file_bytes = base64.b64decode(encoded)
                ext = "png"
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                elif "gif" in header:
                    ext = "gif"
                elif "webp" in header:
                    ext = "webp"
                filename = f"img_{uuid.uuid4().hex[:12]}.{ext}"
                upload_dir = os.path.join(app.static_folder, "uploads", "proposal_images")
                os.makedirs(upload_dir, exist_ok=True)
                file_path = os.path.join(upload_dir, filename)
                with open(file_path, "wb") as f:
                    f.write(file_bytes)
                url = f"/static/uploads/proposal_images/{filename}"
                return jsonify({"success": True, "url": url}), 200
            return jsonify({"success": False, "error": "No image file or data provided."}), 400

        filename = secure_filename(image_file.filename or "")
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
            ext = "png"
        unique_filename = f"img_{uuid.uuid4().hex[:12]}.{ext}"
        upload_dir = os.path.join(app.static_folder, "uploads", "proposal_images")
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, unique_filename)
        image_file.save(file_path)
        url = f"/static/uploads/proposal_images/{unique_filename}"
        return jsonify({"success": True, "url": url}), 200
    except Exception as exc:
        app.logger.exception("Image upload failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/grc/ai/proposal/status", methods=["POST"])
@login_required
def update_proposal_status():
    """Update the status of a proposal record in SQLite DB (Approved, Rejected, or In Evaluation)."""
    user_id = session.get("user_id")
    data = request.get_json(silent=True) or {}

    assessment_id = data.get("assessment_id") or request.form.get("assessment_id")
    if not assessment_id:
        assessment = get_latest_assessment()
        assessment_id = assessment["id"] if assessment else None

    new_status = (data.get("status") or request.form.get("status") or "").strip()

    if not assessment_id:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "No proposal/assessment ID provided."}), 400
        flash("No proposal selected.", "error")
        return redirect(url_for("proposal_tracker"))

    # Normalize canonical status
    if new_status.lower() in ("approved", "accept", "accepted"):
        norm_status = "Approved"
    elif new_status.lower() in ("rejected", "reject", "declined"):
        norm_status = "Rejected"
    elif new_status.lower() in ("submitted", "send", "sent", "resubmit", "resubmitted"):
        norm_status = "Submitted"
    elif new_status.lower() in ("in evaluation", "evaluation", "review", "keep under review", "under review", "in progress"):
        norm_status = "In Evaluation"
    else:
        norm_status = "In Evaluation"

    conn = get_db_connection()
    assessment = conn.execute(
        "SELECT id FROM assessments WHERE id = ? AND user_id = ?",
        (assessment_id, user_id),
    ).fetchone()

    if not assessment:
        conn.close()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Proposal not found or unauthorized."}), 404
        flash("Proposal not found.", "error")
        return redirect(url_for("proposal_tracker"))

    conn.execute(
        "UPDATE assessments SET status = ? WHERE id = ?",
        (norm_status, assessment_id),
    )
    if norm_status == "Submitted":
        conn.execute(
            "UPDATE business_proposals SET submitted_at = CURRENT_TIMESTAMP WHERE assessment_id = ?",
            (assessment_id,),
        )
    conn.commit()
    conn.close()

    if isinstance(session.get("active_proposal"), dict):
        session["active_proposal"]["status"] = norm_status
        session.modified = True

    message = f"Proposal status updated to {norm_status}."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "assessment_id": assessment_id,
            "status": norm_status,
            "message": message,
        }), 200

    flash(message, "success")
    return redirect(url_for("ai_grc"))


@app.route("/grc/ai/proposal/edit-section", methods=["POST"])
@login_required
def edit_section_ai_proposal():
    """Rewrite a single proposal section using Gemini AI based on user instruction."""
    try:
        data = request.get_json(silent=True) or {}
        section_id = str(data.get("section_id") or "").strip()
        section_title = str(data.get("section_title") or "").strip()
        current_content = str(data.get("current_content") or "").strip()
        user_instruction = str(data.get("user_instruction") or "").strip()
        proposal_context = data.get("proposal_context") or {}

        if not section_title:
            return jsonify({"success": False, "error": "Section title is required."}), 400
        if not current_content:
            return jsonify({"success": False, "error": "Current section content is required."}), 400
        if not user_instruction:
            return jsonify({"success": False, "error": "Please provide an instruction for the AI rewrite."}), 400

        client_name = str(proposal_context.get("client") or "Client").strip()
        project_type = str(proposal_context.get("project_type") or "Cybersecurity Assessment").strip()
        curr_context = str(proposal_context.get("currency") or "INR").strip().upper()

        if section_id == "section-13" or section_title.upper() == "COMMERCIALS" or "'amount':" in current_content or '"amount":' in current_content or current_content.strip().startswith("{"):
            normalized_html = normalize_commercials_content(current_content, curr_context)
            clean_prompt_content = re.sub(r'<br\s*/?>', '\n', normalized_html)
            clean_prompt_content = re.sub(r'</p>', '\n\n', clean_prompt_content)
            clean_prompt_content = re.sub(r'<[^>]+>', '', clean_prompt_content).strip()
            current_content = clean_prompt_content

        prompt_text = f"""You are an expert enterprise business and technology consultant editing ONE section of a formal business proposal.

Proposal Context:
- Client Organization: {client_name}
- Project Scope: {project_type}

Section Name: {section_title}

Current Section Content:
\"\"\"
{current_content}
\"\"\"

User Instruction / Desired Changes:
\"\"\"
{user_instruction}
\"\"\"

Strict Requirements:
1. Rewrite ONLY the content of section '{section_title}'.
2. Follow the user instruction precisely (e.g. adjust tone, conciseness, ROI emphasis, or specific technical details).
3. Do NOT include section title headers or section numbers in your response (do NOT add '{section_title}:' or '# {section_title}'). Return ONLY the section text content itself.
4. Maintain factual consistency. Do not invent false credentials, bogus pricing, or unsupported client details.
5. Use clear, professional enterprise business language.
6. If the current section is formatted as bullet points or list items, preserve clean line breaks or bullet formatting.
"""

        app.logger.info("[PROPOSAL] Section AI edit requested for '%s' (instruction: '%s')", section_title, user_instruction[:50])

        client_obj = _create_gemini_client()
        req_http_options = types.HttpOptions(
            client_args={"trust_env": False},
            async_client_args={"trust_env": False},
            timeout=GEMINI_REQUEST_TIMEOUT_MS,
        )
        gen_config = types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(thinking_budget=512),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=req_http_options,
        )

        gemini_res = client_obj.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt_text,
            config=gen_config,
        )

        rewritten = (gemini_res.text or "").strip()
        if not rewritten:
            return jsonify({"success": False, "error": "AI service returned empty response."}), 500

        clean_lines = []
        for line in rewritten.split("\n"):
            line_str = line.strip()
            if line_str.startswith("#") and section_title.lower() in line_str.lower():
                continue
            if line_str.lower() == section_title.lower() or line_str.lower() == f"{section_title.lower()}:":
                continue
            clean_lines.append(line)

        clean_rewritten = "\n".join(clean_lines).strip()

        return jsonify({
            "success": True,
            "section_id": section_id,
            "section_title": section_title,
            "rewritten_content": clean_rewritten or rewritten,
        }), 200

    except (APITimeoutError, TimeoutError, httpx.TimeoutException) as exc:
        app.logger.error("Section AI edit timed out: %s", exc)
        return jsonify({"success": False, "error": "AI service timed out. Please try again."}), 504
    except Exception as exc:
        app.logger.exception("Section AI edit failed")
        return jsonify({"success": False, "error": f"AI rewrite failed: {str(exc)}"}), 500




@app.route("/grc/ai/new", methods=["GET", "POST"])
@login_required
def ai_grc_new():
    """Dedicated entry point for generating a brand-new proposal."""
    session["ai_grc_new_mode"] = True
    session.pop("assessment_id", None)
    template_id = request.args.get("template_id") or request.form.get("template_id")
    if template_id:
        session["selected_template_id"] = template_id
    elif not session.get("selected_template_id"):
        session["selected_template_id"] = DEFAULT_TEMPLATE_ID

    active_id = session.get("selected_template_id", DEFAULT_TEMPLATE_ID)
    return redirect(url_for("ai_grc", ai_mode="business_proposal", template_id=active_id))


@app.route("/ai-proposals/templates", methods=["GET"])
@app.route("/grc/ai/templates-library", methods=["GET"])
@login_required
def templates_library_view():
    """Render the Templates Library page with All Templates and Templates I've Used."""
    user_id = session.get("user_id")
    templates = get_available_templates(user_id)
    usage_data = get_templates_usage_data(user_id)

    all_templates = []
    used_templates = []

    for tmpl in templates:
        t_id = tmpl["template_id"]
        tmpl_usage = usage_data.get(t_id, {})
        tmpl["usage_count"] = tmpl_usage.get("count", 0)
        tmpl["last_used"] = tmpl_usage.get("last_used")
        tmpl["used_by_user"] = tmpl["usage_count"] > 0
        all_templates.append(tmpl)
        if tmpl["used_by_user"]:
            used_templates.append(tmpl)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return jsonify({
            "success": True,
            "all_templates": all_templates,
            "used_templates": used_templates,
            "templates": all_templates,
        }), 200

    return render_template(
        "templates_library.html",
        all_templates=all_templates,
        used_templates=used_templates,
        active_tab=request.args.get("tab", "all"),
        is_templates_view=True,
    )


@app.route("/grc/ai/templates", methods=["GET"])
@login_required
def list_proposal_templates():
    user_id = session.get("user_id")
    templates = get_available_templates(user_id)
    return jsonify({"success": True, "templates": templates}), 200


@app.route("/grc/ai/templates/upload", methods=["POST"])
@login_required
def upload_proposal_template():
    user_id = session.get("user_id")
    template_file = request.files.get("template_file")
    template_name = request.form.get("template_name")
    try:
        res = save_custom_template(user_id, template_file, template_name)
        session["selected_template_id"] = res["template_id"]
        return jsonify({"success": True, "template": res}), 200
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Template upload failed")
        return jsonify({"success": False, "error": f"Template upload failed: {str(exc)}"}), 500


@app.route("/grc/ai/templates/select", methods=["POST"])
@login_required
def select_proposal_template():
    data = request.get_json(silent=True) or {}
    template_id = str(data.get("template_id") or request.form.get("template_id") or "").strip()
    if not template_id:
        return jsonify({"success": False, "error": "Template ID is required."}), 400

    user_id = session.get("user_id")
    template_info = get_template_info(template_id, user_id)
    session["selected_template_id"] = template_info["template_id"]

    return jsonify({"success": True, "template": template_info}), 200


@app.route("/grc/ai/templates/delete", methods=["POST"])
@app.route("/grc/ai/templates/delete/<template_id>", methods=["POST", "DELETE"])
@login_required
def delete_proposal_template(template_id=None):
    """Delete a custom template safely from the library and DB."""
    if not template_id:
        data = request.get_json(silent=True) or {}
        template_id = str(data.get("template_id") or request.form.get("template_id") or "").strip()

    if not template_id:
        return jsonify({"success": False, "error": "Template ID is required."}), 400

    clean_id = str(template_id).strip()

    # Built-in system templates protection
    system_template_ids = {t["template_id"].lower() for t in template_registry.list_templates()}
    system_template_ids.update([
        DEFAULT_TEMPLATE_ID.lower(),
        "standard-professional",
        "standard_professional",
        "whitehats-professional",
        "whitehats_professional",
        "business_proposal_test_template",
        "test_template",
        "test_template_corporate",
        "business_proposal_alternative_design",
        "alternative_design",
        "editorial_design",
    ])
    if clean_id.lower() in system_template_ids:
        return jsonify({"success": False, "error": "Default templates cannot be deleted."}), 400

    user_id = session.get("user_id")
    conn = get_db_connection()
    row = conn.execute(
        "SELECT template_id, name, type FROM proposal_templates WHERE template_id = ?",
        (clean_id,),
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Template not found or default templates cannot be deleted."}), 404

    r_dict = dict(row)
    if r_dict.get("type") == "system" or str(r_dict.get("template_id")).lower() in system_template_ids:
        conn.close()
        return jsonify({"success": False, "error": "Default templates cannot be deleted."}), 400

    conn.execute(
        "UPDATE proposal_templates SET is_deleted = 1 WHERE template_id = ?",
        (clean_id,),
    )
    conn.commit()
    conn.close()

    if session.get("selected_template_id") == clean_id:
        session.pop("selected_template_id", None)

    return jsonify({"success": True, "template_id": clean_id, "message": "Template deleted successfully."}), 200


@app.route("/grc/ai/proposal/create-assessment", methods=["POST"])
@login_required
def create_assessment_from_proposal():
    """Create a new GRC assessment record from a reviewed AI business proposal."""
    proposal_json_raw = request.form.get("proposal_json", "")
    if not proposal_json_raw:
        flash("No business proposal provided for assessment creation.", "error")
        return redirect(url_for("ai_grc", ai_mode="business_proposal"))

    try:
        proposal = json.loads(proposal_json_raw, parse_int=_safe_json_parse_int)
    except json.JSONDecodeError:
        flash("Invalid proposal data format.", "error")
        return redirect(url_for("ai_grc", ai_mode="business_proposal"))

    valid, errors = validate_business_proposal(proposal)
    if not valid:
        flash(f"Cannot create assessment from invalid proposal: {'; '.join(errors)}", "error")
        return redirect(url_for("ai_grc", ai_mode="business_proposal"))

    proposal_inputs = proposal.get("_inputs", {})
    client_name = str(proposal.get("client") or proposal_inputs.get("client") or "New Client").strip()
    project_type = str(proposal.get("project_type") or proposal_inputs.get("project_type") or "Cybersecurity Assessment").strip()

    budget_raw = str(proposal.get("pricing", {}).get("amount") or proposal_inputs.get("budget") or "0")
    pricing_num = re.sub(r"[^\d.]", "", budget_raw) or "0"

    today_str = datetime.now().strftime("%Y-%m-%d")
    end_date_str = (datetime.now() + timedelta(days=56)).strftime("%Y-%m-%d")

    try:
        customer_id = create_customer(client_name, "Technology", "100-500")
        assessment_id = create_assessment(
            customer_id=customer_id,
            assessment_type=project_type,
            framework="ISO 27001",
            start_date=today_str,
            end_date=end_date_str,
            pricing=pricing_num,
            status="In Progress",
            user_id=session["user_id"],
        )
        save_business_proposal(assessment_id, proposal, proposal_inputs)
        session["assessment_id"] = assessment_id

        log_audit(
            "Assessment creation",
            "assessment",
            assessment_id,
            f"Created assessment from business proposal for {client_name}.",
            assessment_id,
        )
        flash("Assessment created and business proposal saved successfully.", "success")
        return redirect(url_for("engagement_overview"))
    except Exception as exc:
        app.logger.exception("Failed to create assessment from proposal")
        flash(f"Failed to create assessment: {exc}", "error")
        return redirect(url_for("ai_grc", ai_mode="business_proposal"))


@app.route("/grc/ai", methods=["GET", "POST"])
@login_required
def ai_grc():
    req_template_id = request.args.get("template_id") or request.form.get("template_id")
    if req_template_id:
        session["selected_template_id"] = req_template_id.strip()

    force_new_generation = session.pop("ai_grc_new_mode", False)
    assessment = None if force_new_generation else get_latest_assessment()
    assessment_id = assessment["id"] if assessment else None
    proposal = None
    proposal_inputs = _business_proposal_defaults(assessment)
    mode = "business_proposal"

    if request.method == "POST":
        prompt = request.form.get("request_text", "").strip()
        mode = "business_proposal"
        proposal_inputs = _business_proposal_defaults(assessment, request.form)

        client_name = proposal_inputs["client"]
        project_type = proposal_inputs["project_type"]
        deliverables = proposal_inputs["deliverables"]
        timeline_text = proposal_inputs["timeline"]
        budget_text = proposal_inputs["budget"]
        additional_context = proposal_inputs["additional_context"]

        validation_errors = []
        if not client_name:
            validation_errors.append("Client/customer name is required for business proposal generation.")
        if not project_type:
            validation_errors.append("Project type is required for business proposal generation.")
        if not any([deliverables, timeline_text, budget_text, additional_context]):
            validation_errors.append("Provide deliverables, timeline, budget, or additional context to generate a proposal.")
        if timeline_text and not _valid_timeline_text(timeline_text):
            validation_errors.append("Timeline looks invalid. Provide a clear timeline such as '6 weeks' or phased schedule.")
        if budget_text and not _valid_budget_text(budget_text):
            validation_errors.append("Budget looks invalid. Provide a numeric amount such as 250000 or ₹2,50,000.")

        if validation_errors:
            for message in validation_errors:
                flash(message, "error")
            return render_template(
                "ai_grc.html",
                proposal=None,
                assessment=assessment,
                mode=mode,
                prompt=prompt,
                proposal_inputs=proposal_inputs,
            )

        status_code = 200
        try:
            proposal = generate_business_proposal(
                client=client_name,
                project_type=project_type,
                deliverables=deliverables,
                timeline=timeline_text,
                budget=budget_text,
                additional_context=additional_context,
                currency=proposal_inputs.get("currency", "INR"),
            )
            proposal["project_type"] = project_type
            proposal["_inputs"] = proposal_inputs

            if not assessment_id:
                budget_raw = str(proposal_inputs.get("budget") or "0")
                pricing_num = re.sub(r"[^\d.]", "", budget_raw) or "0"
                today_str = datetime.now().strftime("%Y-%m-%d")
                end_date_str = (datetime.now() + timedelta(days=56)).strftime("%Y-%m-%d")

                customer_id = create_customer(client_name, "Technology", "100-500")
                assessment_id = create_assessment(
                    customer_id=customer_id,
                    assessment_type=project_type,
                    framework="ISO 27001",
                    start_date=today_str,
                    end_date=end_date_str,
                    pricing=pricing_num,
                    status="In Evaluation",
                    user_id=session["user_id"],
                )
                session["assessment_id"] = assessment_id

            proposal = _normalize_proposal(proposal)
            save_business_proposal(assessment_id, proposal, proposal_inputs)

            assessment = get_assessment_for_user(assessment_id)
            year = datetime.now().strftime("%Y")
            proposal["assessment_id"] = assessment_id
            proposal["id"] = assessment_id
            proposal["code"] = f"PROP-{year}-{assessment_id}"
            current_st = (dict(assessment).get("status") if assessment else "In Evaluation") or "In Evaluation"
            if current_st in ("In Progress", "Draft", "New"):
                current_st = "In Evaluation"
            proposal["status"] = current_st

            flash("Business proposal generated successfully. Your proposal has been saved and is currently under evaluation.", "success")
        except (APITimeoutError, TimeoutError, httpx.TimeoutException) as exc:
            proposal = None
            status_code = 504
            app.logger.error("AI proposal generation timed out for client %s: %s", client_name, exc)
            flash("Proposal generation could not be completed because AI business proposal generation timed out. Your entered proposal information has been preserved. Please click 'Generate Business Proposal' again.", "error")
        except Exception as exc:
            proposal = None
            if "timeout" in str(exc).lower() or "504" in str(exc) or "deadline" in str(exc).lower():
                status_code = 504
                app.logger.error("AI proposal generation timed out for client %s: %s", client_name, exc)
                flash("Proposal generation could not be completed because AI business proposal generation timed out. Your entered proposal information has been preserved. Please click 'Generate Business Proposal' again.", "error")
            else:
                status_code = 500
                app.logger.exception("Business proposal generation failed")
                flash(f"Business proposal generation failed: {exc}", "error")

        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.headers.get("Accept") == "application/json":
            if proposal:
                return jsonify({"success": True, "proposal": proposal}), 200
            return jsonify({
                "error": "AI proposal generation timed out" if status_code == 504 else "AI proposal generation failed",
                "message": "The AI service took too long to respond. Please try again." if status_code == 504 else "Proposal generation failed."
            }), status_code

        return render_template(
            "ai_grc.html",
            proposal=proposal,
            assessment=assessment,
            mode=mode,
            prompt=prompt,
            proposal_inputs=proposal_inputs,
        ), status_code

    if request.method != "POST":
        mode = "business_proposal"
        if not force_new_generation and proposal is None:
            if assessment is not None:
                proposal = get_business_proposal(assessment["id"])
                if proposal is None:
                    proposal = create_default_proposal_for_assessment(assessment)
            else:
                proposal = session.get("active_proposal")

    if proposal and isinstance(proposal, dict) and proposal.get("_inputs"):
        proposal_inputs = proposal["_inputs"]

    user_id = session.get("user_id")
    available_templates = get_available_templates(user_id)
    active_template_id = (
        (proposal and isinstance(proposal, dict) and proposal.get("template_id"))
        or (proposal_inputs and proposal_inputs.get("template_id"))
        or session.get("selected_template_id")
        or DEFAULT_TEMPLATE_ID
    )
    active_template = get_template_info(active_template_id, user_id)

    return render_template(
        "ai_grc.html",
        proposal=proposal,
        assessment=assessment,
        mode=mode,
        prompt=request.form.get("request_text", "") if request.method == "POST" else "",
        proposal_inputs=proposal_inputs,
        available_templates=available_templates,
        active_template=active_template,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
