import re
import subprocess


def test_tracked_python_files_do_not_contain_hardcoded_fireworks_credentials():
    result = subprocess.run(
        ["git", "grep", "-n", "-I", "-E", r"FIREWORKS_API_KEY\s*[:=].*fw_[A-Za-z0-9]+", "--", "*.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, "Hardcoded Fireworks credentials must never be committed"
    assert not re.search(r"fw_[A-Za-z0-9]+", result.stdout)
