import importlib.util
import json
import os
import shutil
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


def provider_for_thread(path):
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT model_provider FROM threads WHERE id = 'thread-1'").fetchone()
    conn.close()
    return row[0]


def make_restore_backup_run(output_root, run_name, state_path, rollout_path, rollout_text):
    run_dir = output_root / run_name
    backup_dir = run_dir / "backups"
    backup_dir.mkdir(parents=True)
    shutil.copy2(state_path, backup_dir / "state_5.sqlite.before-provider-restore.sqlite")

    rollout_backup_path = tool_path_for_backup(run_dir, rollout_path)
    rollout_backup_path.parent.mkdir(parents=True)
    rollout_backup_path.write_text(rollout_text, encoding="utf-8")
    manifest_path = run_dir / "rollout-backups" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "source_path": str(rollout_path),
                    "backup_path": str(rollout_backup_path.relative_to(run_dir / "rollout-backups")),
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return run_dir


def tool_path_for_backup(run_dir, rollout_path):
    path = Path(rollout_path)
    if path.is_absolute():
        relative_parts = path.parts[1:]
    else:
        relative_parts = path.parts
    return run_dir / "rollout-backups" / Path(*relative_parts)


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
    def test_list_backup_runs_returns_timestamped_runs_with_sqlite_backups(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            valid_runs = ["20260621-120000", "20260621-120001"]
            for run_name in valid_runs:
                run_dir = output_root / run_name / "backups"
                run_dir.mkdir(parents=True)
                (run_dir / "state_5.sqlite.before-provider-restore.sqlite").write_text("backup", encoding="utf-8")
            (output_root / "20260621-120002").mkdir()
            (output_root / "notes").mkdir()

            self.assertEqual([path.name for path in tool.list_backup_runs(output_root)], valid_runs)

    def test_destination_for_rollout_backup_handles_windows_drive_paths(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "20260629-120000"

            backup_path = tool.destination_for_rollout_backup(
                run_dir,
                Path(r"C:\Users\me\.codex\sessions\rollout.jsonl"),
            )

            self.assertEqual(
                backup_path,
                run_dir / "rollout-backups" / "C" / "Users" / "me" / ".codex" / "sessions" / "rollout.jsonl",
            )

    def test_rollout_restore_destinations_uses_manifest_for_windows_paths(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "20260629-120000"
            backup_root = run_dir / "rollout-backups"
            backup_file = backup_root / "C" / "Users" / "me" / ".codex" / "sessions" / "rollout.jsonl"
            backup_file.parent.mkdir(parents=True)
            backup_file.write_text("backup", encoding="utf-8")
            (backup_root / "manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "source_path": r"C:\Users\me\.codex\sessions\rollout.jsonl",
                            "backup_path": "C/Users/me/.codex/sessions/rollout.jsonl",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            destinations = tool.rollout_restore_destinations(run_dir)

            self.assertEqual(destinations, [(backup_file, Path(r"C:\Users\me\.codex\sessions\rollout.jsonl"))])

    def test_parser_codex_home_sets_related_default_paths(self):
        tool = load_tool()

        args = tool.build_parser().parse_args(["--codex-home", "/tmp/codex-home"])

        self.assertEqual(args.state, Path("/tmp/codex-home/state_5.sqlite"))
        self.assertEqual(args.config, Path("/tmp/codex-home/config.toml"))
        self.assertEqual(args.output_root, Path("/tmp/codex-home/provider-restore-rollouts"))

    def test_rollback_backup_run_dry_run_reports_without_modifying_files(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            output_root = tmp_path / "restored"
            rollout_path = tmp_path / "rollout.jsonl"
            rollout_path.write_text(
                '{"type":"session_meta","payload":{"model_provider":"anyrouter"}}\n',
                encoding="utf-8",
            )
            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, rollout_path)
            conn = sqlite3.connect(state_path)
            conn.execute("UPDATE threads SET model_provider = 'anyrouter'")
            conn.commit()
            conn.close()

            backup_state = tmp_path / "backup-state.sqlite"
            make_state_db(backup_state, rollout_path)
            run_dir = make_restore_backup_run(
                output_root,
                "20260621-120000",
                backup_state,
                rollout_path,
                '{"type":"session_meta","payload":{"model_provider":"custom"}}\n',
            )

            result = tool.rollback_backup_run(run_dir, state_path=state_path, apply=False)

            self.assertEqual(result.restored_rollouts, 1)
            self.assertEqual(result.missing_rollouts, 0)
            self.assertIsNone(result.current_backup_path)
            self.assertEqual(provider_for_thread(state_path), "anyrouter")
            self.assertIn('"model_provider":"anyrouter"', rollout_path.read_text(encoding="utf-8"))

    def test_rollback_backup_run_apply_restores_sqlite_and_rollouts(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            output_root = tmp_path / "restored"
            rollout_path = tmp_path / "rollout.jsonl"
            rollout_path.write_text(
                '{"type":"session_meta","payload":{"model_provider":"anyrouter"}}\n',
                encoding="utf-8",
            )
            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, rollout_path)
            conn = sqlite3.connect(state_path)
            conn.execute("UPDATE threads SET model_provider = 'anyrouter'")
            conn.commit()
            conn.close()
            state_wal_path = Path(str(state_path) + "-wal")
            state_shm_path = Path(str(state_path) + "-shm")
            state_wal_path.write_text("stale wal", encoding="utf-8")
            state_shm_path.write_text("stale shm", encoding="utf-8")

            backup_state = tmp_path / "backup-state.sqlite"
            make_state_db(backup_state, rollout_path)
            run_dir = make_restore_backup_run(
                output_root,
                "20260621-120000",
                backup_state,
                rollout_path,
                '{"type":"session_meta","payload":{"model_provider":"custom"}}\n',
            )

            result = tool.rollback_backup_run(run_dir, state_path=state_path, apply=True)

            self.assertEqual(result.restored_rollouts, 1)
            self.assertEqual(result.missing_rollouts, 0)
            self.assertTrue(result.current_backup_path.exists())
            self.assertEqual(provider_for_thread(state_path), "custom")
            self.assertIn('"model_provider":"custom"', rollout_path.read_text(encoding="utf-8"))
            self.assertFalse(state_wal_path.exists())
            self.assertFalse(state_shm_path.exists())

    def test_cleanup_backup_runs_keeps_five_newest_timestamped_directories(self):
        tool = load_tool()

        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            run_names = [
                "20260621-120000",
                "20260621-120001",
                "20260621-120002",
                "20260621-120003",
                "20260621-120004",
                "20260621-120005",
                "20260621-120006",
            ]
            for run_name in run_names:
                (output_root / run_name / "backups").mkdir(parents=True)
            (output_root / "notes").mkdir()

            removed = tool.cleanup_backup_runs(output_root, keep=5)

            self.assertEqual([path.name for path in removed], run_names[:2])
            self.assertEqual(
                sorted(path.name for path in output_root.iterdir()),
                ["20260621-120002", "20260621-120003", "20260621-120004", "20260621-120005", "20260621-120006", "notes"],
            )

    def test_apply_cleans_old_backup_runs_after_successful_restore(self):
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

            output_root = tmp_path / "restored"
            old_run_names = [
                "20260621-120000",
                "20260621-120001",
                "20260621-120002",
                "20260621-120003",
                "20260621-120004",
            ]
            for run_name in old_run_names:
                (output_root / run_name / "backups").mkdir(parents=True)

            result = tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=output_root,
                apply=True,
                timestamp="20260621-120005",
            )

            self.assertEqual([path.name for path in result.removed_backup_runs], ["20260621-120000"])
            self.assertEqual(
                sorted(path.name for path in output_root.iterdir()),
                ["20260621-120001", "20260621-120002", "20260621-120003", "20260621-120004", "20260621-120005"],
            )

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

    def test_restore_preserves_rollout_file_mtime_when_rewriting_provider(self):
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
            old_mtime_ns = 1_700_000_000_123_456_789
            os.utime(original_rollout, ns=(old_mtime_ns, old_mtime_ns))

            state_path = tmp_path / "state.sqlite"
            make_state_db(state_path, original_rollout)

            tool.restore_threads(
                state_path=state_path,
                config_path=config_path,
                output_root=tmp_path / "restored",
                apply=True,
                timestamp="20260621-preserve-rollout-mtime",
            )

            self.assertIn('"model_provider": "anyrouter"', original_rollout.read_text(encoding="utf-8"))
            self.assertEqual(original_rollout.stat().st_mtime_ns, old_mtime_ns)


if __name__ == "__main__":
    unittest.main()
