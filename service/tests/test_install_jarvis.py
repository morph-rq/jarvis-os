"""install-jarvis.sh and the `jarvis` command (omarchy/bin/jarvis, omarchy/lib/jarvis-common.sh), run as an
ordinary user against stand-ins for pacman, sudo, lspci, nvidia-smi, PipeWire, Hyprland, systemctl, npm and the
inner installer (omarchy/install.sh, which needs a real Omarchy). What's checked: dry runs change nothing,
packages go through `pacman -S --needed` only, the hardware report reads an RTX 5090 correctly and never
touches drivers, rollback points, backups without secrets, restoring a backup and a Windows export, and
rolling an install back."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETPRIV = shutil.which("setpriv")
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash") or not shutil.which("git"),
                                reason="needs bash and git")

LSPCI_5090 = """01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GB202 [GeForce RTX 5090] [10de:2b85] (rev a1)
\tSubsystem: NVIDIA Corporation Device [10de:16fa]
\tKernel driver in use: nvidia
\tKernel modules: nouveau, nvidia_drm, nvidia
02:00.0 Audio device [0403]: NVIDIA Corporation Device [10de:22e8] (rev a1)
\tKernel driver in use: snd_hda_intel
"""
LSPCI_NOUVEAU = LSPCI_5090.replace("Kernel driver in use: nvidia\n", "Kernel driver in use: nouveau\n", 1)

STUBS = {
    "pacman": r"""
case "$1" in
  -Q|-Qq) grep -qx "$2" "$STUB/installed" ;;   # a query: not logged (it changes nothing)
  -S) log "$@"; shift; for p in "$@"; do [[ $p == -* ]] || echo "$p" >> "$STUB/installed"; done ;;
  -Rns) log "$@"; shift; for p in "$@"; do grep -vx "$p" "$STUB/installed" > "$STUB/i2"; mv "$STUB/i2" "$STUB/installed"; done ;;
  *) log "$@" ;;
esac""",
    "sudo": 'log "$@"; exec "$@"',
    "lspci": 'cat "$STUB/lspci"',
    "nvidia-smi": 'echo "575.64.05"',
    "pactl": r"""
case "$1" in
  info) echo "Server Name: PulseAudio (on PipeWire 1.4.7)" ;;
  get-default-sink) echo alsa_output.usb-Focusrite_Scarlett-00.analog-stereo ;;
  get-default-source) echo alsa_input.usb-Shure_MV7-00.mono-fallback ;;
  list) printf '50\talsa_input.usb-Shure_MV7-00.mono-fallback\tPipeWire\n51\talsa_output.usb-Focusrite_Scarlett-00.analog-stereo.monitor\tPipeWire\n' ;;
esac""",
    "hyprctl": 'echo "Hyprland 0.50.1 built from branch"',
    "systemctl": 'log "$@"; [[ "$*" == *is-enabled* ]] && exit 1; exit 0',
    "omarchy": 'log "$@"',
    "npm": r"""log "$@"
for p in "$@"; do
  case $p in @anthropic-ai/claude-code) printf '#!/bin/sh\necho 2.0.0' > "$HOME/.local/bin/claude"; chmod +x "$HOME/.local/bin/claude" ;;
             @openai/codex) printf '#!/bin/sh\necho 0.40.0' > "$HOME/.local/bin/codex"; chmod +x "$HOME/.local/bin/codex" ;; esac
done""",
    "inner-install": r"""log "$@"
