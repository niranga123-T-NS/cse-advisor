#!/usr/bin/env bash
# Turns a free always-on Linux machine (Oracle Cloud "Always Free" VM, an old laptop, a Raspberry Pi 4/5 with
# 64-bit Ubuntu) into this repository's own GitHub Actions runner. Jobs on your own runner cost nothing, even when
# the repository is private.
#
#   1. GitHub: repository Settings > Actions > Runners > New self-hosted runner > Linux; copy the token shown
#      after "--token" (valid for 1 hour).
#   2. On the machine:   bash setup_runner.sh <TOKEN>
#   3. GitHub: Settings > Secrets and variables > Actions > Variables > New variable: RUNNER = self-hosted
set -euo pipefail
TOKEN="${1:?usage: bash setup_runner.sh <registration token>}"
REPO_URL="https://github.com/niranga123-T-NS/cse-advisor"

sudo apt-get update -qq
sudo apt-get install -y -qq curl jq git tar poppler-utils tesseract-ocr python3-venv unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades       # security updates install themselves

id runner >/dev/null 2>&1 || sudo useradd -m -s /bin/bash runner       # a separate account with no admin rights
ARCH=$(uname -m); case "$ARCH" in x86_64) A=x64;; aarch64|arm64) A=arm64;; *) echo "unsupported $ARCH"; exit 1;; esac
VER=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/^v//')
sudo -u runner bash -c "
  set -e; mkdir -p ~/actions-runner && cd ~/actions-runner
  curl -fsSL -o r.tgz https://github.com/actions/runner/releases/download/v$VER/actions-runner-linux-$A-$VER.tar.gz
  tar xzf r.tgz && rm r.tgz
  ./config.sh --unattended --url $REPO_URL --token $TOKEN --name \$(hostname)-cse --labels self-hosted --replace
"
cd /home/runner/actions-runner && sudo ./svc.sh install runner && sudo ./svc.sh start
echo "Runner installed and started. Now set the repository variable RUNNER = self-hosted."
