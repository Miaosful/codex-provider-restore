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


def make_state_db_with_timestamps(path, rollout_path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            title TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            recency_at INTEGER NOT NULL,
            recency_at_ms INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER touch_times_after_provider_update
        AFTER UPDATE OF model_provider ON threads
        BEGIN
            UPDATE threads
            SET updated_at = 999,
                updated_at_ms = 999000,
                recency_at = 999,
                recency_at_ms = 999000
            WHERE id = NEW.id;
        END
        """
    )
    conn.execute(
        """
        INSERT INTO threads (
            id, rollout_path, model_provider, title,
            updated_at, updated_at_ms, recency_at, recency_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "thread-1",
            str(rollout_path),
            "custom",
            "old provider thread",
            100,
            100000,
            90,
            90000,
        ),
    )
    conn.commit()
    conn.close()


class CodexProviderRestoreTests(unittest.TestCase):
    def test_restore_updates_rollout_in_place_and_keeps_database_path(self):
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
            self.assertEqual(result.rewritten_rollouts, 1)
            self.assertTrue(result.backup_path.exists())

            original_text = original_rollout.read_text(encoding="utf-8")
            self.assertIn('"model_provider": "anyrouter"', original_text)
            rollout_backup = Path(result.rollout_backup_paths[str(original_rollout)])
            self.assertTrue(rollout_backup.exists())
            self.assertIn('"model_provider": "custom"', rollout_backup.read_text(encoding="utf-8"))

            conn = sqlite3.connect(state_path)
            row = conn.execute("SELECT model_provider, rollout_path FROM threads WHERE id = 'thread-1'").fetchone()
            conn.close()

            self.assertEqual(row, ("anyrouter", str(original_rollout)))

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
            self.assertEqual(result.rewritten_rollouts, 1)
            self.assertIsNone(result.backup_path)

            conn = sqlite3.connect(state_path)
            row = conn.execute("SELECT model_provider, rollout_path FROM threads WHERE id = 'thread-1'").fetchone()
            conn.close()

            self.assertEqual(row, ("custom", str(original_rollout)))
            self.assertFalse((tmp_path / "restored").exists())

    def test_matching_provider_does_not_rewrite_rollout(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config_path = tmp_path / "config.toml"
            config_path.write_text('model_provider = "custom"\n', encoding="utf-8")

            original_rollout = tmp_path / "rollout.jsonl"
            original_text = '{"type":"session_meta","payload":{"model_provider":"custom"}}\n'
            original_rollout.write_text(original_text, encoding="utf-8")

            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, original_rollout)

            result = tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=tmp_path / "restored",
                apply=True,
                timestamp="20260619-noop",
            )

            self.assertEqual(result.updated_threads, 0)
            self.assertEqual(result.rewritten_rollouts, 0)
            self.assertEqual(original_rollout.read_text(encoding="utf-8"), original_text)

    def test_restore_recovers_database_path_from_previous_copy_based_restore(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            config_path = tmp_path / "config.toml"
            config_path.write_text('model_provider = "anyrouter"\n', encoding="utf-8")

            original_rollout = tmp_path / "sessions" / "rollout.jsonl"
            original_rollout.parent.mkdir()
            original_rollout.write_text(
                '{"type":"session_meta","payload":{"model_provider":"custom"}}\n',
                encoding="utf-8",
            )

            output_root = tmp_path / "restored"
            bad_rollout_path = output_root / "20260619-bad" / "rollouts" / Path(*original_rollout.parts[1:])
            bad_rollout_path.parent.mkdir(parents=True)
            bad_rollout_path.write_text(original_rollout.read_text(encoding="utf-8"), encoding="utf-8")

            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, bad_rollout_path)

            result = tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=output_root,
                apply=True,
                timestamp="20260619-recover-bad-path",
            )

            self.assertEqual(result.updated_threads, 1)
            self.assertEqual(result.rewritten_rollouts, 1)

            conn = sqlite3.connect(state_path)
            row = conn.execute("SELECT model_provider, rollout_path FROM threads WHERE id = 'thread-1'").fetchone()
            conn.close()

            self.assertEqual(row, ("anyrouter", str(original_rollout)))
            self.assertIn('"model_provider": "anyrouter"', original_rollout.read_text(encoding="utf-8"))

    def test_restore_preserves_thread_timestamps_when_provider_update_touches_row(self):
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
            make_state_db_with_timestamps(state_path, original_rollout)

            tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=tmp_path / "restored",
                apply=True,
                timestamp="20260620-preserve-times",
            )

            conn = sqlite3.connect(state_path)
            row = conn.execute(
                """
                SELECT model_provider, updated_at, updated_at_ms, recency_at, recency_at_ms
                FROM threads
                WHERE id = 'thread-1'
                """
            ).fetchone()
            conn.close()

            self.assertEqual(row, ("anyrouter", 100, 100000, 90, 90000))


if __name__ == "__main__":
    unittest.main()
