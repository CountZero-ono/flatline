import sqlite3
import uuid
import time

VALID_DECAY_CLASSES = {'ARCHITECTURAL', 'OPERATIONAL', 'TRANSIENT', 'PERSONAL'}
VALID_VERDICTS = {'A_WINS', 'B_WINS', 'NEITHER', 'DEFERRED'}


def create_session(db_path):
    session_id = str(uuid.uuid4())
    started_at = int(time.time())
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (id, started_at, status) VALUES (?, ?, 'OPEN')",
            (session_id, started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return session_id


def write_observation(db_path, session_id, content, decay_class):
    if decay_class not in VALID_DECAY_CLASSES:
        raise ValueError(
            f"Invalid decay_class '{decay_class}'. Must be one of {VALID_DECAY_CLASSES}"
        )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Session '{session_id}' not found")
        if row[0] != 'OPEN':
            raise ValueError(
                f"Session '{session_id}' is not OPEN (status: {row[0]})"
            )

        obs_id = str(uuid.uuid4())
        recorded_at = int(time.time())
        conn.execute(
            """INSERT INTO observations
               (id, session_id, content, recorded_at, decay_class, decay_score,
                status, source_type, contradiction_flag, promoted_at)
               VALUES (?, ?, ?, ?, ?, 1.0, 'CANDIDATE', 'SESSION', NULL, NULL)""",
            (obs_id, session_id, content, recorded_at, decay_class),
        )
        conn.commit()
    finally:
        conn.close()
    return obs_id


def close_session(db_path, session_id, annotation=None):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Session '{session_id}' not found")
        if row[0] != 'OPEN':
            raise ValueError(
                f"Session '{session_id}' is not OPEN (status: {row[0]})"
            )

        ended_at = int(time.time())
        conn.execute(
            """UPDATE sessions
               SET status = 'CLOSED', ended_at = ?, user_annotation = ?
               WHERE id = ?""",
            (ended_at, annotation, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def flag_contradiction(db_path, session_id, obs_a_id, obs_b_id, description):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id FROM observations
               WHERE id IN (?, ?) AND session_id = ?""",
            (obs_a_id, obs_b_id, session_id),
        ).fetchall()

        found = {r[0] for r in rows}
        for oid in (obs_a_id, obs_b_id):
            if oid not in found:
                raise ValueError(
                    f"Observation '{oid}' not found or does not belong to session '{session_id}'"
                )

        flag_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO contradiction_flags
               (id, session_id, observation_a_id, observation_b_id, description)
               VALUES (?, ?, ?, ?, ?)""",
            (flag_id, session_id, obs_a_id, obs_b_id, description),
        )
        conn.commit()
    finally:
        conn.close()
    return flag_id


def get_open_contradictions(db_path, session_id):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """SELECT id, observation_a_id, observation_b_id, description
               FROM contradiction_flags
               WHERE session_id = ? AND verdict IS NULL""",
            (session_id,),
        )
        return [
            {
                "id": row[0],
                "observation_a_id": row[1],
                "observation_b_id": row[2],
                "description": row[3],
            }
            for row in cursor
        ]
    finally:
        conn.close()


def resolve_contradiction(db_path, flag_id, verdict):
    if verdict not in VALID_VERDICTS:
        raise ValueError(
            f"Invalid verdict '{verdict}'. Must be one of {VALID_VERDICTS}"
        )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT verdict FROM contradiction_flags WHERE id = ?", (flag_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Contradiction flag '{flag_id}' not found")
        if row[0] is not None:
            raise ValueError(
                f"Contradiction flag '{flag_id}' is already resolved with verdict '{row[0]}'"
            )

        resolved_at = int(time.time())
        conn.execute(
            "UPDATE contradiction_flags SET verdict = ?, resolved_at = ? WHERE id = ?",
            (verdict, resolved_at, flag_id),
        )
        conn.commit()
    finally:
        conn.close()
