#!/usr/bin/env python3
"""Every Deployment rolls out with zero downtime (CLAUDE.md "Deploy means a zero-downtime deploy"; Deyao, 2026-10-16:
"No downtime"). No database and no network: the manifests are read, the HTTP servers run for real on loopback.

    python3 tests/test_rollouts.py

1. Every Deployment in deploy/apps/: an explicit RollingUpdate, never Recreate. A singleton (1 replica) starts its new
   pod first (maxUnavailable 0, maxSurge >= 1); a Deployment of 2 or more replicas replaces one pod at a time
   (maxSurge 0, maxUnavailable 1: the others keep serving and no extra memory is needed; Deyao, 2026-10-20), except
   yyc-mcp, which starts a new pod before an old one goes (maxSurge 1, maxUnavailable 0): its manifest (env, Secret)
   lands before the workflow pins the image built for it, so a new pod can fail to start for a while and both old pods
   must keep serving meanwhile.
2. Every Deployment behind a Service waits out the endpoint removal before its server stops listening (a preStop sleep,
   or, for the WiseSearch relay, its own 503-then-drain), its server has a finite graceful timeout, and the grace period
   covers the preStop and that timeout (so the kubelet never SIGKILLs a request in flight).
3. cloudflared: the socat sidecar that carries every request to Service web is a native sidecar (stopped only after
   cloudflared has drained), and cloudflared's own --grace-period is the most it accepts (3m; web allows 300 s, so a
   request still running after 3 minutes on a connector being replaced is cut: a gap left, cloudflared's limit).
4. web (gunicorn with web.yaml's own flags): a connection the old pod left idle is never reused into a hang and a
   reset. Gunicorn stops reading idle keep-alive connections at SIGTERM but closes them only when the worker exits
   (after the slowest request in flight, up to 300 s); the tunnel's connections reach a pod through socat, pinned to
   it, so every response must close its connection.
"""
import json, os, re, shlex, signal, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.request

import yaml

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = os.path.join(HERE, "deploy", "apps")


def docs():
    out = []
    for f in sorted(os.listdir(APPS)):
        if f.endswith(".yaml"):
            out += [(f, d) for d in yaml.safe_load_all(open(os.path.join(APPS, f))) if d]
    return out


DOCS = docs()
DEPLOYS = {d["metadata"]["name"]: d for _, d in DOCS if d["kind"] == "Deployment" and d["metadata"].get("namespace") == "yyc"}
ALL_DEPLOYS = {f'{d["metadata"].get("namespace")}/{d["metadata"]["name"]}': d for _, d in DOCS if d["kind"] == "Deployment"}
SERVICES = [d for _, d in DOCS if d["kind"] == "Service"]
IN_APP_DRAIN = {"wisers"}  # wisers/relay.py: /healthz 503 and keeps serving for the endpoint removal (test_wisers_rollout.py)


def spec(name):
    return DEPLOYS[name]["spec"]["template"]["spec"]


def container(name, cname=None):
    s = spec(name)
    for c in s["containers"] + s.get("initContainers", []):
        if cname is None or c["name"] == cname:
            return c
    raise KeyError(cname)


def prestop_seconds(c):
    p = (c.get("lifecycle") or {}).get("preStop") or {}
    if "sleep" in p:
        return int(p["sleep"]["seconds"])
    cmd = (p.get("exec") or {}).get("command") or []
    if cmd[:1] == ["sleep"]:
        return int(cmd[1])
    return 0