mkdir -p "$HOME/.local/share/jarvis-workos/venv/bin" "$HOME/.config/jarvis-workos" "$HOME/.local/bin"
printf '#!/bin/sh\nexit 0\n' > "$HOME/.local/share/jarvis-workos/venv/bin/python"; chmod +x "$HOME/.local/share/jarvis-workos/venv/bin/python"
[ -f "$HOME/.config/jarvis-workos/config.toml" ] || echo '[voice]' > "$HOME/.config/jarvis-workos/config.toml"
printf '#!/bin/sh\necho "{\\"GEMINI_API_KEY\\": \\"missing\\"}"\n' > "$HOME/.local/bin/jarvis-workos"; chmod +x "$HOME/.local/bin/jarvis-workos"
""",
}


@pytest.fixture
def box():
    """A user home, a copy of the checkout (with uncommitted edits) as its own git repo, and the stand-ins, all
    owned by an ordinary user (the installer refuses to run as root)."""
    if os.geteuid() == 0 and not SETPRIV:
        pytest.skip("needs setpriv to run as an ordinary user")
    tmp_path = Path(tempfile.mkdtemp(prefix="jarvis-install-test-"))
    os.chmod(tmp_path, 0o755)
    home, stub, stubbin, repo = (tmp_path / n for n in ("home", "stub", "stubbin", "repo"))
    for d in (home, stub, stubbin):
        d.mkdir()
    files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard"], capture_output=True,
                           text=True, check=True).stdout.split()
    for f in files:
        if f.startswith(("install-jarvis.sh", "omarchy/lib/", "omarchy/bin/", "omarchy/caelestia/install-caelestia.sh",
                         "omarchy/uninstall.sh")):
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / f, repo / f)
    (stub / "installed").write_text("python\ngit\npipewire\nchromium\n")
    (stub / "lspci").write_text(LSPCI_5090)
    for name, body in STUBS.items():
        p = stubbin / name
        p.write_text(f'#!/usr/bin/env bash\nlog() {{ echo "{name} $*" >> "$STUB/calls"; }}\n{body}\n')
        p.chmod(0o755)
    (stubbin / "omarchy-uninstall").write_text("")
    os.makedirs(home / ".local/bin")
    if os.geteuid() == 0:
        for root, dirs, fs in os.walk(tmp_path):
            for n in [root, *(os.path.join(root, x) for x in dirs + fs)]:
                os.chown(n, 65534, 65534)
    env = {"HOME": str(home), "STUB": str(stub), "LANG": "C.UTF-8", "TERM": "dumb",
           "PATH": f"{home}/.local/bin:{stubbin}:/usr/bin:/bin", "JARVIS_INNER_INSTALLER": str(stubbin / "inner-install"),
           "GIT_CONFIG_NOSYSTEM": "1", "XDG_RUNTIME_DIR": str(tmp_path / "run")}
    b = {"home": home, "stub": stub, "repo": repo, "env": env, "stubbin": stubbin}
    run(b, "git", "init", "-q", str(repo))
    run(b, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    run(b, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    yield b
    shutil.rmtree(tmp_path, ignore_errors=True)


def run(b, *argv, check=True):
    pre = [SETPRIV, "--reuid=65534", "--regid=65534", "--clear-groups"] if os.geteuid() == 0 else []
    r = subprocess.run([*pre, *argv], env=b["env"], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise AssertionError(f"{argv} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def own(b) -> None:
    """Files a test wrote as root belong to the test user too."""
    if os.geteuid() == 0:
        for root, dirs, fs in os.walk(b["home"].parent):
            for n in [root, *(os.path.join(root, x) for x in dirs + fs)]:
                os.chown(n, 65534, 65534)


def install(b, *args, check=True):
    own(b)
    return run(b, "bash", str(b["repo"] / "install-jarvis.sh"), *args, check=check)


def calls(b) -> list[str]:
    p = b["stub"] / "calls"
    return p.read_text().splitlines() if p.exists() else []


def test_a_dry_run_reports_and_changes_nothing(box):
    r = install(box, "--dry-run")
    out = r.stdout
    assert "GeForce RTX 5090" in out and "NVIDIA driver 575.64.05" in out
    assert "alsa_input.usb-Shure_MV7-00.mono-fallback" in out and "Microphones: 1" in out   # monitors aren't mics
    assert "[dry-run] sudo pacman -S --needed" in out and "-Sy" not in out
    assert "[dry-run] bash" in out and "--dev" in out
    assert "nothing was changed" in out
    assert calls(box) == []                                      # no stand-in was asked to change anything
    assert not (box["home"] / ".local/state/jarvis-workos").exists()
    assert not (box["home"] / ".local/bin/jarvis").exists()


def test_install_packages_tools_rollback_point_and_the_jarvis_command(box):
    cfg = box["home"] / ".config/jarvis-workos"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('[voice]\nvoice = "Puck"\n')
    (cfg / "secrets.env").write_text("GEMINI_API_KEY=AIza-not-a-real-key\n")
    data = box["home"] / ".local/share/jarvis-workos"
    (data / "venv/bin").mkdir(parents=True)
    (data / "venv/bin/python").write_text("#!/bin/sh\n")
    (data / "venv/bin/python").chmod(0o755)
    (data / "memory.db").write_text("facts")
    (data / "venv/big").write_text("x" * 1000)

    r = install(box, "--yes")
    c = calls(box)
    pacman = [x for x in c if x.startswith("pacman -S ")]
    assert len(pacman) == 1 and "--needed" in pacman[0] and "--noconfirm" in pacman[0] and " -Sy" not in " ".join(c)
    assert "nodejs" in pacman[0] and "at-spi2-core" in pacman[0] and "chromium" not in pacman[0]   # only what's missing
    assert any(x.startswith("npm install --global --prefix") and "@anthropic-ai/claude-code" in x and "@openai/codex" in x
               for x in c)
    assert any(x.startswith("inner-install") and "--dev" in x for x in c)
    jarvis = box["home"] / ".local/bin/jarvis"
    assert os.readlink(jarvis) == str(box["repo"] / "omarchy/bin/jarvis")
    assert os.readlink(box["home"] / ".local/bin/jarvis-os") == str(box["repo"] / "omarchy/bin/jarvis")
    assert (cfg / "checkout").read_text().strip() == str(box["repo"])
    assert "no Gemini API key yet" in r.stdout

    gens = sorted((box["home"] / ".local/state/jarvis-workos/generations").iterdir())
    assert len(gens) == 1
    env = (gens[0] / "install.env").read_text()
    head = run(box, "git", "-C", str(box["repo"]), "rev-parse", "HEAD").stdout.strip()
    assert f"commit={head}" in env and "installed=yes" in env and "options=--yes" in env
    assert "nodejs" in (gens[0] / "packages-added.txt").read_text()
    with tarfile.open(gens[0] / "backup.tar.gz") as t:
        names = t.getnames()
        config = tarfile.open(fileobj=t.extractfile("backup/config.tar"))
        data_tar = tarfile.open(fileobj=t.extractfile("backup/data.tar"))
        assert "./config.toml" in config.getnames() and "./secrets.env" not in config.getnames()
        assert "./memory.db" in data_tar.getnames() and not any(n.startswith("./venv") for n in data_tar.getnames())
    assert "backup/manifest.json" in names


def test_another_jarvis_command_is_left_alone(box):
    other = box["stubbin"] / "jarvis"
    other.write_text("#!/bin/sh\necho someone else's jarvis\n")
    other.chmod(0o755)
    r = install(box, "--yes", "--no-dev-tools")
    assert "another 'jarvis' already exists" in r.stdout
    assert not (box["home"] / ".local/bin/jarvis").exists() and (box["home"] / ".local/bin/jarvis-os").is_symlink()


def test_hey_jarvis_own_jarvis_file_is_never_overwritten(box):
    mine = box["home"] / ".local/bin/jarvis"                      # Hey Jarvis's launcher
    mine.write_text("#!/bin/sh\necho hey jarvis\n")
    box["env"]["PATH"] = box["env"]["PATH"].replace(f'{box["home"]}/.local/bin:', "")   # not on PATH yet
    r = install(box, "--yes", "--no-dev-tools")
    assert "another 'jarvis' already exists" in r.stdout and mine.read_text() == "#!/bin/sh\necho hey jarvis\n"


def test_without_the_nvidia_driver_it_says_so_and_changes_nothing(box):
    (box["stub"] / "lspci").write_text(LSPCI_NOUVEAU)
    r = install(box, "--dry-run")
    assert "driver in use: nouveau, not NVIDIA's" in r.stdout and "nvidia-open-dkms 570+" in r.stdout
    assert not any("nvidia" in x for x in calls(box))


def test_it_refuses_a_checkout_on_a_usb_or_windows_file_system(box, monkeypatch):
    lib = box["repo"] / "omarchy/lib/jarvis-common.sh"
    r = run(box, "bash", "-c", f'. "{lib}"; jw_fs_type() {{ echo vfat; }}; jw_check_checkout_fs /media/usb/jarvis',
            check=False)
    assert r.returncode != 0 and "Copy it into your Linux home first" in r.stderr


def test_backup_restore_and_a_windows_export(box):
    install(box, "--yes", "--no-dev-tools")
    jarvis = str(box["home"] / ".local/bin/jarvis")
    cfg = box["home"] / ".config/jarvis-workos"
    (cfg / "config.toml").write_text('[voice]\nvoice = "Charon"\n')
    (cfg / "secrets.env").write_text("GEMINI_API_KEY=AIza-not-a-real-key\n")
    out = box["home"] / "b.tar.gz"
    own(box)
    r = run(box, jarvis, "backup", "--out", str(out))
    assert "no keys or sign-ins in it" in r.stdout and oct(out.stat().st_mode)[-3:] == "600"

    (cfg / "config.toml").write_text('[voice]\nvoice = "Kore"\n')
    own(box)
    r = run(box, jarvis, "restore", str(out))
    assert (cfg / "config.toml").read_text() == '[voice]\nvoice = "Charon"\n'
    aside = next((box["home"] / ".local/state/jarvis-workos").glob("pre-restore-*"))
    assert (aside / "config/config.toml").read_text() == '[voice]\nvoice = "Kore"\n'   # set aside, not deleted
    assert (cfg / "secrets.env").exists()                                          # a restore keeps the keys you have

    # A Windows export (scripts/export-windows.ps1), with PowerShell 5.1's backslash paths.
    zpath = box["home"] / "win.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("backup\\config\\config.toml", '[voice]\nvoice = "Aoede"\n')
        z.writestr("backup\\data\\memory.db", "windows facts")
        z.writestr("backup\\keys-to-re-enter.txt", "GEMINI_API_KEY\r\nTELEGRAM_BOT_TOKEN\r\n")
        z.writestr("backup\\manifest.json", json.dumps({"source": "windows", "with_secrets": False}, separators=(",", ":")))
    own(box)
    r = run(box, jarvis, "restore", str(zpath))
    assert (cfg / "config.toml").read_text() == '[voice]\nvoice = "Aoede"\n'
    assert (box["home"] / ".local/share/jarvis-workos/memory.db").read_text() == "windows facts"
    assert "set these again: GEMINI_API_KEY TELEGRAM_BOT_TOKEN" in r.stdout

    evil = box["home"] / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("backup/manifest.json", "{}")
        z.writestr("../../escape.txt", "x")
    own(box)
    r = run(box, jarvis, "restore", str(evil), check=False)
    assert r.returncode != 0 and not (box["home"].parent / "escape.txt").exists()


def test_rollback_undoes_the_last_install(box):
    cfg = box["home"] / ".config/jarvis-workos"
    cfg.mkdir(parents=True)
    (cfg / "config.toml").write_text('[voice]\nvoice = "Puck"\n')
    install(box, "--yes", "--no-dev-tools")                     # first install (nothing installed before)
    (cfg / "config.toml").write_text('[voice]\nvoice = "Fenrir"\n')
    install(box, "--yes", "--no-dev-tools")                     # an upgrade: its rollback point has Fenrir
    (cfg / "config.toml").write_text('[voice]\nvoice = "Odin"\n')
    before = len([x for x in calls(box) if x.startswith("inner-install")])
    box["env"]["YES"] = "1"
    own(box)
    r = run(box, str(box["home"] / ".local/bin/jarvis"), "rollback")
    assert "rolled back" in r.stdout
    assert (cfg / "config.toml").read_text() == '[voice]\nvoice = "Fenrir"\n'
    assert len([x for x in calls(box) if x.startswith("inner-install")]) == before + 1   # reinstalled
    # Rolling back the very first install means uninstalling (the config is as it was before it).
    first = sorted((box["home"] / ".local/state/jarvis-workos/generations").iterdir())[0]
    r = run(box, str(box["home"] / ".local/bin/jarvis"), "rollback", first.name)
    assert (cfg / "config.toml").read_text() == '[voice]\nvoice = "Puck"\n'
    assert len([x for x in calls(box) if x.startswith("inner-install")]) == before + 1   # not reinstalled


def test_jarvis_passes_other_commands_to_jarvis_workos(box):
    install(box, "--yes", "--no-dev-tools")
    r = run(box, str(box["home"] / ".local/bin/jarvis"), "secret", "status")
    assert '"GEMINI_API_KEY": "missing"' in r.stdout


def test_doctor_reports_hardware_install_and_tools(box):
    install(box, "--yes")
    r = run(box, str(box["home"] / ".local/bin/jarvis"), "doctor", check=False)
    out = r.stdout
    assert "GeForce RTX 5090" in out and "checkout:" in out and "claude" in out and "codex" in out
    assert "island plugin not installed" in out                  # the stand-in installer doesn't make one


def test_the_transfer_package_has_the_project_and_its_history_and_no_keys(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts/make-transfer-package.sh", repo / "scripts/")
    (repo / "README.md").write_text("Jarvis\n")
    (repo / "run.sh").write_text("#!/bin/sh\necho hi\n")
    (repo / "run.sh").chmod(0o755)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "one"], check=True)
    r = subprocess.run(["bash", str(repo / "scripts/make-transfer-package.sh"), str(tmp_path / "out")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    zpath = next((tmp_path / "out").glob("jarvis-os-*.zip"))
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        top = names[0].split("/")[0]
        assert {f"{top}/jarvis/README.md", f"{top}/jarvis.bundle", f"{top}/TRANSFER.txt", f"{top}/SHA256SUMS"} <= set(names)
        assert (z.getinfo(f"{top}/jarvis/run.sh").external_attr >> 16) & 0o111     # still executable
        z.extract(f"{top}/jarvis.bundle", tmp_path)
    clone = subprocess.run(["git", "clone", "-q", str(tmp_path / top / "jarvis.bundle"), str(tmp_path / "clone")],
                           capture_output=True, text=True)
    assert clone.returncode == 0 and (tmp_path / "clone/run.sh").exists()

    (repo / "notes.txt").write_text("key AIza" + "B" * 35 + "\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "oops"], check=True)
    r = subprocess.run(["bash", str(repo / "scripts/make-transfer-package.sh"), str(tmp_path / "out2")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "looks like a key" in r.stderr and not list((tmp_path / "out2").glob("*.zip"))
