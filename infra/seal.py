#!/usr/bin/env python3
"""seal.py: seal the YYC cluster's Secrets for git (Bitnami Sealed Secrets). Prints no secret value.

  infra/seal.py NS/NAME [NS/NAME ...]   (re)seal these Secrets from infra/secrets.txt: values from the env (a session
                                        holding the store `yyc`), written to the FILE the map names
  infra/seal.py --all                   every Secret in the map (a rebuilt cluster: new controller key, new cert)
  infra/seal.py --check                 the map, the sealed/ folders and their kustomization.yaml agree; each sealed
                                        file was sealed with its folder's cert; the env holds every store key the map
                                        names (names only)
  infra/seal.py --check --names-only    the same without the env (CI: yyc-ai.yml check item 5); --allow-unsealed: a map
                                        line whose file does not exist yet, and is not listed either, is a warning
  Options: --stdin KEY   KEY's value from stdin instead of the env (one key; e.g. a value not yet in this session's env)
           --cert FILE   seal to another cert than the folder's (a rebuilt cluster's new cert, before CERTS names it;
                         fetch one with `kubectl get --raw /api/v1/namespaces/kube-system/services/sealed-secrets-controller:http/proxy/v1/cert.pem`)

Two clusters (yyc-ai/refactor DESIGN 15.9): the cert is picked by the sealed file's folder (CERTS below): the new Talos
cluster's folders get infra/sealed-secrets-talos.pem, every other folder the old cluster's infra/sealed-secrets.pem.
Each sealed file records its cert's fingerprint in its header; --check refuses a file whose recorded cert is not its
folder's (a file without the line is the old cert's: every file sealed before 2026-11-04). A map name may carry
`@talos` (NS/NAME@talos) when the same Secret name exists on both clusters.
A value `A|B?` joins store keys with commas; `?` marks an optional part (skipped when the env lacks it).
           --annotate K=V  an annotation on the resulting Secret (template metadata)

A new Secret: put its values into the store (`jarvis2 put-key yyc KEY --explain ...`), add a line to infra/secrets.txt,
run `infra/seal.py NS/NAME` (it also lists the file in the folder's kustomization.yaml), commit, push. The folders
`*/sealed/` are applied by the FENCED Flux Kustomizations (yyc-apps-secrets, yyc-aiwatch-secrets) and the new cluster's
workflows' fenced steps: SealedSecrets only.
Sealing needs only the public cert; nothing outside the cluster can unseal (scope strict: name + namespace are bound).
"""
import argparse, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "infra", "secrets.txt")
HEADER = ("# SealedSecret made by infra/seal.py from the store `yyc` (map: infra/secrets.txt). Don't edit: re-seal.\n")
OLD_CERT = "infra/sealed-secrets.pem"
TALOS_CERT = "infra/sealed-secrets-talos.pem"
# folder prefix -> cert (first match wins; anything else: OLD_CERT)
CERTS = [
    ("yyc-ai/deploy/sealed/", TALOS_CERT),
    ("news-stream/deploy/clusters/talos/", TALOS_CERT),
]
SEALED_DIRS = ("news-stream/deploy/apps/sealed", "yyc-ai/deploy/sealed",
               "news-stream/deploy/clusters/talos/sealed", "news-stream/deploy/clusters/talos/db/sealed",
               "news-stream/deploy/clusters/talos/apps/sealed", "news-stream/deploy/clusters/talos/aiwatch/sealed",
               "news-stream/deploy/clusters/talos/pg/sealed")


def cert_for(file):
    for prefix, cert in CERTS:
        if file.startswith(prefix):
            return cert
    return OLD_CERT


def fingerprint(cert):
    import hashlib
    return hashlib.sha256(open(os.path.join(ROOT, cert), "rb").read()).hexdigest()[:16]


def cert_line(cert):
    return f"# cert: {cert} sha256:{fingerprint(cert)}\n"


def recorded_cert(path):
    """the cert line of a sealed file's header: (cert, fingerprint), or the old cert's for a file without one"""
    for ln in open(path):
        if not ln.startswith("#"):
            break
        if ln.startswith("# cert: "):
            c, _, fp = ln[len("# cert: "):].strip().partition(" sha256:")
            return c, fp
    return OLD_CERT, fingerprint(OLD_CERT)


def resolve(src, env):
    """a source's value: 'text' literally, A|B? joined, or one env var; None when a required part is missing"""
    if src.startswith("'") and src.endswith("'"):
        return src[1:-1]
    parts = []
    for part in src.split("|"):
        opt = part.endswith("?")
        k = part[:-1] if opt else part
        if k in env:
            parts.append(env[k])
        elif not opt:
            return None
    return ",".join(parts)


def required_keys(src):
    if src.startswith("'"):
        return []
    return [p for p in src.split("|") if not p.endswith("?")]


