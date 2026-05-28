import sqlite3
import time
import logging
import requests

logger = logging.getLogger(__name__)

MEMMACHINE_URL = "http://192.168.1.53:8080/api/v2/memories"
MEMMACHINE_HEADERS = {"user-id": "fb", "Content-Type": "application/json"}


def get_unpromoted(db_path, session_id):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """SELECT id, content, decay_class, status
               FROM observations
               WHERE session_id = ?
                 AND status IN ('ACTIVE', 'VALIDATED')
                 AND promoted_at IS NULL""",
            (session_id,),
        )
        return [
            {
                "id": row[0],
                "content": row[1],
                "decay_class": row[2],
                "status": row[3],
            }
            for row in cursor
        ]
    finally:
        conn.close()


def promote_session(db_path, session_id):
    unpromoted = get_unpromoted(db_path, session_id)

    promoted = 0
    failed = 0

    for obs in unpromoted:
        payload = {
            "org_id": "mcp-universal",
            "project_id": "mcp-fb",
            "messages": [
                {
                    "content": obs["content"],
                    "producer": "flatline",
                    "produced_for": "fb",
                    "metadata": {
                        "decay_class": obs["decay_class"],
                        "observation_id": obs["id"],
                        "session_id": session_id,
                    },
                }
            ]
        }

        try:
            resp = requests.post(
                MEMMACHINE_URL,
                headers=MEMMACHINE_HEADERS,
                json=payload,
            )
            if resp.status_code in (200, 201):
                now = int(time.time())
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute(
                        "UPDATE observations SET promoted_at = ? WHERE id = ?",
                        (now, obs["id"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                promoted += 1
            else:
                logger.error(
                    "MemMachine POST failed for obs %s: HTTP %s",
                    obs["id"],
                    resp.status_code,
                )
                failed += 1
        except Exception as exc:
            logger.error(
                "MemMachine POST error for obs %s: %s",
                obs["id"],
                exc,
            )
            failed += 1

    return {"promoted": promoted, "failed": failed}
