#!/usr/bin/env bash
# Optional: Laya, a small model that answers typed decisions (choice, score, yes/no), on the CPU, as its own service.
# It needs PyTorch (~1 GB) and a ~800 MB checkpoint, so it has its own environment and PEACE stays small.
# Safe to run again.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
venv=~/.local/share/peace/laya
PATH=~/.local/bin:$PATH

[ -x "$venv/bin/python" ] || uv venv "$venv" --python 3.13 -q
uv pip install -q --python "$venv/bin/python" --torch-backend=cpu 'laya[serve]==0.4.21'
mkdir -p ~/.config/systemd/user
cp "$repo/desktop/systemd/peace-laya.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable peace-laya.service
systemctl --user restart peace-laya.service
echo "Laya a arrancar em 127.0.0.1:3920 (na primeira vez descarrega o modelo). Estado: systemctl --user status peace-laya"
