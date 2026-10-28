"""The runner must refuse source trees whose recorded revision would not describe the code."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from run_validation import source_revision


class SourceRevisionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name).resolve()
        for relative in ("qiskit/__init__.py", "crates/lib/src/lib.rs", "Cargo.toml", "Cargo.lock"):
            self.write(relative, "")
        self.write(".gitignore", "*.so\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
        self.sha = self.git("rev-parse", "HEAD")
        self.extension = self.source / "qiskit" / "_accelerate.so"
        self.extension.write_text("")
        self.set_mtime(self.extension, self.newest() + 1)

    def write(self, relative, text):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], text=True).strip()

    def newest(self):
        return max(path.stat().st_mtime for path in self.source.rglob("*"))

    @staticmethod
    def set_mtime(path, stamp):
        os.utime(path, (stamp, stamp))

    def test_clean_checkout_returns_head(self):
        self.assertEqual(source_revision(self.source, self.extension), self.sha)

    def test_changes_outside_the_sources_are_allowed(self):
        self.write("notes.md", "scratch")
        self.write(".gitignore", "*.so\n/design/\n")
        self.assertEqual(source_revision(self.source, self.extension), self.sha)

    def test_untracked_source_file_fails(self):
        self.write("qiskit/extra.py", "")
        with self.assertRaisesRegex(ValueError, r"(?s)Uncommitted changes.*qiskit/extra\.py"):
            source_revision(self.source, self.extension)

    def test_modified_source_file_fails(self):
        for relative in ("Cargo.lock", "crates/lib/src/lib.rs", "qiskit/__init__.py"):
            with self.subTest(path=relative):
                self.write(relative, "changed")
                with self.assertRaisesRegex(ValueError, "(?s)Uncommitted changes.*" + relative):
                    source_revision(self.source, self.extension)
                self.write(relative, "")

    def test_rust_source_newer_than_extension_fails(self):
        # A git pull that rewrites a Rust file leaves it newer than the compiled extension.
        rust = self.source / "crates" / "lib" / "src" / "lib.rs"
        self.set_mtime(rust, self.extension.stat().st_mtime + 10)
        with self.assertRaisesRegex(ValueError, "older than crates/lib/src/lib.rs"):
            source_revision(self.source, self.extension)

    def test_revision_file_replaces_git(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        source = Path(directory.name).resolve()
        (source / "crates").mkdir()
        (source / "crates" / "lib.rs").write_text("")
        (source / "REVISION").write_text("abc123\n")
        extension = source / "_accelerate.so"
        extension.write_text("")
        self.set_mtime(extension, (source / "crates" / "lib.rs").stat().st_mtime + 1)
        self.assertEqual(source_revision(source, extension), "abc123")
        self.set_mtime(extension, extension.stat().st_mtime - 3600)
        with self.assertRaisesRegex(ValueError, "older than crates/lib.rs"):
            source_revision(source, extension)


if __name__ == "__main__":
    unittest.main()
