#!/usr/bin/env bash
# Installs the Bit Buddy input forwarder for the current user.
# See INSTALL.md for what this does and why each step is needed.
set -euo pipefail

echo "== Installing packages (needs sudo password) =="
sudo dnf install -y python3-evdev python3-xlib

echo "== Adding $USER to the 'input' group =="
if groups "$USER" | tr ' ' '\n' | grep -qx input; then
    echo "Already in the input group."
else
    sudo usermod -aG input "$USER"
    echo "Added. This only takes effect after you log out and back in."
fi

echo "== Installing daemon script =="
mkdir -p "$HOME/.local/share/bitbuddy-forwarder"
cp "$(dirname "$0")/bitbuddy_forwarder.py" "$HOME/.local/share/bitbuddy-forwarder/bitbuddy_forwarder.py"

echo "== Installing systemd user service =="
mkdir -p "$HOME/.config/systemd/user"
cp "$(dirname "$0")/bitbuddy-forwarder.service" "$HOME/.config/systemd/user/bitbuddy-forwarder.service"
systemctl --user daemon-reload
systemctl --user enable bitbuddy-forwarder.service

echo
echo "Done. Log out and back in, then it starts automatically."
echo "Check status with: systemctl --user status bitbuddy-forwarder.service"
echo "Check the log with: tail -f ~/.local/share/bitbuddy-forwarder.log"
