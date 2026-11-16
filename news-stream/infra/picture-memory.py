#!/usr/bin/env python3
"""Measure the picture worker's decode memory on real pictures (README "Pictures", Memory): replays pictures through
images.prepare on images.WORKERS threads, sampling the process's RSS every 50 ms, and prints the start, the peak and
the end. No database and no model calls.

    python3 infra/picture-memory.py fetch URLS.txt DIR   download each picture URL (one per line) into DIR
    python3 infra/picture-memory.py run DIR [ROUNDS]     replay DIR's pictures ROUNDS times (default 2)

Pictures to use: the live table's largest plus a recent sample, e.g. on the node
    psql -At -c "(select url from images order by width*height desc limit 40) union all
                 (select url from images where at > (now() - interval '1 day')::text order by random() limit 260)"
Compare glibc settings with env, e.g. MALLOC_MMAP_THRESHOLD_=1048576 (what deploy/apps/images.yaml sets).
"""
import glob, hashlib, os, queue, sys, threading, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("DATABASE_URL", "postgresql:///unused")
import images  # noqa: E402


def rss():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024


def fetch(urls_file, out):
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(out, exist_ok=True)
    urls = [u.strip() for u in open(urls_file) if u.strip()]

    def one(u):
        try:
            data = images.download(u)
        except Exception as e:
            return f"{u[:80]} {e!r:.80}"
        open(os.path.join(out, hashlib.sha256(u.encode()).hexdigest()[:16]), "wb").write(data)
    with ThreadPoolExecutor(16) as ex:
        bad = [x for x in ex.map(one, urls) if x]
    print(f"{len(urls) - len(bad)} of {len(urls)} downloaded into {out}")


def run(folder, rounds=2):
    blobs = [open(f, "rb").read() for f in sorted(glob.glob(os.path.join(folder, "*")))]
    start, peak, done = rss(), [0], threading.Event()
    peak[0] = start

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], rss())
            time.sleep(0.05)
    threading.Thread(target=sample, daemon=True).start()
    q = queue.Queue()
    for b in blobs * rounds:
        q.put(b)

    def work():
        while True:
            try:
                b = q.get_nowait()
            except queue.Empty:
                return
            try:
                images.prepare(b)
            except Exception:
                pass
    t0 = time.time()
    ts = [threading.Thread(target=work) for _ in range(images.WORKERS)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    done.set()
    print(f"{len(blobs)} pictures x {rounds} on {images.WORKERS} threads in {time.time() - t0:.0f} s: start {start} MB "
          f"(the pictures' bytes: {sum(map(len, blobs)) // 2**20} MB), peak {peak[0]} MB (+{peak[0] - start}), "
          f"end {rss()} MB (+{rss() - start})")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["fetch"] and len(a) == 3:
        fetch(a[1], a[2])
    elif a[:1] == ["run"] and len(a) in (2, 3):
        run(a[1], int(a[2]) if len(a) == 3 else 2)
    else:
        print(__doc__)
        sys.exit(2)