def load_map():
    out = {}
    for ln in open(MAP):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        name, typ, path, *pairs = ln.split()
        keys = {}
        for p in pairs:
            k, src = p.split("=", 1)
            keys[k] = (src[:-3], True) if src.endswith("+nl") else (src, False)
        out[name] = {"type": typ, "file": path, "keys": keys}
    return out


def kustomization_add(path):
    kz = os.path.join(os.path.dirname(path), "kustomization.yaml")
    base = os.path.basename(path)
    s = open(kz).read()
    if f"  - {base}\n" in s:
        return
    if "resources: []" in s:
        s = s.replace("resources: []", f"resources:\n  - {base}")
    else:
        s = s.replace("resources:\n", f"resources:\n  - {base}\n", 1)
    open(kz, "w").write(s)


def seal(name, spec, cert, stdin_key=None, stdin_val=None, annotations=None):
    ns, n = name.split("@")[0].split("/")
    data = {}
    for k, (src, nl) in spec["keys"].items():
        v = stdin_val if k == stdin_key else resolve(src, os.environ)
        if v is None:
            sys.exit(f"{name}: {src} (for {k}) is not in the env; hold the store yyc, or pass --stdin {k}")
        data[k] = v + ("\n" if nl else "")
    cert = cert or os.path.join(ROOT, cert_for(spec["file"]))
    secret = {"apiVersion": "v1", "kind": "Secret", "type": spec["type"],
              "metadata": {"name": n, "namespace": ns, **({"annotations": annotations} if annotations else {})},
              "stringData": data}
    p = subprocess.run(["kubeseal", "--cert", cert, "--scope", "strict", "--format", "yaml"],
                       input=json.dumps(secret).encode(), capture_output=True)
    if p.returncode:
        sys.exit(f"{name}: kubeseal failed: {p.stderr.decode()[:300]}")
    path = os.path.join(ROOT, spec["file"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(HEADER + cert_line(os.path.relpath(cert, ROOT)) + p.stdout.decode())
    kustomization_add(path)
    print(f"sealed {name} ({len(data)} keys, {os.path.relpath(cert, ROOT)}) -> {spec['file']}")


def check(m, names_only=False, allow_unsealed=False):
    bad = 0
    for name, spec in m.items():
        path = os.path.join(ROOT, spec["file"])
        kzp = os.path.join(os.path.dirname(path), "kustomization.yaml")
        kz = open(kzp).read() if os.path.exists(kzp) else ""
        listed = f"  - {os.path.basename(path)}\n" in kz
        if not names_only:
            for k, (src, _) in spec["keys"].items():
                for need in required_keys(src):
                    if need not in os.environ:
                        print(f"not in the env: {need} ({name} {k})")
        if not os.path.exists(path):
            if allow_unsealed and not listed:
                print(f"::warning::not sealed yet: {spec['file']} ({name})")
            else:
                print(f"missing file: {spec['file']} ({name})"); bad += 1
            continue
        if not listed:
            print(f"not in kustomization.yaml: {spec['file']}"); bad += 1
        want = cert_for(spec["file"])
        got, fp = recorded_cert(path)
        if got != want or fp != fingerprint(want):
            print(f"wrong cert: {spec['file']} was sealed with {got} (sha256:{fp}), its folder needs {want} "
                  f"(sha256:{fingerprint(want)}): re-seal"); bad += 1
    files = {s["file"] for s in m.values()}
    for d in SEALED_DIRS:
        if not os.path.isdir(os.path.join(ROOT, d)):
            continue
        for f in sorted(os.listdir(os.path.join(ROOT, d))):
            if f != "kustomization.yaml" and f"{d}/{f}" not in files:
                print(f"not in infra/secrets.txt: {d}/{f}"); bad += 1
    print("ok" if not bad else f"{bad} problem(s)")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stdin", metavar="KEY")
    ap.add_argument("--names-only", action="store_true")
    ap.add_argument("--allow-unsealed", action="store_true")
    ap.add_argument("--cert", default=None)
    ap.add_argument("--annotate", action="append", default=[], metavar="K=V")
    a = ap.parse_args()
    m = load_map()
    if a.check:
        sys.exit(1 if check(m, a.names_only, a.allow_unsealed) else 0)
    names = list(m) if a.all else a.names
    if not names:
        ap.error("name a Secret (NS/NAME), or --all / --check")
    unknown = [n for n in names if n not in m]
    if unknown:
        sys.exit(f"not in infra/secrets.txt: {' '.join(unknown)}")
    if a.stdin and len(names) != 1:
        sys.exit("--stdin seals one Secret")
    sv = sys.stdin.read() if a.stdin else None
    if sv is not None and sv.endswith("\n"):
        sv = sv[:-1]  # like `jarvis2 put-key`: one trailing newline from echo is not part of the value
    ann = dict(x.split("=", 1) for x in a.annotate) or None
    for n in names:
        seal(n, m[n], a.cert and os.path.abspath(a.cert), a.stdin, sv, ann)


if __name__ == "__main__":
    main()
