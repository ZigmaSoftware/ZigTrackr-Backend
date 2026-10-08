#!/bin/sh
set -eu

# Use port-only rules, matching the host's other application services.
if [ "$(id -u)" -ne 0 ]; then
    echo "Run on 192.168.1.128 with: sudo sh $0" >&2
    exit 1
fi

# Add the replacement rules before removing the previous LAN-specific rules.
ufw allow 1811/tcp comment ''
ufw allow 3299/tcp comment ''
ufw --force delete allow in on eno1 proto tcp from 192.168.0.0/22 to 192.168.1.128 port 1811 comment 'ZigTrackr frontend LAN'
ufw --force delete allow in on eno1 proto tcp from 192.168.0.0/22 to 192.168.1.128 port 3299 comment 'ZigTrackr backend LAN'
ufw status numbered
