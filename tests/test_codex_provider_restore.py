import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "codex_provider_restore.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("codex_provider_restore", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_state_db(path, rollout_path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            title TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO threads (id, rollout_path, model_provider, title) VALUES (?, ?, ?, ?)",
        ("thread-1", str(rollout_path), "custom", "old provider thread"),
    )
    conn.commit()
    conn.close()


class CodexProviderRestoreTests(unittest.TestCase):
    def test_restore_copies_rollout_and_updates_database_without_touching_original(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config_path = tmp_path / "config.toml"
            config_path.write_text('model_provider = "anyrouter"\n', encoding="utf-8")

            original_rollout = tmp_path / "sessions" / "rollout.jsonl"
            original_rollout.parent.mkdir()
            original_rollout.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "thread-1",
                            "model_provider": "custom",
                            "title": "old provider thread",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, original_rollout)

            result = tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=tmp_path / "restored",
                apply=True,
                timestamp="20260619-restore-test",
            )

            self.assertEqual(result.target_provider, "anyrouter")
            self.assertEqual(result.updated_threads, 1)
            self.assertEqual(result.copied_rollouts, 1)
            self.assertTrue(result.backup_path.exists())

            original_text = original_rollout.read_text(encoding="utf-8")
            self.assertIn('"model_provider": "custom"', original_text)

            copied_rollout = Path(result.copied_paths[str(original_rollout)])
            self.assertTrue(copied_rollout.exists())
            copied_text = copied_rollout.read_text(encoding="utf-8")
            self.assertIn('"model_provider": "anyrouter"', copied_text)

            conn = sqlite3.connect(state_path)
            row = conn.execute("SELECT model_provider, rollout_path FROM threads WHERE id = 'thread-1'").fetchone()
            conn.close()

            self.assertEqual(row, ("anyrouter", str(copied_rollout)))

    def test_dry_run_does_not_modify_database_or_create_backup(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config_path = tmp_path / "config.toml"
            config_path.write_text('model_provider = "anyrouter"\n', encoding="utf-8")

            original_rollout = tmp_path / "rollout.jsonl"
            original_rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"custom"}}\n',
                encoding="utf-8",
            )

            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, original_rollout)

            result = tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=tmp_path / "restored",
                apply=False,
                timestamp="20260619-dry-run",
            )

            self.assertEqual(result.updated_threads, 1)
            self.assertEqual(result.copied_rollouts, 1)
            self.assertIsNone(result.backup_path)

            conn = sqlite3.connect(state_path)
            row = conn.execute("SELECT model_provider, rollout_path FROM threads WHERE id = 'thread-1'").fetchone()
            conn.close()

            self.assertEqual(row, ("custom", str(original_rollout)))
            self.assertFalse((tmp_path / "restored").exists())


if __name__ == "__main__":
    unittest.main()
