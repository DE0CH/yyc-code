#!/usr/bin/env python3
"""warp-register.py NODE: registers a Cloudflare WARP account for one Talos node (a WireGuard key serves one peer only)
and prints the node's warp0 facts as JSON {privateKey, address, peerPublicKey, endpoint}, for the yyc store key
YYC_TALOS_WARP_<NODE> (yyc-talos gen reads it). Same registration as ../../cluster/node-setup.sh. Prints the private key:
pipe it straight into store-put.py, never to a terminal."""
import base64, datetime, json, sys, urllib.request
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization as s

k = X25519PrivateKey.generate()
priv = base64.b64encode(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption())).decode()
pub = base64.b64encode(k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode()
body = {"key": pub, "install_id": "", "fcm_token": "", "model": "PC", "serial_number": "", "locale": "en_US",
        "tos": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "key_type": "curve25519", "tunnel_type": "wireguard"}
req = urllib.request.Request("https://api.cloudflareclient.com/v0a5641/reg", json.dumps(body).encode(), {
    "User-Agent": "1.1.1.1/6.38.9-5641 (Android 16.0.0)", "CF-Client-Version": "a-6.38.9-5641",
    "Content-Type": "application/json; charset=UTF-8"}, method="POST")
d = json.load(urllib.request.urlopen(req, timeout=30))["config"]
json.dump({"node": sys.argv[1], "privateKey": priv, "address": d["interface"]["addresses"]["v4"] + "/32",
           "peerPublicKey": d["peers"][0]["public_key"], "endpoint": "[2606:4700:d0::a29f:c001]:2408"}, sys.stdout)
