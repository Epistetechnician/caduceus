#!/bin/bash
# Run once on the VM as the normal user (has sudo). Creates the unprivileged 'agent' user, the allowlist proxy and firewall rules.
set -e
sudo apt-get install -y -qq iptables >/dev/null
id agent >/dev/null 2>&1 || sudo useradd -m -s /bin/bash agent
sudo mkdir -p /srv/agentwork && sudo chmod 1777 /srv/agentwork
sudo install -m 755 ~/scripts/rb-run /usr/local/bin/rb-run
echo 'agent ALL=(root) NOPASSWD: /usr/local/bin/rb-run' | sudo tee /etc/sudoers.d/agent-rbrun >/dev/null
sudo chmod 440 /etc/sudoers.d/agent-rbrun
pkill -f allowlist_proxy.py || true
nohup python3 ~/scripts/allowlist_proxy.py 3128 ~/proxy-denied.log ${ALLOW:-anthropic.com claude.ai claude.com} > ~/proxy.log 2>&1 &
sudo iptables -F OUTPUT
sudo iptables -A OUTPUT -m owner --uid-owner agent -o lo -j ACCEPT
sudo iptables -A OUTPUT -m owner --uid-owner agent -j REJECT
echo SETUP_OK
