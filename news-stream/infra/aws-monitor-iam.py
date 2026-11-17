#!/usr/bin/env python3
"""aws-monitor-iam.py: the monitor's read-only IAM user in YYC's AWS account 551897326524 (monitor.bal_aws).

IAM user `yyc-monitor` with one inline policy, `yyc-monitor-read`: ce:GetCostAndUsage (the account's own billed cost,
Cost Explorer) and ec2:DescribeInstances (the instances now). Nothing else: it changes nothing and reads nothing else.

  infra/aws-monitor-iam.py apply            make the user and (re)write its policy; safe to run again
  infra/aws-monitor-iam.py key DIR          a new access key, written to DIR/id and DIR/secret (mode 600), never printed;
                                            into the store `yyc` as YYC_AWS_MONITOR_ACCESS_KEY_ID / _SECRET_ACCESS_KEY
                                            (claude-env scripts/store-put.py), then `infra/seal.py yyc/monitor-keys
                                            yyc/monitor-keys@talos`. Refused while the user already has two keys
  infra/aws-monitor-iam.py check            the user's policy and how many keys it has (dates only)
  infra/aws-monitor-iam.py drop-key DIR     delete the access key whose id is in DIR/id (after a rotation)

Runs as YYC's admin: env YYC_AWS_ACCESS_KEY_ID / YYC_AWS_SECRET_ACCESS_KEY (store `yyc`), e.g.
  session-secret --keys YYC_AWS_ACCESS_KEY_ID,YYC_AWS_SECRET_ACCESS_KEY --exec infra/aws-monitor-iam.py apply
Standard library only (IAM's Query API, Signature Version 4): no AWS CLI needed.
"""
import hashlib, hmac, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

USER, POLICY = "yyc-monitor", "yyc-monitor-read"
DOCUMENT = {"Version": "2012-10-17", "Statement": [
    {"Sid": "OwnCost", "Effect": "Allow", "Action": "ce:GetCostAndUsage", "Resource": "*"},
    {"Sid": "InstancesNow", "Effect": "Allow", "Action": "ec2:DescribeInstances", "Resource": "*"}]}


def _h(k, s):
    return hmac.new(k, s.encode(), hashlib.sha256).digest()


def iam(action, ok=(), **params):
    """one IAM call → the response XML; an error code in `ok` is returned as "" instead of raised"""
    ak, sk = os.environ["YYC_AWS_ACCESS_KEY_ID"], os.environ["YYC_AWS_SECRET_ACCESS_KEY"]
    host, region, service = "iam.amazonaws.com", "us-east-1", "iam"
    body = urllib.parse.urlencode(dict(params, Action=action, Version="2010-05-08")).encode()
    t = datetime.now(timezone.utc)
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    h = {"content-type": "application/x-www-form-urlencoded; charset=utf-8", "host": host, "x-amz-date": amz}
    names = sorted(h)
    canon = "\n".join(["POST", "/", "", "".join(f"{n}:{h[n]}\n" for n in names), ";".join(names),
                       hashlib.sha256(body).hexdigest()])
    scope = f"{day}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canon.encode()).hexdigest()])
    key = _h(_h(_h(_h(("AWS4" + sk).encode(), day), region), service), "aws4_request")
    h["authorization"] = (f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={';'.join(names)}, "
                          f"Signature={hmac.new(key, sts.encode(), hashlib.sha256).hexdigest()}")
    h.pop("host")
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://{host}/", data=body, method="POST", headers=h),
                                    timeout=30) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        x = e.read().decode("utf-8", "replace")
        code = (re.search(r"<Code>([^<]+)</Code>", x) or [None, ""])[1]
        if code in ok:
            return ""
        sys.exit(f"IAM {action}: HTTP {e.code} {code}: {(re.search(r'<Message>([^<]+)</Message>', x) or [None, ''])[1]}")


def keys():
    x = iam("ListAccessKeys", UserName=USER)
    return list(zip(re.findall(r"<AccessKeyId>([^<]+)</AccessKeyId>", x), re.findall(r"<CreateDate>([^<]+)</CreateDate>", x)))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "apply":
        made = iam("CreateUser", ok=("EntityAlreadyExists",), UserName=USER, **{
            "Tags.member.1.Key": "purpose", "Tags.member.1.Value": "yyc-monitor: the news stream monitor reads cost and instances"})
        iam("PutUserPolicy", UserName=USER, PolicyName=POLICY, PolicyDocument=json.dumps(DOCUMENT))
        print(f"user {USER}: {'made' if made else 'already there'}; policy {POLICY} written")
    elif cmd == "key" and len(sys.argv) == 3:
        have = keys()
        if len(have) >= 2:
            sys.exit(f"user {USER} already has two access keys (made {', '.join(d for _, d in have)}): drop one first")
        x = iam("CreateAccessKey", UserName=USER)
        os.makedirs(sys.argv[2], mode=0o700, exist_ok=True)
        for name, tag in (("id", "AccessKeyId"), ("secret", "SecretAccessKey")):
            path = os.path.join(sys.argv[2], name)
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
                f.write(re.search(rf"<{tag}>([^<]+)</{tag}>", x)[1])
        print(f"a new access key of {USER} is in {sys.argv[2]}/id and /secret ({len(have) + 1} keys now)")
    elif cmd == "check":
        x = iam("GetUserPolicy", UserName=USER, PolicyName=POLICY)
        doc = json.loads(urllib.parse.unquote(re.search(r"<PolicyDocument>([^<]+)</PolicyDocument>", x)[1]))
        print(f"user {USER}, policy {POLICY}: {sorted(s['Action'] for s in doc['Statement'])}"
              f"{'' if doc == DOCUMENT else '  (differs from this script: run apply)'}")
        print(f"access keys: {len(keys())} (made {', '.join(d for _, d in keys()) or '-'})")
    elif cmd == "drop-key" and len(sys.argv) == 3:
        iam("DeleteAccessKey", UserName=USER, AccessKeyId=open(os.path.join(sys.argv[2], "id")).read().strip())
        print(f"deleted; {len(keys())} keys now")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
