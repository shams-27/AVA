"""Unit tests for AVA's pure logic: the command risk classifier, reply parsing,
command post-processing and package-manager detection. Standard library only.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import contextlib
import io
import os
import re
import subprocess
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


_ANSI = re.compile(r"\x1b\[[0-9?;]*[A-Za-z]")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _capture(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return _ANSI.sub("", buf.getvalue())


def _render_menu(cols, rows, cursor=0, selected=None, ai="offline"):
    """Renders the menu as plain text for a terminal of the given size."""
    items = [e.name for e in AVA.MENU_ITEMS]
    selected = selected or [False] * len(items)
    avail = [(e.name, k % 2 == 0) for k, e in enumerate(e for e in AVA.MENU_ITEMS if e.check_bin)]
    AVA._AI_STATUS_CACHE["state"] = ai
    try:
        with mock.patch.object(AVA, "_term_width", return_value=cols), \
                mock.patch.object(AVA, "_term_height", return_value=rows), \
                mock.patch.object(AVA, "_tool_availability", return_value=avail), \
                mock.patch.object(AVA, "_cached_version", return_value="v1.2.3"):
            return _capture(AVA.render_full_menu, items, selected, cursor)
    finally:
        AVA._AI_STATUS_CACHE.clear()


class MenuLayoutTests(unittest.TestCase):
    """The menu must stay readable on small and narrow terminals."""

    WIDTHS = (34, 40, 50, 64, 80, 120)
    HEIGHTS = (12, 14, 16, 20, 24, 30, 40)

    def test_every_line_fits_the_terminal_width(self):
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                for line in _render_menu(cols, 40).split("\n"):
                    self.assertLessEqual(len(line), cols, line)

    def test_borders_line_up_at_every_width(self):
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                lines = [ln for ln in _render_menu(cols, 40).split("\n") if ln.strip()]
                self.assertEqual(len({len(ln.rstrip()) for ln in lines}), 1, cols)

    def test_never_taller_than_the_terminal(self):
        for rows in self.HEIGHTS:
            with self.subTest(rows=rows):
                self.assertLessEqual(_render_menu(80, rows).count("\n"), rows - 1)

    def test_highlighted_row_is_always_visible(self):
        n = len(AVA.MENU_ITEMS)
        for rows in (12, 16, 24, 40):
            for cursor in range(n):
                with self.subTest(rows=rows, cursor=cursor):
                    text = _render_menu(80, rows, cursor=cursor)
                    self.assertIn(AVA.ICON_PROMPT, text)
                    self.assertIn(AVA.MENU_ITEMS[cursor].name[:4], text.split(AVA.ICON_PROMPT)[1].split("\n")[0])

    def test_scroll_indicators_show_hidden_items(self):
        up = re.escape(AVA.ARROW_UP) + r" \d+ more"
        down = re.escape(AVA.ARROW_DOWN) + r" \d+ more"
        top = _render_menu(80, 12, cursor=0)
        self.assertRegex(top, down)
        self.assertNotRegex(top, up)
        bottom = _render_menu(80, 12, cursor=len(AVA.MENU_ITEMS) - 1)
        self.assertRegex(bottom, up)
        self.assertNotRegex(bottom, down)

    def test_everything_is_shown_when_it_fits(self):
        text = _render_menu(80, 40)
        for entry in AVA.MENU_ITEMS:
            self.assertIn(entry.name, text)
        self.assertNotRegex(text, r"\d+ more")

    def test_selected_task_names_are_listed_in_the_footer(self):
        selected = [False] * len(AVA.MENU_ITEMS)
        selected[2] = selected[3] = True
        text = _render_menu(80, 40, selected=selected)
        self.assertIn("2 tasks selected: Go, Rust", text)

    def test_ask_ava_shows_ai_status_and_tip(self):
        last = len(AVA.MENU_ITEMS) - 1
        self.assertIn("AI ready", _render_menu(80, 40, ai="ready"))
        self.assertIn("AI offline", _render_menu(80, 40, ai="offline"))
        self.assertIn("no model", _render_menu(80, 40, ai="no model"))
        self.assertIn("Describe a task in plain English", _render_menu(80, 40, cursor=last))
        self.assertNotIn("Describe a task in plain English", _render_menu(80, 40, cursor=0))

    def test_narrow_menu_uses_one_wording_for_missing_tools(self):
        text = _render_menu(32, 40)
        self.assertIn("missing", text)
        self.assertNotIn("not installed", text)

    def test_wide_menu_keeps_full_wording(self):
        text = _render_menu(100, 40)
        self.assertIn("not installed", text)
        self.assertIn("v1.2.3", text)


class LayoutHelperTests(unittest.TestCase):
    def test_box_width_adapts_to_terminal(self):
        for cols, expected in [(120, 64), (66, 64), (50, 48), (20, 30)]:
            with self.subTest(cols=cols), mock.patch.object(AVA, "_term_width", return_value=cols):
                self.assertEqual(AVA._box_w(), expected)

    def test_pick_returns_first_candidate_that_fits(self):
        self.assertEqual(AVA._pick(["long text here", "short"], 20), "long text here")
        self.assertEqual(AVA._pick(["long text here", "short"], 10), "short")
        self.assertEqual(AVA._pick(["long text here", "still long"], 4), "still long")

    def test_menu_window_keeps_focus_visible(self):
        self.assertEqual(AVA._menu_window(10, 5, 20), (0, 10))
        for focus in range(20):
            with self.subTest(focus=focus):
                start, end = AVA._menu_window(20, focus, 8)
                self.assertEqual(end - start, 8)
                self.assertTrue(start <= focus < end)
                self.assertTrue(0 <= start and end <= 20)

    def test_selection_summary_singular_and_plural(self):
        self.assertIn("1 task selected: Go", AVA._selection_summary(["Go"], 62)[0])
        self.assertIn("2 tasks selected: Go, Rust", AVA._selection_summary(["Go", "Rust"], 62)[0])

    def test_selection_summary_shrinks_to_fit(self):
        names = ["Node.js & npm", "OpenJDK", "Go", "Rust"]
        for width in (62, 40, 30, 20):
            with self.subTest(width=width):
                plain = AVA._selection_summary(names, width)[0]
                self.assertLessEqual(len(plain) + 1, width)
        self.assertEqual(AVA._selection_summary(names, 14)[0].strip(), "4 selected")
        self.assertIn("Node.js & npm +3 more", AVA._selection_summary(names, 40)[0])


class AiStatusTests(unittest.TestCase):
    def setUp(self):
        AVA._AI_STATUS_CACHE.clear()

    def tearDown(self):
        AVA._AI_STATUS_CACHE.clear()

    def check(self, reply):
        AVA._AI_STATUS_CACHE.clear()
        with mock.patch.object(AVA, "_ollama_get", return_value=reply), \
                mock.patch.object(AVA, "_model_name", return_value="m:1"):
            return AVA._ai_state(), AVA._ai_status()

    def test_offline_when_server_does_not_answer(self):
        state, (variants, tone) = self.check(None)
        self.assertEqual((state, tone), ("offline", "warn"))
        self.assertIn("AI offline", variants)

    def test_no_model_when_server_has_other_models(self):
        state, (_, tone) = self.check({"models": [{"name": "other:1"}]})
        self.assertEqual((state, tone), ("no model", "warn"))

    def test_ready_when_model_is_present(self):
        state, (variants, tone) = self.check({"models": [{"name": "m:1"}]})
        self.assertEqual((state, tone), ("ready", "ok"))
        self.assertTrue(any("AI ready" in v for v in variants))

    def test_result_is_cached_for_the_menu_session(self):
        with mock.patch.object(AVA, "_ollama_get", return_value=None) as get:
            AVA._ai_state(); AVA._ai_state(); AVA._ai_state()
        self.assertEqual(get.call_count, 1)


class AsciiFallbackTests(unittest.TestCase):
    def test_menu_is_pure_ascii_when_terminal_is_not_utf8(self):
        code = (
            "import io, contextlib, sys\n"
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "import AVA\nfrom unittest import mock\n"
            "AVA._AI_STATUS_CACHE['state'] = 'ready'\n"
            "with mock.patch.object(AVA, '_term_width', return_value=80), \\\n"
            "     mock.patch.object(AVA, '_term_height', return_value=40), \\\n"
            "     mock.patch.object(AVA, '_cached_version', return_value='v1'):\n"
            "    buf = io.StringIO()\n"
            "    with contextlib.redirect_stdout(buf):\n"
            "        AVA.render_full_menu([e.name for e in AVA.MENU_ITEMS], [True] + [False] * 9, 0)\n"
            "        AVA.render_intro_box()\n"
            "out = buf.getvalue()\n"
            "bad = sorted({c for c in out if ord(c) > 127})\n"
            "print('ASCII' if not bad else 'NON-ASCII ' + repr(bad))\n"
            "print('BOXES' if '[x]' in out and '[ ]' in out else 'NO-BOXES')\n"
        )
        env = dict(os.environ, PYTHONIOENCODING="ascii")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        self.assertEqual(result.stdout.split(), ["ASCII", "BOXES"], result.stdout + result.stderr)


class NarrowBoxTests(unittest.TestCase):
    def test_other_boxes_shrink_to_a_narrow_terminal(self):
        with mock.patch.object(AVA, "_term_width", return_value=40):
            boxed = _capture(AVA._print_boxed, "Suggested command", [("echo " + "word " * 30, ""), ("short", "")], "")
            output = _capture(AVA._print_output_box, "line one\n" + "x" * 120 + "\nline three", True)
            intro = _capture(AVA.render_intro_box)
        for name, text in (("boxed", boxed), ("output", output), ("intro", intro)):
            with self.subTest(box=name):
                self.assertTrue(text.strip())
                for line in text.split("\n"):
                    self.assertLessEqual(len(line), 40, line)

    def test_intro_title_is_shortened_when_narrow(self):
        with mock.patch.object(AVA, "_term_width", return_value=36):
            text = _capture(AVA.render_intro_box)
        self.assertIn(f"v{AVA.AVA_VERSION}", text)
        self.assertNotIn("Terminal Assistant", text)


if __name__ == "__main__":
    unittest.main()
