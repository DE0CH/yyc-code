"""S3 (Hetzner Object Storage) with the standard library: AWS Signature Version 4, path-style addressing.

Used by tiktok_video.py (Deyao, 2026-10-22: the TikTok videos of posts that pass stage 1 are kept in a bucket of
Hetzner project yyc). The edge app signs its own short-lived GET links with the same algorithm
(web-app/src/server/s3.ts); tests/test_tiktok_video.py checks both against AWS's published example.

  Client(endpoint, region, access_key, secret_key, bucket)
  .put_file(key, path, content_type)  upload one file (UNSIGNED-PAYLOAD: streamed from disk, never read whole)
  .head(key)                          its size in bytes, or None when absent
  .usage(prefix="")                   {"objects": n, "bytes": n} under a prefix (ListObjectsV2, every page)
  .presign(key, expires, params)      a GET link anyone can open until it expires (query-string signature)
  from_env()                          the Client from VIDEO_S3_ENDPOINT / _REGION / _ACCESS_KEY / _SECRET_KEY / _BUCKET
"""
import base64, hashlib, hmac, os, re, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

UNSIGNED = "UNSIGNED-PAYLOAD"
EMPTY = hashlib.sha256(b"").hexdigest()


def _q(s, safe="-_.~"):
    return urllib.parse.quote(str(s), safe=safe)


def _hmac(k, s):
    return hmac.new(k, s.encode(), hashlib.sha256).digest()


def signing_key(secret, day, region, service="s3"):
    return _hmac(_hmac(_hmac(_hmac(("AWS4" + secret).encode(), day), region), service), "aws4_request")


class Client:
    def __init__(self, endpoint, region, access_key, secret_key, bucket):
        self.endpoint, self.region = endpoint.rstrip("/"), region
        self.ak, self.sk, self.bucket = access_key, secret_key, bucket
        self.host = urllib.parse.urlsplit(self.endpoint).netloc

    def _path(self, key=""):
        """path-style /<bucket>/<key>; with bucket "" the endpoint is the bucket's own host (virtual-hosted)"""
        base = "/" + _q(self.bucket) if self.bucket else ""
        return (base + "/" + _q(key, safe="-_.~/")) if key else (base or "/")

    def _canonical_query(self, params):
        return "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(params.items()))

    def sign(self, method, key="", params=None, headers=None, payload=UNSIGNED, now=None):
        """→ (url, headers) of a request signed in its Authorization header"""
        now = now or datetime.now(timezone.utc)
        amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        h = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
        h.update({"host": self.host, "x-amz-date": amz, "x-amz-content-sha256": payload})
        names = sorted(h)
        q = self._canonical_query(params or {})
        canon = "\n".join([method, self._path(key), q, "".join(f"{n}:{h[n]}\n" for n in names), ";".join(names), payload])
        scope = f"{day}/{self.region}/s3/aws4_request"
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
        sig = hmac.new(signing_key(self.sk, day, self.region), sts.encode(), hashlib.sha256).hexdigest()
        h["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.ak}/{scope}, SignedHeaders={';'.join(names)}, "
                              f"Signature={sig}")
        h.pop("host")
        return self.endpoint + self._path(key) + ("?" + q if q else ""), h

    def presign(self, key, expires=3600, params=None, now=None):
        """a GET link valid `expires` seconds (params: e.g. response-content-disposition for a download)"""
        now = now or datetime.now(timezone.utc)
        amz, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        scope = f"{day}/{self.region}/s3/aws4_request"
        p = dict(params or {}, **{"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.ak}/{scope}",
                                  "X-Amz-Date": amz, "X-Amz-Expires": str(int(expires)), "X-Amz-SignedHeaders": "host"})
        q = self._canonical_query(p)
        canon = "\n".join(["GET", self._path(key), q, f"host:{self.host}\n", "host", UNSIGNED])
        sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
        sig = hmac.new(signing_key(self.sk, day, self.region), sts.encode(), hashlib.sha256).hexdigest()
        return f"{self.endpoint}{self._path(key)}?{q}&X-Amz-Signature={sig}"

    def _open(self, method, key="", params=None, headers=None, data=None, timeout=60):
        url, h = self.sign(method, key, params, headers)
        return urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=timeout)

    def put_file(self, key, path, content_type, extra=None, timeout=1800):
        size = os.path.getsize(path)
        hd = dict({"Content-Type": content_type, "Content-Length": str(size)}, **(extra or {}))
        with open(path, "rb") as f, self._open("PUT", key, headers=hd, data=f, timeout=timeout) as r:
            r.read()
        return size

    def head(self, key):
        try:
            with self._open("HEAD", key) as r:
                return int(r.headers.get("Content-Length") or 0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def usage(self, prefix=""):
        n = b = 0
        token = None
        while True:
            p = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                p["continuation-token"] = token
            with self._open("GET", params=p) as r:
                x = r.read().decode()
            sizes = re.findall(r"<Size>(\d+)</Size>", x)
            n += len(sizes)
            b += sum(int(s) for s in sizes)
            m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
            if "<IsTruncated>true</IsTruncated>" not in x or not m:
                return {"objects": n, "bytes": b}
            token = m.group(1)

    def request(self, method, params=None, headers=None, data=None):
        """a bucket-level call (e.g. PUT ?cors) → the response body"""
        hd = dict(headers or {})
        if data is not None:
            hd["Content-MD5"] = base64.b64encode(hashlib.md5(data).digest()).decode()
        url, h = self.sign(method, "", params, hd, payload=hashlib.sha256(data or b"").hexdigest())
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=60) as r:
            return r.read()


def from_env(env=os.environ):
    need = ["VIDEO_S3_ENDPOINT", "VIDEO_S3_ACCESS_KEY", "VIDEO_S3_SECRET_KEY", "VIDEO_S3_BUCKET"]
    if not all(env.get(k) for k in need):
        return None
    return Client(env["VIDEO_S3_ENDPOINT"], env.get("VIDEO_S3_REGION") or "fsn1", env["VIDEO_S3_ACCESS_KEY"],
                  env["VIDEO_S3_SECRET_KEY"], env["VIDEO_S3_BUCKET"])
