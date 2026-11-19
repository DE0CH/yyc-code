"""The edge app's dev server (web-app/, `vite dev`) for a test that renders its pages, when none is given.

    import devserver
    devserver.ensure(5185)   # before the test reads WEB_APP_DATABASE_URL, WEB_APP_URL and ACCESS_TEST_KEY
    devserver.ensure(5182, "SEARCH_SPA_URL", "PARITY_DATABASE_URL")   # a test that names them otherwise

On a developer's machine WEB_APP_URL names the server he runs, and nothing happens here. Otherwise (CI: run_shard.py
gives each file its own throwaway database, TEST_DATABASE_URL): WEB_APP_DATABASE_URL is that database, ACCESS_TEST_KEY
an RSA key made here (written to a temporary file), its public half web-app/.dev.vars' ACCESS_TEST_JWKS (kid "test",
the way test_edge_ops signs in), and `vite dev` runs on the port against the database until the test's process ends
(then .dev.vars is put back). The server is started once the module's import has set the environment; the database
gets its tables from the test's first corpus call, before any page is asked for.
"""
import atexit, json, os, signal, subprocess, tempfile, time, urllib.error, urllib.request

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web-app")


def _key():
    """a fresh RSA key → (its private PEM, the JWKS with its public half)"""
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key()))
    pub.update(kid="test", alg="RS256", use="sig")
    return pem, json.dumps({"keys": [pub]})


def _up(url, deadline, proc):
    while True:
        try:
            with urllib.request.urlopen(url + "/version", timeout=10) as r:
                if r.status < 500:
                    return
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return
        except Exception:  # noqa: BLE001: not listening yet
            pass
        if proc.poll() is not None or time.time() > deadline:
            raise RuntimeError("the dev server did not start (exit %s)" % proc.poll())
        time.sleep(2)


def ensure(port, url_var="WEB_APP_URL", db_var="WEB_APP_DATABASE_URL"):
    """url_var / db_var: the names the test reads its server's address and database by (test_search_spa:
    SEARCH_SPA_URL, PARITY_DATABASE_URL; test_dashboard_spa: DASH_BASE, DASH_DB)"""
    if os.environ.get(url_var):
        return  # the developer's own server, database and key
    db_url = os.environ.get("TEST_DATABASE_URL") or "postgresql:///yyc_test_web_app"
    name = db_url.rsplit("/", 1)[1]
    os.environ[db_var] = db_url
    url = f"http://127.0.0.1:{port}"
    os.environ[url_var] = url
    pem, jwks = _key()
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.environ["ACCESS_TEST_KEY"] = path

    vars_file = os.path.join(APP, ".dev.vars")
    before = open(vars_file).read() if os.path.exists(vars_file) else None
    base = before if before is not None else open(vars_file + ".example").read()
    with open(vars_file, "w") as f:
        f.write("\n".join(l for l in base.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                "\nACCESS_TEST_JWKS=" + jwks + "\n")
    env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=f"postgres://claude:dev@127.0.0.1:5432/{name}")
    log = open(os.path.join(tempfile.gettempdir(), f"devserver-{port}.log"), "w")  # noqa: SIM115 (closed at exit)
    proc = subprocess.Popen(["npx", "vite", "dev", "--port", str(port), "--strictPort"], cwd=APP, env=env,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def stop():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except Exception:  # noqa: BLE001: already gone
            pass
        log.close()
        if before is None:
            os.remove(vars_file)
        else:
            with open(vars_file, "w") as f:
                f.write(before)
        os.remove(path)
    atexit.register(stop)
    try:
        _up(url, time.time() + 600, proc)
    except Exception:
        log.flush()
        print(open(log.name).read()[-3000:])
        raise
