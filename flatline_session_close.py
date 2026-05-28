import argparse
import json
import sqlite3
import subprocess
import sys
import time

from flatline_crystallizer import crystallize_session
from flatline_l1_session import sign_out, get_open_contradictions

QWEN_SERVICE = "llama-qwen"
CRYSTALLIZER_SERVICE = "llama-crystallizer"
SERVICE_START_WAIT = 30


def service_stop(name):
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if "not found" in stderr or "not loaded" in stderr or "inactive" in stderr:
            return
        raise RuntimeError(f"Failed to stop {name}: {stderr}")


def service_start(name):
    try:
        subprocess.run(
            ["systemctl", "--user", "start", name],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to start {name}: {e.stderr}")
    time.sleep(SERVICE_START_WAIT)


def poweroff():
    try:
        subprocess.run(
            ["systemctl", "poweroff"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to poweroff: {e.stderr}")


def run_crystallization(db_path, neo4j_session, session_id, user_annotation=None, dry_run=False):
    if not dry_run:
        service_stop(QWEN_SERVICE)
        service_start(CRYSTALLIZER_SERVICE)
    result = crystallize_session(db_path, neo4j_session, session_id, user_annotation)
    if not dry_run:
        service_stop(CRYSTALLIZER_SERVICE)
    return result


def signing_out(db_path, neo4j_session, session_id, user_annotation=None, dry_run=False):
    conflicts = get_open_contradictions(db_path, session_id)
    if conflicts:
        raise RuntimeError("Unresolved contradictions — resolve before signing out")
    result = sign_out(db_path, session_id, user_annotation)
    if dry_run and neo4j_session is None:
        return result
    result = run_crystallization(db_path, neo4j_session, session_id, user_annotation, dry_run=dry_run)
    if not dry_run:
        service_start(QWEN_SERVICE)
    return result


def signing_off(db_path, neo4j_session, session_id, user_annotation=None):
    conflicts = get_open_contradictions(db_path, session_id)
    if conflicts:
        raise RuntimeError("Unresolved contradictions — resolve before signing out")
    sign_out(db_path, session_id, user_annotation)
    run_crystallization(db_path, neo4j_session, session_id, user_annotation, dry_run=False)
    poweroff()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Session sign-out")
    parser.add_argument("session_id", help="UUID of the session to sign out")
    parser.add_argument("-d", "--db", default="flatline.db", help="Path to the SQLite database")
    parser.add_argument("-a", "--annotation", default=None, help="Optional annotation")
    args = parser.parse_args()

    db_path = args.db
    session_id = args.session_id

    # Ensure schema is applied if DB has no tables
    conn = sqlite3.connect(db_path)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    conn.close()
    if not tables:
        schema_path = "flatline_l1_schema.sql"
        with open(schema_path) as f:
            schema = f.read()
        conn = sqlite3.connect(db_path)
        conn.executescript(schema)
        conn.close()

    # Verify session exists and is open
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM sessions WHERE id = ? AND status = 'OPEN'",
        (session_id,),
    ).fetchone()
    conn.close()
    if row is None:
        print(json.dumps({"error": f"Session '{session_id}' not found or not OPEN"}))
        sys.exit(1)

    try:
        result = signing_out(db_path, None, session_id, user_annotation=args.annotation, dry_run=True)
        print(json.dumps(result, indent=2))
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