def flag(cmd, name):
    """the value of --name in a command list (None when absent)"""
    for i, a in enumerate(cmd):
        if a == name:
            return cmd[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def served(name):
    labels = DEPLOYS[name]["spec"]["template"]["metadata"]["labels"]
    return any(all(labels.get(k) == v for k, v in s["spec"]["selector"].items()) for s in SERVICES)


def grace(name):
    return spec(name).get("terminationGracePeriodSeconds", 30)


PDBS = [d for _, d in DOCS if d["kind"] == "PodDisruptionBudget"]
NODE_BOUND = set()  # a Deployment tied to a node by a ReadWriteOnce volume: none (wisers moved its state to Postgres,
# 2026-11-09); such a one would get no PDB, or a drain could never finish
VOLUME_BEING_MOVED = set()  # a volume kept only until its copy is in Postgres: none (wisers' copied 2026-11-09)


def pdb_for(name):
    d = ALL_DEPLOYS[name]
    labels = d["spec"]["template"]["metadata"]["labels"]
    ns = d["metadata"].get("namespace")
    return [p for p in PDBS if p["metadata"].get("namespace") == ns
            and all(labels.get(k) == v for k, v in p["spec"]["selector"]["matchLabels"].items())]


class Disruptions(unittest.TestCase):
    """a node drain (a multi-node move, 2026-10-20) never takes a serving workload down: Deployments of 2+ replicas
    keep one (PDB minAvailable 1); a singleton is never evicted (PDB maxUnavailable 0): cluster/drain.sh moves it with
    its own rolling update (new pod first, lock handover), so `kubectl drain` alone waits instead of cutting it"""

    def test_replicated_deployments_keep_one(self):
        for n, d in ALL_DEPLOYS.items():
            if d["spec"].get("replicas", 1) >= 2:
                p = pdb_for(n)
                self.assertTrue(p, n)
                self.assertEqual(p[0]["spec"].get("minAvailable"), 1, n)

    def test_replicas_spread_over_nodes(self):
        """2026-10-20: both cloudflared pods (and both web pods' predecessors) sat on one node, one node's loss = both"""
        for n, d in ALL_DEPLOYS.items():
            if d["spec"].get("replicas", 1) >= 2:
                tsc = d["spec"]["template"]["spec"].get("topologySpreadConstraints") or []
                self.assertTrue(any(t.get("topologyKey") == "kubernetes.io/hostname" and t.get("maxSkew") == 1
                                    for t in tsc), n)

    def test_no_job_is_tied_to_a_node(self):
        """state lives in the application (Postgres, the bucket), never on a volume a job must run beside (Deyao,
        2026-11-08): the scheduler holds no room for a CronJob's next run, so a job tied to one node waits whenever
        anything else fills it (2026-10-21: the poller took the browser-data node and sites-browser stayed Pending)"""
        for _, d in DOCS:
            if d["kind"] != "CronJob":
                continue
            ps = d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            n = d["metadata"]["name"]
            self.assertFalse([v for v in ps.get("volumes") or [] if "persistentVolumeClaim" in v], n)
            self.assertFalse((ps.get("affinity") or {}).get("podAffinity"), n)
        self.assertFalse([d["metadata"]["name"] for _, d in DOCS if d["kind"] == "PersistentVolumeClaim"
                          and d["metadata"]["name"] not in VOLUME_BEING_MOVED], "a volume beyond the ones being moved")

    def test_stateless_servers_run_two(self):
        for n in ("flagd", "web", "cloudflared"):
            self.assertGreaterEqual(DEPLOYS[n]["spec"]["replicas"], 2, n)

    def test_singletons_are_never_evicted(self):
        for n, d in DEPLOYS.items():
            if n in NODE_BOUND or d["spec"].get("replicas", 1) != 1:
                continue
            p = pdb_for(f"yyc/{n}")
            self.assertTrue(p, n)
            self.assertEqual(p[0]["spec"].get("maxUnavailable"), 0, n)
        for n in NODE_BOUND:
            self.assertFalse(pdb_for(f"yyc/{n}"), n)

    def test_drain_script_moves_deployments_by_rollout(self):
        sh = open(os.path.join(HERE, "deploy", "cluster", "drain.sh")).read()
        for step in ("kubectl cordon", "rollout restart", "rollout status", "kubectl drain"):
            self.assertIn(step, sh)


SURGE_FIRST = {"yyc/yyc-mcp"}  # 2 replicas that start the new pod first (docstring, 1.)


class Manifests(unittest.TestCase):
    def test_every_deployment_rolls(self):
        self.assertGreaterEqual(set(DEPLOYS), {"cloudflared", "flagd", "images", "monitor", "poller", "transcribe",
                                                "web", "whatsapp", "wisers"})
        for n, d in ALL_DEPLOYS.items():
            st = d["spec"].get("strategy") or {}
            self.assertEqual(st.get("type"), "RollingUpdate", n)
            ru = st.get("rollingUpdate") or {}
            if n in SURGE_FIRST:
                self.assertEqual((ru.get("maxSurge"), ru.get("maxUnavailable")), (1, 0), n)
            elif d["spec"].get("replicas", 1) >= 2:
                self.assertEqual((ru.get("maxSurge"), ru.get("maxUnavailable")), (0, 1), n)
            else:
                self.assertEqual(ru.get("maxUnavailable"), 0, n)
                self.assertGreaterEqual(int(ru.get("maxSurge", 0)), 1, n)

    def test_served_deployments_wait_out_the_endpoint_removal(self):
        names = [n for n in DEPLOYS if served(n) and n not in IN_APP_DRAIN]
        self.assertGreaterEqual(set(names), {"web", "flagd"})
        for n in names:
            c = spec(n)["containers"][0]
            self.assertGreaterEqual(prestop_seconds(c), 5, f"{n}: a preStop sleep before the server stops listening")

    def test_grace_covers_the_drain(self):
        for n, timeout_flag in (("web", "--graceful-timeout"), ("monitor", "--graceful-timeout")):
            c = spec(n)["containers"][0]
            t = flag(c["command"], timeout_flag)
            self.assertIsNotNone(t, f"{n}: a finite graceful timeout ({timeout_flag})")
            self.assertGreater(grace(n), prestop_seconds(c) + int(t), f"{n}: grace period > preStop + drain")

    def test_web_closes_every_connection(self):
        self.assertEqual(flag(container("web")["command"], "--keep-alive"), "0")

    def test_cloudflared_drains_before_its_origin(self):
        s = spec("cloudflared")
        self.assertNotIn("origin", [c["name"] for c in s["containers"]], "socat is not a regular container")
        side = [c for c in s.get("initContainers", []) if c["name"] == "origin"]
        self.assertEqual(len(side), 1, "socat is a native sidecar (initContainers, restartPolicy Always)")
        self.assertEqual(side[0].get("restartPolicy"), "Always")
        cf = container("cloudflared", "cloudflared")
        g = flag(cf["args"], "--grace-period")
        self.assertEqual(g, "3m", "the longest cloudflared accepts (it refuses to start above 3m0s)")
        self.assertGreater(grace("cloudflared"), 180)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TOY = """
import time
def app(environ, start):
    if environ["PATH_INFO"] == "/slow":
        time.sleep(4)
    start("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "2")])
    return [b"ok"]
"""


def http(sock, path, timeout=15):
    """one GET on an open socket → (status line + headers, or the error)"""
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: web\r\n\r\n".encode())
    sock.settimeout(timeout)
    data = b""
    try:
        while not (b"\r\n\r\n" in data and data.endswith(b"ok")):
            ch = sock.recv(4096)
            if not ch:
                return data.decode() or "EOF"
            data += ch
    except OSError as e:
        return repr(e)
    return data.decode()


class WebDrain(unittest.TestCase):
    def test_no_idle_connection_hangs_after_sigterm(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "toy.py"), "w").write(TOY)
        cmd = list(container("web")["command"])
        port = free_port()
        cmd[cmd.index("--chdir") + 1] = d
        cmd[cmd.index("--bind") + 1] = f"127.0.0.1:{port}"
        cmd[cmd.index("--workers") + 1] = "1"  # the idle connection and the slow request on one worker
        cmd[-1] = "toy:app"
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    a = socket.create_connection(("127.0.0.1", port))
                    break
                except OSError:
                    time.sleep(0.1)
            first = http(a, "/fast")
            self.assertIn("200 OK", first)
            b = socket.create_connection(("127.0.0.1", port))
            threading.Thread(target=http, args=(b, "/slow"), daemon=True).start()
            time.sleep(0.5)
            p.send_signal(signal.SIGTERM)  # the old pod, its preStop over
            time.sleep(1.5)  # the worker has seen it (its poll wakes at least once a second)
            if "connection: close" not in first.lower():  # a client may reuse it: then it must be answered
                t0 = time.time()
                again = http(a, "/fast")
                self.assertIn("200 OK", again, f"reused idle connection after SIGTERM: {again!r} "
                                               f"after {time.time() - t0:.1f} s")
            self.assertEqual(p.wait(30), 0)
        finally:
            p.kill()


if __name__ == "__main__":
    unittest.main()
