"""Tests for the miniclaw skill CLI."""

import unittest
from io import StringIO
from unittest.mock import patch

from core.skill_cli import build_parser, dispatch


class TestParser(unittest.TestCase):
    def test_install_requires_source(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["install"])

    def test_install_accepts_path(self):
        parser = build_parser()
        args = parser.parse_args(["install", "/tmp/foo", "--tier", "imported"])
        self.assertEqual(args.subcommand, "install")
        self.assertEqual(args.source, "/tmp/foo")
        self.assertEqual(args.tier, "imported")

    def test_list_default(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        self.assertEqual(args.subcommand, "list")

    def test_dev_requires_path(self):
        parser = build_parser()
        args = parser.parse_args(["dev", "/tmp/foo"])
        self.assertEqual(args.subcommand, "dev")


class TestListDispatch(unittest.TestCase):
    def test_list_prints_loaded_skills(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            rc = dispatch(args)
        self.assertEqual(rc, 0)
        self.assertIn("dashboard", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
