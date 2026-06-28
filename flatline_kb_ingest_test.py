import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from flatline_kb_ingest import (
    parse_frontmatter,
    extract_durable_chunks,
    find_duplicate_node,
    create_knowledge_node,
    corroborate_knowledge_node,
    ingest_file,
    DEDUP_THRESHOLD,
)


class TestParseFrontmatter(unittest.TestCase):
    def test_no_frontmatter(self):
        text = "Just a regular note, no frontmatter here."
        metadata, body = parse_frontmatter(text)
        self.assertEqual(metadata, {})
        self.assertEqual(body, text)

    def test_scalar_frontmatter(self):
        text = '---\ntitle: "Fermented Cabbage"\nsource: book\n---\nBody text here.'
        metadata, body = parse_frontmatter(text)
        self.assertEqual(metadata["title"], "Fermented Cabbage")
        self.assertEqual(metadata["source"], "book")
        self.assertIn("Body text here.", body)

    def test_list_frontmatter(self):
        text = '---\ntags:\n  - "fermentation"\n  - "pickling"\n---\nBody.'
        metadata, body = parse_frontmatter(text)
        self.assertEqual(metadata["tags"], ["fermentation", "pickling"])


class TestExtractDurableChunks(unittest.TestCase):
    def test_keeps_durable_paragraph(self):
        body = (
            "## Fermentation Basics\n\n"
            "Lacto-fermentation requires a sufficient salt concentration "
            "to suppress pathogenic bacteria growth in vegetables."
        )
        chunks = extract_durable_chunks(body, source_title="test")
        self.assertEqual(len(chunks), 1)
        self.assertIn("salt concentration", chunks[0]["content"])
        self.assertEqual(chunks[0]["decay_class"], "OPERATIONAL")

    def test_skips_procedural_steps(self):
        body = "## Method\n\nBlanch the cabbage for two minutes then drain and pat dry."
        chunks = extract_durable_chunks(body, source_title="test")
        self.assertEqual(chunks, [])

    def test_skips_first_person_opinion(self):
        body = (
            "## Notes\n\n"
            "I really like this recipe, I think my version is better than "
            "I used to make and I found my own twist works best for me."
        )
        chunks = extract_durable_chunks(body, source_title="test")
        self.assertEqual(chunks, [])

    def test_alt_text_regex_strips_full_sentence(self):
        # Regression test: the old regex only stripped the first word after
        # "Image shows", leaving fragments like "a cabbage being fermented
        # in a jar." behind in the chunk. Fixed regex should remove the
        # whole sentence and leave only the real content.
        body = (
            "## Notes\n\n"
            "Image shows a cabbage being fermented in a crock. "
            "Lacto-fermentation requires a sufficient salt concentration "
            "to suppress pathogenic bacteria growth in vegetables."
        )
        chunks = extract_durable_chunks(body, source_title="test")
        self.assertEqual(len(chunks), 1)
        content = chunks[0]["content"]
        self.assertNotIn("Image shows", content)
        self.assertNotIn("cabbage being fermented in a crock", content)
        self.assertIn("salt concentration", content)


class TestFindDuplicateNode(unittest.TestCase):
    @patch("flatline_kb_ingest.search_existing_knowledge_nodes")
    def test_returns_matched_id_above_threshold(self, mock_search):
        mock_search.return_value = [
            {"id": 1, "score": DEDUP_THRESHOLD + 0.01, "content": "x", "node_id": "kn-123"},
        ]
        result = find_duplicate_node("some content", [0.1, 0.2])
        self.assertEqual(result, "kn-123")

    @patch("flatline_kb_ingest.search_existing_knowledge_nodes")
    def test_returns_none_below_threshold(self, mock_search):
        mock_search.return_value = [
            {"id": 1, "score": DEDUP_THRESHOLD - 0.05, "content": "x", "node_id": "kn-123"},
        ]
        result = find_duplicate_node("some content", [0.1, 0.2])
        self.assertIsNone(result)

    @patch("flatline_kb_ingest.search_existing_knowledge_nodes")
    def test_returns_none_when_node_id_missing(self, mock_search):
        # Defensive case: a high-scoring hit with no node_id in its payload
        # (e.g. a non-KnowledgeNode point that slipped through the filter)
        # must not be treated as a usable match.
        mock_search.return_value = [
            {"id": 1, "score": 0.99, "content": "x", "node_id": None},
        ]
        result = find_duplicate_node("some content", [0.1, 0.2])
        self.assertIsNone(result)

    @patch("flatline_kb_ingest.search_existing_knowledge_nodes")
    def test_returns_none_with_no_hits(self, mock_search):
        mock_search.return_value = []
        result = find_duplicate_node("some content", [0.1, 0.2])
        self.assertIsNone(result)


