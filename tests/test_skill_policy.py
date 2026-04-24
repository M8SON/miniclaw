"""Tests for per-tier policy constants and helpers."""

import unittest

from core.skill_policy import (
    TIER_BUNDLED,
    TIER_AUTHORED,
    TIER_IMPORTED,
    TIER_DEV,
    policy_for,
    is_credential_pattern,
    is_scoped_volume,
    DEVICE_ALLOWLIST_PATTERNS,
)


class TestPolicyLookup(unittest.TestCase):
    def test_bundled_has_no_clamps(self):
        policy = policy_for(TIER_BUNDLED)
        self.assertIsNone(policy.max_memory_mb)
        self.assertIsNone(policy.max_timeout_seconds)
        self.assertIsNone(policy.max_cpus)
        self.assertTrue(policy.allow_native)

    def test_authored_has_moderate_clamps(self):
        policy = policy_for(TIER_AUTHORED)
        self.assertEqual(policy.max_memory_mb, 1024)
        self.assertEqual(policy.max_timeout_seconds, 120)
        self.assertEqual(policy.max_cpus, 2.0)
        self.assertFalse(policy.allow_native)

    def test_imported_has_strict_clamps(self):
        policy = policy_for(TIER_IMPORTED)
        self.assertEqual(policy.max_memory_mb, 512)
        self.assertEqual(policy.max_timeout_seconds, 60)
        self.assertEqual(policy.max_cpus, 1.0)
        self.assertFalse(policy.allow_native)

    def test_dev_matches_bundled_policy(self):
        self.assertEqual(policy_for(TIER_DEV), policy_for(TIER_BUNDLED))


class TestCredentialPattern(unittest.TestCase):
    def test_anthropic_api_key_matches(self):
        self.assertTrue(is_credential_pattern("ANTHROPIC_API_KEY"))

    def test_generic_token_matches(self):
        self.assertTrue(is_credential_pattern("GITHUB_TOKEN"))

    def test_generic_secret_matches(self):
        self.assertTrue(is_credential_pattern("STRIPE_SECRET"))

    def test_generic_key_matches(self):
        self.assertTrue(is_credential_pattern("OPENWEATHER_API_KEY"))

    def test_plain_name_does_not_match(self):
        self.assertFalse(is_credential_pattern("LOG_LEVEL"))


class TestScopedVolume(unittest.TestCase):
    def test_miniclaw_scoped_path_ok(self):
        home = "/home/user"
        self.assertTrue(is_scoped_volume("~/.miniclaw/foo:/data", "foo", home))

    def test_root_mount_rejected(self):
        self.assertFalse(is_scoped_volume("/:/rootfs", "foo", "/home/user"))

    def test_home_root_rejected(self):
        self.assertFalse(is_scoped_volume("~:/host", "foo", "/home/user"))

    def test_wrong_skill_name_rejected(self):
        self.assertFalse(is_scoped_volume("~/.miniclaw/bar:/data", "foo", "/home/user"))


class TestDeviceAllowlist(unittest.TestCase):
    def test_snd_allowed(self):
        self.assertTrue(any(p.match("/dev/snd") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_i2c_wildcard_allowed(self):
        self.assertTrue(any(p.match("/dev/i2c-0") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_kmsg_rejected(self):
        self.assertFalse(any(p.match("/dev/kmsg") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_mem_rejected(self):
        self.assertFalse(any(p.match("/dev/mem") for p in DEVICE_ALLOWLIST_PATTERNS))


if __name__ == "__main__":
    unittest.main()
