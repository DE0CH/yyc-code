`label.bits`: one 848×590 label (a name tag rendered by lib/), 1 = black, rows of 106 bytes.
`label.niimbot-py.hex`: what claude-env `scripts/niimbot/niimbot.py` (`job` + `block_packets`, M3, density 1,
label type 1) sends for it, one frame per line (the 0xa7 blocks printed correctly on the M3, 2026-11-05), which
lib/print.js must match byte for byte.
