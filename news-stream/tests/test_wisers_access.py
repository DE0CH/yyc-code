#!/usr/bin/env python3
"""Who may use the WiseSearch relay (wisers/relay.py access_email, on_behalf):

    python3 tests/test_wisers_access.py

Signed with a local RSA key standing in for the Access team's: any email Access signed for this app's audience gets in
(Access already applied YYC's users list); an
Access service token gets in only when its client id (the token's common_name) is in WISERS_SERVICE_CLIENT_IDS ("client
id=label": yyc-mcp's token, 2026-10-29), and its calls are logged as the staff member it names in X-YYC-User with the
token's label; a wrong
audience, issuer, expiry or signature is refused whoever it names. yyc-mcp inside the cluster sends an internal token
(WISERS_INTERNAL_TOKENS, "sha256=label"): let in without an Access token, ignored when one comes with it (the tunnel's
traffic), refused when unlisted.
"""
import base64, json, os, sys, time, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "wisers"))
INTERNAL_TOKEN = "internal-secret-token"
os.environ.update(ACCESS_AUD="aud-wisers", WISERS_SERVICE_CLIENT_IDS="abc123.access=yyc-mcp, other.access=other",
                  WISERS_INTERNAL_TOKENS=__import__("hashlib").sha256(INTERNAL_TOKEN.encode()).hexdigest() + "=yyc-mcp")
import relay  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB = KEY.public_key().public_numbers()
relay._keys = lambda: {"k1": (PUB.n, PUB.e)}


def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def token(claims, kid="k1", key=KEY):
    h = b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    p = b64(json.dumps(claims).encode())
    sig = key.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{b64(sig)}"


def claims(**kw):
    c = {"aud": ["aud-wisers"], "iss": relay.ACCESS_TEAM, "exp": time.time() + 600}
    c.update(kw)
    return c


class Access(unittest.TestCase):
    def test_emails(self):
        self.assertEqual(relay.access_email(token(claims(email="Michelle.Yeoh@YYC.hk"))), "michelle.yeoh@yyc.hk")
        self.assertEqual(relay.access_email(token(claims(email="Tony.Leung@Example.com"))), "tony.leung@example.com")

    def test_internal_token(self):
        self.assertEqual(relay.internal_caller("Bearer " + INTERNAL_TOKEN, ""), "service:yyc-mcp")
        self.assertIsNone(relay.internal_caller("Bearer " + INTERNAL_TOKEN, "an.access.token"))  # through the tunnel
        self.assertIsNone(relay.internal_caller("Bearer wrong", ""))
        self.assertIsNone(relay.internal_caller(INTERNAL_TOKEN, ""))
        self.assertEqual(relay.on_behalf(relay.internal_caller("Bearer " + INTERNAL_TOKEN, ""), "a@yyc.com"), "a@yyc.com (yyc-mcp)")

    def test_service_tokens(self):
        self.assertEqual(relay.access_email(token(claims(common_name="abc123.access"))), "service:yyc-mcp")
        self.assertEqual(relay.access_email(token(claims(common_name="other.access"))), "service:other")
        self.assertIsNone(relay.access_email(token(claims(common_name="unknown.access"))))
        self.assertIsNone(relay.access_email(token(claims(common_name=""))))

    def test_bad_tokens_refused_for_both(self):
        for c in (claims(aud=["other"]), claims(iss="https://evil.cloudflareaccess.com"), claims(exp=time.time() - 5)):
            self.assertIsNone(relay.access_email(token(dict(c, email="michelle.yeoh@yyc.hk"))))
            self.assertIsNone(relay.access_email(token(dict(c, common_name="abc123.access"))))
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertIsNone(relay.access_email(token(claims(common_name="abc123.access"), key=other)))

    def test_who_is_logged(self):
        self.assertEqual(relay.on_behalf("service:yyc-mcp", "Staff@YYC.hk"), "staff@yyc.hk (yyc-mcp)")
        self.assertEqual(relay.on_behalf("service:yyc-mcp", "not an email"), "service:yyc-mcp (yyc-mcp)")
        self.assertEqual(relay.on_behalf("service:yyc-mcp", ""), "service:yyc-mcp (yyc-mcp)")
        # a person's own header never changes who they are
        self.assertEqual(relay.on_behalf("michelle.yeoh@yyc.hk", "someone@else.com"), "michelle.yeoh@yyc.hk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
