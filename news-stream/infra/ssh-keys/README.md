Public SSH keys allowed in as root on the YYC server (yyc-stream), one `<who>.pub` per Claude session or person.
`yyc-update` (every 5 min) rewrites /root/.ssh/authorized_keys = the provisioning admin key + these files, so a
key gets in by being pushed to main and out by being deleted. Only public keys belong here.
