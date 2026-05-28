import sqlite3

VALID_STATES = {
    'CANDIDATE', 'ACTIVE', 'VALIDATED', 'GAP',
    'INVALIDATED', 'SUPERSEDED', 'DECAYED',
}

TRANSITIONS = {
    'CANDIDATE': {'ACTIVE', 'INVALIDATED'},
    'ACTIVE': {'VALIDATED', 'INVALIDATED', 'SUPERSEDED', 'DECAYED', 'GAP'},
    'VALIDATED': {'INVALIDATED', 'SUPERSEDED', 'DECAYED'},
    'GAP': {'ACTIVE'},
    'INVALIDATED': set(),
    'SUPERSEDED': set(),
    'DECAYED': set(),
}


def transition(db_path, obs_id, target_status, reason=None):
    if target_status not in VALID_STATES:
        raise ValueError(
            f"Invalid status '{target_status}'. Must be one of {VALID_STATES}"
        )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Observation '{obs_id}' not found")

        current = row[0]
        allowed = TRANSITIONS.get(current, set())
        if target_status not in allowed:
            raise ValueError(
                f"Cannot transition observation '{obs_id}' from '{current}' to '{target_status}'"
            )

        conn.execute(
            "UPDATE observations SET status = ? WHERE id = ?",
            (target_status, obs_id),
        )
        conn.commit()
    finally:
        conn.close()


def promote_to_active(db_path, obs_id):
    transition(db_path, obs_id, 'ACTIVE')


def mark_gap(db_path, obs_id):
    transition(db_path, obs_id, 'GAP')


def close_gap(db_path, obs_id):
    transition(db_path, obs_id, 'ACTIVE')


def decay_observation(db_path, obs_id, new_score):
    if new_score < 0.0 or new_score > 1.0:
        raise ValueError(
            f"decay_score must be between 0.0 and 1.0, got {new_score}"
        )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Observation '{obs_id}' not found")

        conn.execute(
            "UPDATE observations SET decay_score = ? WHERE id = ?",
            (new_score, obs_id),
        )

        if new_score <= 0.1 and row[0] in TRANSITIONS and 'DECAYED' in TRANSITIONS[row[0]]:
            conn.execute(
                "UPDATE observations SET status = 'DECAYED' WHERE id = ?",
                (obs_id,),
            )

        conn.commit()
    finally:
        conn.close()


def get_observations_by_status(db_path, session_id, status):
    if status not in VALID_STATES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of {VALID_STATES}"
        )

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """SELECT id, content, decay_class, decay_score, recorded_at
               FROM observations
               WHERE session_id = ? AND status = ?""",
            (session_id, status),
        )
        return [
            {
                "id": row[0],
                "content": row[1],
                "decay_class": row[2],
                "decay_score": row[3],
                "recorded_at": row[4],
            }
            for row in cursor
        ]
    finally:
        conn.close()
