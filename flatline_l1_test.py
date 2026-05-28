import unittest
import tempfile
import sqlite3
import os

from flatline_l1_writer import (
    create_session, write_observation, close_session,
    flag_contradiction, get_open_contradictions, resolve_contradiction,
)
from flatline_l1_lifecycle import (
    transition, promote_to_active, mark_gap, close_gap,
    decay_observation, get_observations_by_status,
)
from flatline_l1_session import preflight_check, sign_out, still_broken, neither_worked
from flatline_l2_promote import promote_session, get_unpromoted
from unittest.mock import patch, MagicMock


class TestFlatlineL1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        with open(os.path.join(os.path.dirname(__file__), 'flatline_l1_schema.sql')) as f:
            schema = f.read()
        conn.executescript(schema)
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def test_create_session(self):
        sid = create_session(self.db_path)
        conn = self._conn()
        row = conn.execute("SELECT status FROM sessions WHERE id = ?", (sid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'OPEN')

    def test_write_observation(self):
        sid = create_session(self.db_path)
        oid = write_observation(self.db_path, sid, 'test content', 'ARCHITECTURAL')
        conn = self._conn()
        row = conn.execute("SELECT decay_class, status, decay_score FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'ARCHITECTURAL')
        self.assertEqual(row[1], 'CANDIDATE')
        self.assertEqual(row[2], 1.0)

    def test_invalid_decay_class(self):
        sid = create_session(self.db_path)
        with self.assertRaises(ValueError):
            write_observation(self.db_path, sid, 'bad', 'INVALID_CLASS')

    def test_lifecycle_transitions(self):
        sid = create_session(self.db_path)
        oid = write_observation(self.db_path, sid, 'test', 'OPERATIONAL')
        promote_to_active(self.db_path, oid)
        conn = self._conn()
        row = conn.execute("SELECT status FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'ACTIVE')

        transition(self.db_path, oid, 'VALIDATED')
        conn = self._conn()
        row = conn.execute("SELECT status FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'VALIDATED')

        with self.assertRaises(ValueError):
            transition(self.db_path, oid, 'CANDIDATE')

    def test_decay_auto_transition(self):
        sid = create_session(self.db_path)
        oid = write_observation(self.db_path, sid, 'test', 'TRANSIENT')
        promote_to_active(self.db_path, oid)
        decay_observation(self.db_path, oid, 0.05)
        conn = self._conn()
        row = conn.execute("SELECT status, decay_score FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'DECAYED')
        self.assertEqual(row[1], 0.05)

    def test_contradiction_flow(self):
        sid = create_session(self.db_path)
        oid_a = write_observation(self.db_path, sid, 'obs a', 'PERSONAL')
        oid_b = write_observation(self.db_path, sid, 'obs b', 'PERSONAL')
        promote_to_active(self.db_path, oid_a)
        promote_to_active(self.db_path, oid_b)
        fid = flag_contradiction(self.db_path, sid, oid_a, oid_b, 'conflict')
        open = get_open_contradictions(self.db_path, sid)
        self.assertEqual(len(open), 1)
        self.assertEqual(open[0]['id'], fid)

        resolve_contradiction(self.db_path, fid, 'A_WINS')
        open = get_open_contradictions(self.db_path, sid)
        self.assertEqual(len(open), 0)

    def test_sign_out_blocked(self):
        sid = create_session(self.db_path)
        oid_a = write_observation(self.db_path, sid, 'obs a', 'ARCHITECTURAL')
        oid_b = write_observation(self.db_path, sid, 'obs b', 'ARCHITECTURAL')
        promote_to_active(self.db_path, oid_a)
        promote_to_active(self.db_path, oid_b)
        flag_contradiction(self.db_path, sid, oid_a, oid_b, 'must resolve')
        result = sign_out(self.db_path, sid)
        self.assertEqual(result['status'], 'BLOCKED')

    def test_sign_out_clean(self):
        sid = create_session(self.db_path)
        write_observation(self.db_path, sid, 'clean obs', 'OPERATIONAL')
        result = sign_out(self.db_path, sid)
        self.assertEqual(result['status'], 'CLOSED')
        conn = self._conn()
        row = conn.execute("SELECT status FROM sessions WHERE id = ?", (sid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'CLOSED')

    def test_still_broken(self):
        sid = create_session(self.db_path)
        oid_a = write_observation(self.db_path, sid, 'broken a', 'PERSONAL')
        oid_b = write_observation(self.db_path, sid, 'broken b', 'PERSONAL')
        promote_to_active(self.db_path, oid_a)
        promote_to_active(self.db_path, oid_b)
        flag_id = flag_contradiction(self.db_path, sid, oid_a, oid_b, 'conflict')
        still_broken(self.db_path, oid_a)
        conn = self._conn()
        row = conn.execute(
            "SELECT verdict FROM contradiction_flags WHERE id = ?", (flag_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 'NEITHER')

    def test_neither_worked(self):
        sid = create_session(self.db_path)
        oid_a = write_observation(self.db_path, sid, 'obs a', 'TRANSIENT')
        oid_b = write_observation(self.db_path, sid, 'obs b', 'TRANSIENT')
        promote_to_active(self.db_path, oid_a)
        promote_to_active(self.db_path, oid_b)
        fid = flag_contradiction(self.db_path, sid, oid_a, oid_b, 'neither')
        neither_worked(self.db_path, fid)
        conn = self._conn()
        row = conn.execute("SELECT verdict FROM contradiction_flags WHERE id = ?", (fid,)).fetchone()
        conn.close()
        self.assertEqual(row[0], 'NEITHER')

    def _make_active_obs(self):
        sid = create_session(self.db_path)
        oid = write_observation(self.db_path, sid, 'test content', 'ARCHITECTURAL')
        promote_to_active(self.db_path, oid)
        return sid, oid

    @patch('flatline_l2_promote.requests.post')
    def test_promote_session_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        sid, oid = self._make_active_obs()
        close_session(self.db_path, sid)

        result = promote_session(self.db_path, sid)
        self.assertEqual(result, {'promoted': 1, 'failed': 0})

        conn = self._conn()
        row = conn.execute("SELECT promoted_at FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertIsNotNone(row[0])

    @patch('flatline_l2_promote.requests.post')
    def test_promote_session_failure(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        sid, oid = self._make_active_obs()
        close_session(self.db_path, sid)

        result = promote_session(self.db_path, sid)
        self.assertEqual(result, {'promoted': 0, 'failed': 1})

        conn = self._conn()
        row = conn.execute("SELECT promoted_at FROM observations WHERE id = ?", (oid,)).fetchone()
        conn.close()
        self.assertIsNone(row[0])

    def test_promote_session_skips_candidate(self):
        sid = create_session(self.db_path)
        write_observation(self.db_path, sid, 'candidate only', 'TRANSIENT')
        close_session(self.db_path, sid)

        with patch('flatline_l2_promote.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp
            result = promote_session(self.db_path, sid)

        self.assertEqual(result, {'promoted': 0, 'failed': 0})
        mock_post.assert_not_called()

    @patch('flatline_l2_promote.requests.post')
    def test_sign_out_includes_promotion(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        sid, oid = self._make_active_obs()
        result = sign_out(self.db_path, sid)

        self.assertEqual(result['status'], 'CLOSED')
        self.assertEqual(result['promoted'], 1)
        self.assertEqual(result['failed'], 0)

    @patch('flatline_l2_promote.requests.post')
    def test_promote_session_skips_already_promoted(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        sid, oid = self._make_active_obs()
        close_session(self.db_path, sid)

        result1 = promote_session(self.db_path, sid)
        self.assertEqual(result1, {'promoted': 1, 'failed': 0})

        result2 = promote_session(self.db_path, sid)
        self.assertEqual(result2, {'promoted': 0, 'failed': 0})

        mock_post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