class TestCreateAndCorroborate(unittest.TestCase):
    def test_create_knowledge_node_writes_locked_schema_fields(self):
        session = MagicMock()
        create_knowledge_node(
            session,
            node_id="abc-uuid",
            content="test content",
            source_title="Test Source",
            source_chunk_ref="Intro",
            decay_class="OPERATIONAL",
            confidence=0.8,
            embedding_id="999",
        )
        self.assertEqual(session.run.call_count, 1)
        _, kwargs = session.run.call_args
        self.assertEqual(kwargs["node_id"], "abc-uuid")
        self.assertEqual(kwargs["embedding_id"], "999")
        self.assertEqual(kwargs["source_descriptor"], "Test Source::Intro")
        query_text = session.run.call_args[0][0]
        self.assertIn("id: $node_id", query_text)
        self.assertIn("embedding_id: $embedding_id", query_text)
        self.assertIn("corroboration_count: 1", query_text)

    def test_corroborate_knowledge_node_matches_by_id_not_node_id(self):
        session = MagicMock()
        corroborate_knowledge_node(session, "existing-uuid", "New Source", "Ch. 2")
        query_text = session.run.call_args[0][0]
        kwargs = session.run.call_args[1]
        # Must MATCH on the schema's `id` field, not a content-hash `node_id`
        self.assertIn("{id: $node_id}", query_text)
        self.assertIn("corroboration_count = n.corroboration_count + 1", query_text)
        self.assertIn("coalesce(n.sources", query_text)
        self.assertEqual(kwargs["node_id"], "existing-uuid")
        self.assertEqual(kwargs["source_descriptor"], "New Source::Ch. 2")


class TestIngestFileFlow(unittest.TestCase):
    def _write_tmp_md(self, body):
        f = tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", delete=False, encoding="utf-8"
        )
        f.write(body)
        f.close()
        return f.name

    def setUp(self):
        self.body = (
            "## Fermentation Basics\n\n"
            "Lacto-fermentation requires a sufficient salt concentration "
            "to suppress pathogenic bacteria growth in vegetables."
        )
        self.path = self._write_tmp_md(self.body)
        self.session = MagicMock()

    def tearDown(self):
        os.unlink(self.path)

    @patch("flatline_kb_ingest.upsert_knowledge_node_vector")
    @patch("flatline_kb_ingest.create_knowledge_node")
    @patch("flatline_kb_ingest.corroborate_knowledge_node")
    @patch("flatline_kb_ingest.find_duplicate_node", return_value=None)
    @patch("flatline_kb_ingest._embed", return_value=[0.42, 0.43, 0.44])
    def test_new_chunk_creates_node_and_embeds_exactly_once(
        self, mock_embed, mock_find_dup, mock_corroborate, mock_create, mock_upsert
    ):
        stats = ingest_file(Path(self.path), self.session)

        self.assertEqual(stats["ingested"], 1)
        self.assertEqual(stats["deduped"], 0)
        self.assertEqual(stats["skipped"], 0)

        mock_create.assert_called_once()
        mock_corroborate.assert_not_called()

        # Regression check: embed should be called exactly once per chunk —
        # not once for the dedup check and again inside the Qdrant upsert.
        self.assertEqual(mock_embed.call_count, 1)
        upsert_args = mock_upsert.call_args[0]
        vector_passed_to_upsert = upsert_args[2]
        self.assertEqual(vector_passed_to_upsert, [0.42, 0.43, 0.44])

    @patch("flatline_kb_ingest.upsert_knowledge_node_vector")
    @patch("flatline_kb_ingest.create_knowledge_node")
    @patch("flatline_kb_ingest.corroborate_knowledge_node")
    @patch("flatline_kb_ingest.find_duplicate_node", return_value="existing-node-uuid")
    @patch("flatline_kb_ingest._embed", return_value=[0.1, 0.2, 0.3])
    def test_duplicate_chunk_corroborates_instead_of_creating(
        self, mock_embed, mock_find_dup, mock_corroborate, mock_create, mock_upsert
    ):
        stats = ingest_file(Path(self.path), self.session)

        self.assertEqual(stats["ingested"], 0)
        self.assertEqual(stats["deduped"], 1)
        self.assertEqual(stats["skipped"], 0)

        mock_corroborate.assert_called_once()
        args = mock_corroborate.call_args[0]
        self.assertEqual(args[1], "existing-node-uuid")

        mock_create.assert_not_called()
        mock_upsert.assert_not_called()

    @patch("flatline_kb_ingest._embed", side_effect=RuntimeError("embedding endpoint down"))
    def test_embed_failure_increments_skipped(self, mock_embed):
        stats = ingest_file(Path(self.path), self.session)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["ingested"], 0)
        self.assertEqual(stats["deduped"], 0)


if __name__ == "__main__":
    unittest.main()
