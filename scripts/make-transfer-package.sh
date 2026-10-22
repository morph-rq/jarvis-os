#!/usr/bin/env bash
# Make one file to carry Jarvis to the Omarchy machine (USB stick, cloud drive, scp):
#
#   scripts/make-transfer-package.sh [OUT_DIR]      -> OUT_DIR/jarvis-os-<date>-<commit>.zip (default: dist/)
#
# Inside the zip:
#   jarvis/              the project at the current commit (committed files only: no secrets, no venvs)
#   jarvis.bundle        the whole git history, every branch: `git clone jarvis.bundle jarvis` restores the repo
#   TRANSFER.txt         the steps on the Omarchy side
#   SHA256SUMS
# Uncommitted changes are not included (commit them first; the script says so). Before packing, the files
# are scanned for things that look like API keys or private keys; any hit stops it.
set -euo pipefail

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
out_dir="$(mkdir -p "${1:-$repo/dist}" && cd "${1:-$repo/dist}" && pwd)"
commit=$(git -C "$repo" rev-parse --short HEAD)
branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD)
name="jarvis-os-$(date +%Y%m%d)-$commit"

if [[ -n $(git -C "$repo" status --porcelain --untracked-files=no) ]]; then
  echo "Note: there are uncommitted changes; the package has the last commit ($commit) only." >&2
fi

# Anything that looks like a credential in the committed files stops the packaging.
if git -C "$repo" grep -nIE 'AIza[0-9A-Za-z_-]{35}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{40,}|ghp_[A-Za-z0-9]{36}|xox[abp]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
    -- . ':!*.lock' ':!scripts/make-transfer-package.sh' ':!service/tests/*' ; then
  echo "Something above looks like a key or a private key; remove it from the repository first." >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/$name/jarvis"
git -C "$repo" archive --format=tar HEAD | tar -C "$work/$name/jarvis" -xf -
git -C "$repo" bundle create "$work/$name/jarvis.bundle" --all 2>/dev/null
cat > "$work/$name/TRANSFER.txt" <<EOF
Jarvis OS transfer package: commit $commit (branch $branch), made $(date -Iseconds)

On the Omarchy machine (MIGRATION.md has the whole guide):

  1. Copy this zip into your home folder and unpack it there (not on the USB stick itself):
       cd ~ && unzip $name.zip && cd $name
  2. Make your working copy from the bundle (full history, so you can commit and push; recommended):
       git clone jarvis.bundle ~/jarvis && cd ~/jarvis && git switch $branch
       git remote set-url origin https://github.com/morph-rq/jarvis.git   # to push to GitHub later
     (or, without git history: cp -r jarvis ~/jarvis && cd ~/jarvis)
  3. Install:
       bash install-jarvis.sh --dry-run    # see what it will do
       bash install-jarvis.sh
  4. Bring your data (optional): bash install-jarvis.sh --restore ~/jarvis-windows-export-….zip
     (made on Windows with scripts\\export-windows.ps1), or later: jarvis restore FILE
EOF
(cd "$work/$name" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
python3 - "$work" "$name" "$out_dir/$name.zip" <<'PY'
import os, sys, zipfile
work, name, out = sys.argv[1:]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, dirs, files in os.walk(os.path.join(work, name)):
        dirs.sort()
        for f in sorted(files):
            path = os.path.join(root, f)
            if os.path.islink(path):
                continue
            z.write(path, os.path.relpath(path, work))   # keeps the executable bits
PY
echo "$out_dir/$name.zip ($(du -h "$out_dir/$name.zip" | cut -f1))"
