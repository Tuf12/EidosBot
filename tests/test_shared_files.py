"""Tests for web-app file sharing."""

import tempfile
import unittest
from pathlib import Path

from eidos.gateway.files import SharedFileRegistry, content_disposition
from eidos.gateway.server import RemoteGatewayServer
from eidos.safety.risk_classifier import RiskClassifier, RiskLevel


class TestSharedFileRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.events = []
        self.registry = SharedFileRegistry(
            index_path=str(root / "index.json"),
            inbox_dir=str(root / "inbox"),
            on_shared=self.events.append,
        )
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_and_reload(self):
        sample = self.root / "notes.md"
        sample.write_text("# Hello\n", encoding="utf-8")
        rec = self.registry.register(str(sample), conversation_id="chat-1", note="draft")
        self.assertTrue(rec["id"])
        self.assertEqual(rec["name"], "notes.md")
        self.assertEqual(rec["mime"], "text/markdown")
        self.assertFalse(rec["already_shared"])
        self.assertEqual(len(self.events), 1)

        again = self.registry.register(str(sample), conversation_id="chat-1", note="draft")
        self.assertEqual(again["id"], rec["id"])
        self.assertTrue(again["already_shared"])
        self.assertEqual(len(self.events), 1)

        reloaded = SharedFileRegistry(
            index_path=str(self.root / "index.json"),
            inbox_dir=str(self.root / "inbox"),
        )
        listed = reloaded.list_records("chat-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], rec["id"])
        self.assertEqual(reloaded.list_records("other-chat"), [])

    def test_conversations_stay_separate(self):
        sample = self.root / "report.pdf"
        sample.write_bytes(b"%PDF-1.4")
        self.registry.register(str(sample), conversation_id="a")
        self.registry.register(str(sample), conversation_id="b")
        self.assertEqual(len(self.registry.list_records("a")), 1)
        self.assertEqual(len(self.registry.list_records("b")), 1)
        self.assertNotEqual(
            self.registry.list_records("a")[0]["id"],
            self.registry.list_records("b")[0]["id"],
        )

    def test_missing_and_directory_are_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.register(str(self.root / "nope.txt"))
        with self.assertRaises(ValueError):
            self.registry.register(str(self.root))

    def test_upload_uses_original_name(self):
        rec = self.registry.save_upload(
            "../weird name.md",
            b"# from the phone\n",
            conversation_id="chat-1",
        )
        self.assertEqual(rec["name"], "weird name.md")
        self.assertEqual(rec["direction"], "user")
        self.assertTrue(Path(rec["path"]).is_file())
        self.assertEqual(Path(rec["path"]).read_text(encoding="utf-8"), "# from the phone\n")
        self.assertIsNone(self.registry.get("does-not-exist"))

    def test_missing_file_is_flagged(self):
        sample = self.root / "gone.txt"
        sample.write_text("x", encoding="utf-8")
        rec = self.registry.register(str(sample), conversation_id="chat-1")
        sample.unlink()
        listed = self.registry.get(rec["id"])
        self.assertTrue(listed["missing"])

    def test_content_disposition_is_inline_or_download(self):
        inline = content_disposition("notes.md", False)
        download = content_disposition("notes.md", True)
        self.assertTrue(inline.startswith("inline;"))
        self.assertTrue(download.startswith("attachment;"))
        self.assertIn("notes.md", inline)

    def test_share_file_is_passive(self):
        level, _ = RiskClassifier.classify_action("share_file", {"path": "/tmp/a.md"})
        self.assertEqual(level, RiskLevel.LEVEL_0_PASSIVE)

    def test_gateway_lists_files_for_the_active_conversation(self):
        sample = self.root / "brief.md"
        sample.write_text("brief", encoding="utf-8")
        self.registry.register(str(sample), conversation_id="conv-9", note="brief")

        class _Loop:
            active_conversation_id = "conv-9"

        server = RemoteGatewayServer.__new__(RemoteGatewayServer)
        server.agent_loop = _Loop()
        server.files = self.registry
        listed = server._shared_files_for(None)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "brief.md")
        prompt = RemoteGatewayServer._attachment_prompt(
            "Look this over",
            [{"name": "brief.md", "path": str(sample)}],
        )
        self.assertIn("Look this over", prompt)
        self.assertIn(str(sample), prompt)


if __name__ == "__main__":
    unittest.main()
