from flatline_l1_writer import (
    close_session,
    get_open_contradictions,
    resolve_contradiction,
)
from flatline_l1_lifecycle import transition, mark_gap, promote_to_active
from flatline_l2_promote import promote_session
import sqlite3


def _get_flag_obs_ids(db_path, flag_id):
    """Return (observation_a_id, observation_b_id) for a contradiction flag."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT observation_a_id, observation_b_id
               FROM contradiction_flags WHERE id = ?""",
            (flag_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Contradiction flag '{flag_id}' not found")
        return row[0], row[1]
    finally:
        conn.close()


def _to_gap(db_path, obs_id):
    """Transition an observation to GAP, promoting from CANDIDATE if needed."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Observation '{obs_id}' not found")
        if row[0] == 'CANDIDATE':
            promote_to_active(db_path, obs_id)
    finally:
        conn.close()
    mark_gap(db_path, obs_id)


def preflight_check(db_path, session_id):
    return get_open_contradictions(db_path, session_id)


def sign_out(db_path, session_id, annotation=None, force=False):
    conflicts = preflight_check(db_path, session_id)
    unresolved = len(conflicts)

    if conflicts and not force:
        return {"status": "BLOCKED", "conflicts": conflicts}

    close_session(db_path, session_id, annotation)
    promo = promote_session(db_path, session_id)
    return {
        "status": "CLOSED",
        "session_id": session_id,
        "conflicts_unresolved": unresolved,
        "promoted": promo["promoted"],
        "failed": promo["failed"],
    }


def still_broken(db_path, obs_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT id FROM contradiction_flags
               WHERE session_id = (SELECT session_id FROM observations WHERE id = ?)
               AND (observation_a_id = ? OR observation_b_id = ?)
               AND verdict IS NULL
               LIMIT 1""",
            (obs_id, obs_id, obs_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"No open contradiction found for observation '{obs_id}'"
            )
        flag_id = row[0]
    finally:
        conn.close()

    resolve_contradiction(db_path, flag_id, 'NEITHER')
    obs_a, obs_b = _get_flag_obs_ids(db_path, flag_id)
    _to_gap(db_path, obs_a)
    _to_gap(db_path, obs_b)


def neither_worked(db_path, flag_id):
    resolve_contradiction(db_path, flag_id, 'NEITHER')
    obs_a, obs_b = _get_flag_obs_ids(db_path, flag_id)
    _to_gap(db_path, obs_a)
    _to_gap(db_path, obs_b)


def resolve_a_wins(db_path, flag_id):
    return resolve_contradiction(db_path, flag_id, 'A_WINS')


def resolve_b_wins(db_path, flag_id):
    return resolve_contradiction(db_path, flag_id, 'B_WINS')
