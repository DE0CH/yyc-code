"""One copy at a time across pods (the workers' singleton lock; CLAUDE.md "Deploy means a zero-downtime deploy").

A Postgres session advisory lock on a connection of its own (not the pool), held for as long as the copy works. A
rolling deploy starts the new pod first: it waits here (trying every POLL seconds, so a SIGTERM while waiting ends it at
once) while the old one, on SIGTERM, finishes its work in hand and exits, which frees the lock.

The lock lives only as long as its connection: a database restart or a dropped connection frees it without the
holder knowing (2026-10-16: three transcribe pods kept working beside the new holder). So a holder checks its connection
every CHECK seconds; when it is gone it takes the lock again on a new connection, and when another copy got it in the
gap, it stops (`on_lost`, by default setting the copy's stop event: drain and exit), so two copies never work side by
side for more than a few seconds.

The keeper speaks on the lock's connection until the copy calls release() (or exits), NOT until its stop event: the
lock's connection ends itself after corpus.LOCK_IDLE idle seconds (a dead holder's lock frees itself), so a keeper that
went quiet on SIGTERM let the lock go while the copy still finished its work in hand, and the next pod took it mid-drain
(the poller, 2026-10-21 03:29 and 03:31 UTC; tests/test_drain_holds_lock.py).

    LOCK = podlock.Lock("images", STOP)   # the advisory lock 'yyc images'
    if not LOCK.take(): ...                # False: STOP was set while waiting
"""
import threading

import psycopg

import corpus

CHECK = 5  # seconds between checks that the lock's connection is still there
POLL = 1  # seconds between tries while another copy holds the lock
RETRY_MAX = 30  # the longest wait between tries while the database is unreachable


class Lock:
    def __init__(self, name, stop, on_lost=None):
        self.name, self.key, self.stop = name, "yyc " + name, stop
        self.on_lost = on_lost or stop.set
        self.conn = None
        self.held = threading.Event()
        self.gen = 0  # one keeper per take
        self.done = threading.Event()  # set by release(): this take's keeper stops speaking

    def _connect(self):
        return corpus.lock_connect(f"yyc-{self.name}-lock")  # ended by the server when idle: _keep speaks every CHECK s

    def _try(self, c):
        return c.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self.key,)).fetchone()[0]

    @staticmethod
    def _close(c):
        try:
            if c is not None:
                c.close()
        except Exception:  # noqa: BLE001
            pass

    def take(self):
        """wait until this copy holds the lock → True, or until stop is set → False. A database that is down is waited
        for (backoff), not a reason to exit"""
        print(f"waiting for {self.name} lock", flush=True)
        c, delay = None, 1
        while not self.stop.is_set():
            try:
                if c is None:
                    c = self._connect()
                if self._try(c):
                    self.conn = c
                    self.held.set()
                    self.gen += 1
                    self.done = threading.Event()
                    print(f"took {self.name} lock", flush=True)
                    threading.Thread(target=self._keep, args=(self.gen, self.done), daemon=True,
                                     name=f"keep-{self.name}-lock").start()
                    return True
                delay = 1
                self.stop.wait(POLL)
            except psycopg.OperationalError as e:
                print(f"database unavailable ({self.name} lock): {str(e).strip().splitlines()[0][:200]}; "
                      f"retrying in {delay} s", flush=True)
                self._close(c)
                c = None
                self.stop.wait(delay)
                delay = min(delay * 2, RETRY_MAX)
        self._close(c)
        return False

    def _keep(self, gen, done):
        """until release(): a stopping copy (SIGTERM) is still draining, and the lock stays its own until it is done"""
        while not done.wait(CHECK):
            if gen != self.gen or not self.held.is_set():
                return
            c = self.conn
            try:
                c.execute("SELECT 1")
                continue
            except psycopg.Error as e:
                if done.is_set():  # release() closed it
                    return
                print(f"{self.name} lock connection lost: {str(e).strip().splitlines()[0][:200]}", flush=True)
            self._close(c)
            delay = 1
            while not done.is_set():
                n = None
                try:
                    n = self._connect()
                    ok = self._try(n)
                except psycopg.OperationalError as e:
                    self._close(n)
                    print(f"database unavailable (taking the {self.name} lock again): "
                          f"{str(e).strip().splitlines()[0][:200]}; retrying in {delay} s", flush=True)
                    done.wait(delay)
                    delay = min(delay * 2, RETRY_MAX)
                    continue
                if ok and done.is_set():  # released meanwhile
                    self._close(n)
                    return
                if ok:
                    self.conn = n
                    print(f"retook {self.name} lock", flush=True)
                    break
                self._close(n)
                self.conn = None
                self.held.clear()
                print(f"{self.name} lock lost to another copy: stopping", flush=True)
                self.on_lost()
                return

    def release(self):
        """after the copy's work in hand is done: the keeper stops and the lock goes with its connection"""
        self.done.set()
        self.held.clear()
        self._close(self.conn)
        self.conn = None
