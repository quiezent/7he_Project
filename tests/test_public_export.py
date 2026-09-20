"""Synthetic allowlist-export tests, independent of private athlete data."""
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from tools.export_public import PUBLIC_FILES, export_public


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.public = self.root / "public"
        for name in PUBLIC_FILES:
            path = self.public / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic public fixture: " + name, encoding="utf-8")
        self.output = self.root / "export.zip"

    def test_allowlist_only_and_manifest_hashes(self):
        (self.root / "private.json").write_text("private fixture", encoding="utf-8")
        (self.public / "unreviewed.txt").write_text("unreviewed fixture", encoding="utf-8")
        export_public(self.root, self.output)
        with zipfile.ZipFile(self.output) as archive:
            prefix = "7he-project-public/"
            self.assertEqual(set(archive.namelist()),
                             {prefix + p for p in (*PUBLIC_FILES, "MANIFEST.json")})
            manifest = json.loads(archive.read(prefix + "MANIFEST.json"))
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), digest)

    def test_existing_output_is_not_overwritten(self):
        self.output.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError): export_public(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b"preserve")

    def test_missing_file_produces_no_archive(self):
        (self.public / PUBLIC_FILES[0]).unlink()
        with self.assertRaises(ValueError): export_public(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_file_rejected(self):
        target = self.root / "private.txt"; target.write_text("fixture", encoding="utf-8")
        path = self.public / PUBLIC_FILES[0]; path.unlink(); path.symlink_to(target)
        with self.assertRaises(ValueError): export_public(self.root, self.output)

    def test_symlink_directory_rejected(self):
        old = self.public / "tests"; old.rename(self.root / "elsewhere")
        old.symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaises(ValueError): export_public(self.root, self.output)

    def test_output_inside_public_rejected(self):
        with self.assertRaises(ValueError): export_public(self.root, self.public / "new.zip")

    def test_missing_output_parent_rejected(self):
        with self.assertRaises(ValueError):
            export_public(self.root, self.root / "missing" / "export.zip")

    def test_dangling_output_symlink_rejected(self):
        self.output.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError): export_public(self.root, self.output)


if __name__ == "__main__": unittest.main()
