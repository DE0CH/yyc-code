# yyc-socks

The YYC cluster's way out to the internet: a SOCKS5 server in Rust, which also speaks HTTP proxy on the same port
for clients that know only that. A pod hands it a domain name and a port
(`socks5h://socks.egress.svc.cluster.local:1080`, the `h` = the proxy resolves the name). The pod never looks up an
outside name and never cares whether a site has IPv4, IPv6 or both.

## What it does with a connection

1. **Handshake** (RFC 1928, CONNECT only; BIND and UDP ASSOCIATE answer "command not supported"). No authentication,
   or a username naming the **profile** (RFC 1929, the password is ignored): `default`, or `sandbox` (YYC AI's
   egress, for the sandboxes' traffic). Any other username is refused.
2. **Resolve** the name, A and AAAA together, each profile with its own resolver and cache:
   - `default`: the node's resolver (`/etc/resolv.conf` of a host-network pod = the node's `/etc/k8s-resolv.conf` =
     its systemd-resolved, which asks Hetzner's resolvers over IPv6; `news-stream/deploy/cluster/node-dns.sh`);
   - `sandbox`: malware-blocking resolvers over IPv6, Cloudflare for Families "security" (a blocked name answers
     0.0.0.0 / `::`) and Quad9 (a blocked name answers NXDOMAIN).
3. **Policy**: public unicast addresses only. Refused: private, loopback, link-local (the metadata service), CGNAT,
   multicast, documentation and other special ranges of both families, IPv4-mapped forms of those, the node's own
   addresses (read from the interfaces every minute: the Hetzner firewall doesn't stand between a node and itself),
   and `DENY_CIDRS`. Anything inside the cluster is reached directly, never through the proxy.
4. **Connect** IPv6 first, the next address 250 ms later alternating the families (Happy Eyeballs, RFC 8305), 10 s
   in all. IPv6 leaves over the node's native IPv6; IPv4 over its WARP.
5. **Relay** bytes both ways until either side closes.

Reply codes: RFC 1928's, plus `0x80` for a name the sandbox resolvers block as a threat (unassigned by the RFC: any
client reads it as a failure). `0x02` = not a public address, `0x04` = no such name (or no address), `0x01` = no
resolver answered, `0x05`/`0x06`/`0x03` = the connect was refused / timed out / unreachable.

## HTTP proxy, same port

A client whose first byte isn't SOCKS5's `0x05` is read as an HTTP proxy client (urllib, Node's
`--use-env-proxy`, yt-dlp, ffmpeg, pip: `HTTPS_PROXY=http://socks.egress.svc.cluster.local:1080`). `CONNECT
host:port` tunnels; an absolute-form request (`GET http://host/path`) goes on with its request line in origin form,
its `Proxy-*` headers dropped and `Connection: close` (one request per connection, since the next may be for another
host). The profile is the username of `Proxy-Authorization: Basic`. Same resolver, policy and Happy Eyeballs. A
refusal is `403` (address, threat), `502` (no such name, DNS, connect) or `504` (connect timed out) with the reason in
`X-Yyc-Socks-Refusal`.

## The pool

`news-stream/deploy/apps/socks.yaml`: a DaemonSet on the host network on every apps node (`pool=apps`), listening on
the node's private address only (`HOST_IP`); Service `socks` (namespace `egress`) spreads connections over the copies
that are **ready**: DNS, IPv6 and IPv4 all seen working from that node within 20 s (a probe every 5 s). A node whose
way out breaks leaves the pool by itself and comes back when it works again.

On yyc-talos (`news-stream/deploy/clusters/talos/socks.yaml`) the workers have no IPv6 and leave through yyc-nat1's
WARP: `FAMILIES=ipv4` connects over IPv4 only and leaves IPv6 out of readiness (`/readyz` says `ipv6 not used`), and
`SANDBOX_DNS` names the same blocking resolvers' IPv4 addresses. The image is built for amd64 and arm64.

A rollout replaces one copy at a time. On SIGTERM a copy turns unready at once, keeps taking connections for
`LINGER_SECONDS` (15) while the Service's endpoints catch up, then closes its listener and lets the open connections
finish (up to `DRAIN_SECONDS`, 600; the pod's grace period is 630 s).

## Endpoints (`HOST_IP:1081`)

`/healthz` alive · `/readyz` 200 or 503 with `dns`, `ipv6`, `ipv4`, `draining` · `/metrics` Prometheus text:
`yyc_socks_accepted_total`, `yyc_socks_connected_total{family}`, `yyc_socks_refused_total{reason=address|threat|nxdomain}`,
`yyc_socks_failed_total{reason=dns|connect|bad_request|busy}`, `yyc_socks_bytes_total{direction}`,
`yyc_socks_open_connections`, `yyc_socks_probe_age_seconds{check}`. Each refusal or failure is also one log line
(client, profile, target, reason).

## Settings (env)

`HOST_IP` (listen address), `FAMILIES` (`both`, or `ipv4` on a node with no IPv6), `SOCKS_PORT` (1080), `HEALTH_PORT` (1081), `DEFAULT_DNS` (`system`, or servers
`a,b`), `SANDBOX_DNS` (the four IPv6 servers above), `DENY_CIDRS`, `MAX_CONNECTIONS` (8192), `CONNECT_TIMEOUT_MS`
(10000), `PROBE_NAMES` (`github.com,www.cloudflare.com`), `LINGER_SECONDS`, `DRAIN_SECONDS`.

## Build and test

`cargo test` (the wire format, the policy, Happy Eyeballs); `.github/workflows/socks-image.yml` builds the image
(`ghcr.io/de0ch/yyc-socks`), tests the container as a proxy and pins the tag for Flux. Locally:

```bash
HOST_IP=127.0.0.1 SOCKS_PORT=11080 HEALTH_PORT=11081 cargo run --release &
curl --socks5-hostname 127.0.0.1:11080 https://api.exa.ai/                    # through it
curl --socks5-hostname sandbox:x@127.0.0.1:11080 http://malware.testcategory.com/   # refused: threat
curl 127.0.0.1:11081/readyz
```
