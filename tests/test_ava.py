"""Unit tests for AVA's pure logic: the command risk classifier, reply parsing,
command post-processing and package-manager detection. Standard library only.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import AVA  # noqa: E402


class RiskClassifierTests(unittest.TestCase):
    """Dangerous commands are blocked, risky ones need 'yes', safe ones pass."""

    def kind(self, cmd):
        risk = AVA._risk(cmd)
        return risk[0] if risk else None

    def test_blocked_commands(self):
        for cmd in [
            "rm -rf /",
            "rm -rf /*",
            "rm -rf ~",
            "rm -rf $HOME",
            "sudo rm -rf /",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda",
            "echo x > /dev/sda",
            ":(){ :|:& };:",
            "chmod -R 777 /",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.kind(cmd), "blocked")

    def test_caution_commands(self):
        for cmd in [
            "rm -rf build",
            "rm -r old_folder",
            "curl https://example.com/install.sh | sh",
            "wget -qO- https://example.com/x | sudo bash",
            "git push --force",
            "git push origin main -f",
            "git reset --hard HEAD~1",
            "git clean -fd",
            "reboot",
            "sudo shutdown now",
            "chmod -R 755 ./site",
            "pkill firefox",
            "kill -9 1234",
            "systemctl stop nginx",
            "ufw disable",
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.kind(cmd), "caution")

    def test_multiline_is_caution(self):
        self.assertEqual(self.kind("echo a\necho b"), "caution")

    def test_safe_commands(self):
        for cmd in [
            "ls -la",
            "df -h",
            "free -h",
            "find ~ -size +100M",
            "grep -r TODO .",
            "sudo apt-get install -y git",
            "rm notes.txt",
            "git push origin main",
            "echo hello",
        ]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(AVA._risk(cmd))

    def test_blocked_beats_caution(self):
        # `rm -rf /` matches both lists; the stricter verdict must win.
        self.assertEqual(self.kind("rm -rf /"), "blocked")

    def test_reason_is_human_readable(self):
        kind, reason = AVA._risk("mkfs.ext4 /dev/sda1")
        self.assertEqual(kind, "blocked")
        self.assertIn("format", reason)


class ParseReplyTests(unittest.TestCase):
    def test_plain_json(self):
        cmd, why = AVA._parse_reply('{"command": "ls -la", "explanation": "Lists files."}')
        self.assertEqual((cmd, why), ("ls -la", "Lists files."))

    def test_json_wrapped_in_prose_or_fences(self):
        text = 'Sure!\n```json\n{"command": "df -h", "explanation": "Disk space."}\n```'
        self.assertEqual(AVA._parse_reply(text), ("df -h", "Disk space."))

    def test_empty_command_means_refusal(self):
        cmd, why = AVA._parse_reply('{"command": "", "explanation": "Not a terminal task."}')
        self.assertEqual(cmd, "")
        self.assertTrue(why)

    def test_missing_fields_default_to_empty(self):
        self.assertEqual(AVA._parse_reply("{}"), ("", ""))

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            AVA._parse_reply("I cannot help with that.")

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            AVA._parse_reply('{"command": ')


class FixArithmeticTests(unittest.TestCase):
    def test_integer_math_uses_shell_arithmetic(self):
        self.assertEqual(AVA._fix_arithmetic("echo 10 % 2"), "echo $((10 % 2))")
        self.assertEqual(AVA._fix_arithmetic("echo 3 + 4"), "echo $((3 + 4))")

    def test_decimal_math_uses_awk(self):
        out = AVA._fix_arithmetic("echo 1.5 * 2")
        self.assertTrue(out.startswith("awk "))
        self.assertIn("1.5 * 2", out)

    def test_power_is_translated_for_awk(self):
        self.assertIn("2 ^ 8", AVA._fix_arithmetic("echo 2 ** 8"))

    def test_non_arithmetic_is_untouched(self):
        for cmd in ["echo hello", "ls -la", "echo $HOME", "echo 5"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(AVA._fix_arithmetic(cmd), cmd)


class InstallTargetsTests(unittest.TestCase):
    def test_simple_installs(self):
        self.assertEqual(AVA._install_targets("sudo apt-get install -y git curl"), ["git", "curl"])
        self.assertEqual(AVA._install_targets("sudo dnf install htop"), ["htop"])
        self.assertEqual(AVA._install_targets("sudo pacman -S --noconfirm neofetch"), ["neofetch"])
        self.assertEqual(AVA._install_targets("sudo apk add vim"), ["vim"])

    def test_upgrades_are_not_installs(self):
        self.assertIsNone(AVA._install_targets("sudo pacman -Syu"))

    def test_chains_and_other_commands_are_rejected(self):
        for cmd in [
            "sudo apt-get install -y git && rm -rf x",
            "sudo apt-get install -y git; echo done",
            "ls -la",
            "sudo apt-get update",
        ]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(AVA._install_targets(cmd))

    def test_suspicious_package_names_are_rejected(self):
        self.assertIsNone(AVA._install_targets("sudo apt-get install -y $(whoami)"))


class SearchCommandTests(unittest.TestCase):
    def test_search_tools_detected(self):
        for cmd in ["grep foo file.txt", "ps aux | grep nginx", "sudo grep -r x /etc", "diff a b"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(AVA._is_search_command(cmd))

    def test_other_commands_not_search(self):
        for cmd in ["ls -la", "cat file.txt | sort", "grep foo f && ls"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(AVA._is_search_command(cmd))


class InteractiveDetectionTests(unittest.TestCase):
    def test_interactive_programs(self):
        for cmd in ["htop", "vim notes.txt", "nano x", "ssh user@host", "python3", "sudo less /var/log/syslog"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(AVA._is_interactive(cmd))

    def test_non_interactive(self):
        for cmd in ["ls -la", "df -h", "echo hi", "sudo apt-get install -y git"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(AVA._is_interactive(cmd))


class KnownFixTests(unittest.TestCase):
    def test_permission_denied_suggests_sudo(self):
        fix = AVA._known_fix("cat /etc/shadow", "cat: /etc/shadow: Permission denied")
        self.assertEqual(fix[1], "sudo cat /etc/shadow")

    def test_no_double_sudo(self):
        self.assertIsNone(AVA._known_fix("sudo cat /etc/shadow", "Permission denied"))

    def test_offline_has_no_replacement_command(self):
        fix = AVA._known_fix("curl x", "Could not resolve host: x")
        self.assertIsNotNone(fix)
        self.assertEqual(fix[1], "")

    def test_unknown_error(self):
        self.assertIsNone(AVA._known_fix("ls", "something odd"))


class MissingProgramsTests(unittest.TestCase):
    def test_existing_program_is_not_missing(self):
        self.assertEqual(AVA._missing_programs("ls -la"), [])

    def test_unknown_program_is_reported(self):
        self.assertEqual(AVA._missing_programs("definitely_not_a_real_tool_xyz --help"),
                         ["definitely_not_a_real_tool_xyz"])

    def test_builtins_and_variables_are_skipped(self):
        self.assertEqual(AVA._missing_programs("cd /tmp && echo $HOME"), [])

    def test_looks_past_sudo_and_env_prefixes(self):
        self.assertEqual(AVA._missing_programs("sudo FOO=1 nonexistent_tool_abc"), ["nonexistent_tool_abc"])

    def test_each_pipeline_segment_is_checked(self):
        self.assertEqual(AVA._missing_programs("ls | nonexistent_tool_abc"), ["nonexistent_tool_abc"])


class PackageManagerTests(unittest.TestCase):
    def test_detects_first_available_manager(self):
        def fake_which(name):
            return "/usr/bin/" + name if name in ("dnf", "pacman") else None

        with mock.patch.object(AVA.shutil, "which", side_effect=fake_which):
            self.assertEqual(AVA.detect_package_manager(), "dnf")  # earlier in LINUX_MANAGERS

    def test_none_when_nothing_found(self):
        with mock.patch.object(AVA.shutil, "which", return_value=None):
            self.assertIsNone(AVA.detect_package_manager())

    def test_display_name(self):
        self.assertEqual(AVA._pm_display_name("apt-get"), "apt")
        self.assertEqual(AVA._pm_display_name("pacman"), "pacman")

    def test_package_install_command_uses_sudo_when_not_root(self):
        with mock.patch.object(AVA.shutil, "which", side_effect=lambda n: "/x" if n == "apt-get" else None), \
                mock.patch.object(AVA, "is_admin", return_value=False):
            self.assertEqual(AVA._package_install_cmd("htop"), ["sudo", "apt-get", "install", "-y", "htop"])

    def test_package_install_command_rejects_bad_names(self):
        self.assertIsNone(AVA._package_install_cmd("htop; rm -rf /"))


class SystemPromptTests(unittest.TestCase):
    """The prompt is the only guard against the model answering chat requests with echo.
    These tests cannot prove the model obeys it, only that the rules are present."""

    def test_forbids_echo_for_jokes_and_chat(self):
        prompt = AVA._system_prompt("tell me a joke")
        self.assertIn("never use echo or printf to tell a joke", prompt)

    def test_still_allows_empty_command_for_non_terminal_tasks(self):
        prompt = AVA._system_prompt("tell me a joke")
        self.assertIn('set "command" to ""', prompt)
        self.assertIn("not a terminal task", prompt)

    def test_requires_json_only_reply(self):
        self.assertIn("single JSON object", AVA._system_prompt(""))

    def test_math_hint_still_uses_shell_arithmetic(self):
        # The echo rule must not conflict with the arithmetic hint.
        prompt = AVA._system_prompt("what is 10 % 2")
        self.assertIn("echo $((10 % 2))", prompt)
        self.assertIn("echo may only print something the shell computes", prompt)

    def test_unrelated_hints_are_not_included(self):
        self.assertNotIn("lscpu", AVA._system_prompt("tell me a joke"))


class ToolSpecTests(unittest.TestCase):
    """Every tool in the menu must have install steps for every supported manager."""

    def test_all_specs_cover_all_linux_managers(self):
        for spec in AVA.TOOL_SPECS:
            for pm in AVA.LINUX_MANAGERS:
                with self.subTest(tool=spec.name, pm=pm):
                    self.assertIn(pm, spec.install_cmds)
                    for step in spec.install_cmds[pm]:
                        self.assertEqual(step[0], pm)

    def test_only_supported_managers_are_defined(self):
        for spec in AVA.TOOL_SPECS:
            self.assertEqual(set(spec.install_cmds), set(AVA.LINUX_MANAGERS))

    def test_names_are_unique(self):
        names = [s.name for s in AVA.TOOL_SPECS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
