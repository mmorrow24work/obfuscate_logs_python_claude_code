"""
Generate a zip archive of synthetic log files for testing obfuscate_logs.py.

Usage:
    python generate_test_logs.py [--size small|medium|large|N] [--output FILE] [--seed N]

Size presets:
    small   —   50 files ×  100 lines  (~5 000 log entries)
    medium  —  200 files ×  200 lines  (~40 000 log entries)  [default]
    large   — 1000 files ×  500 lines  (~500 000 log entries)
    N       —    N files ×  200 lines  (integer override)

The generated zip and log filenames contain real hostnames, and the log content
contains real-looking IPv4 addresses (plain, padded, and CIDR forms) so that
obfuscate_logs.py has realistic data to work against.
"""
from __future__ import annotations

import argparse
import io
import random
import zipfile
from datetime import datetime, timedelta
from pathlib import Path


SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "small":  (50,    100),
    "medium": (200,   200),
    "large":  (1000,  500),
}

# Fake hostnames that will appear in filenames and syslog headers
HOSTNAMES = [
    "router-01.lab.local",
    "router-02.lab.local",
    "sw-core.lab.local",
    "sw-access-01.lab.local",
    "fw-edge.lab.local",
    "srv-web-01.lab.local",
    "srv-db-01.lab.local",
    "mgmt-host.lab.local",
    "backup-srv.lab.local",
    "monitor-01.lab.local",
]

USERS   = ["alice", "bob", "carol", "dave", "svc_monitor", "admin", "netops"]
PATHS   = ["/api/v1/status", "/health", "/metrics", "/index.html", "/login", "/api/data", "/robots.txt"]
IFACES  = ["eth0", "eth1", "bond0", "vlan100", "vlan200", "lo"]
MONTHS  = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gen_ip_pool(rng: random.Random, count: int = 25) -> list[str]:
    """Generate a pool of unique fake IPv4 addresses."""
    ips: set[str] = set()
    while len(ips) < count:
        kind = rng.randint(0, 2)
        if kind == 0:
            ips.add(f"10.{rng.randint(1, 10)}.{rng.randint(0, 15)}.{rng.randint(1, 254)}")
        elif kind == 1:
            ips.add(f"192.168.{rng.randint(0, 5)}.{rng.randint(1, 254)}")
        else:
            ips.add(f"172.16.{rng.randint(0, 15)}.{rng.randint(1, 254)}")
    return sorted(ips)


def _pad_ip(ip: str) -> str:
    """Zero-pad each octet: 192.168.1.10 → 192.168.001.010"""
    return '.'.join(p.zfill(3) for p in ip.split('.'))


def _ts(dt: datetime) -> str:
    """Format syslog-style timestamp: Jun  9 14:05:03 (note double-space for single-digit days)."""
    day = f"{dt.day:2d}"
    return f"{MONTHS[dt.month - 1]} {day} {dt.strftime('%H:%M:%S')}"


def _net(ip: str, prefix: int) -> str:
    """Return network address string for an IP: 192.168.1.5 + 24 → 192.168.1.0"""
    parts = ip.split('.')
    return '.'.join(parts[:3]) + '.0' + f"/{prefix}"


# ---------------------------------------------------------------------------
# Log line generators — one per template type
# ---------------------------------------------------------------------------

def _line_ssh_auth(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    src = rng.choice(ips)
    ip = _pad_ip(src) if rng.random() < 0.15 else src
    result = rng.choice(["Accepted password", "Failed password", "Invalid user"])
    user = rng.choice(USERS)
    pid = rng.randint(1000, 65535)
    port = rng.randint(1024, 65535)
    return (
        f"{_ts(dt)} {hostname} sshd[{pid}]: {result} for {user} "
        f"from {ip} port {port} ssh2"
    )


def _line_firewall(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    src = rng.choice(ips)
    dst = rng.choice(ips)
    ip_src = _pad_ip(src) if rng.random() < 0.15 else src
    ip_dst = _pad_ip(dst) if rng.random() < 0.15 else dst
    action = rng.choice(["ACCEPT", "DROP", "REJECT"])
    iface = rng.choice(IFACES)
    dpt = rng.randint(1, 65535)
    proto = rng.choice(["TCP", "UDP", "ICMP"])
    return (
        f"{_ts(dt)} {hostname} kernel: [UFW {action}] "
        f"IN={iface} OUT= SRC={ip_src} DST={ip_dst} PROTO={proto} DPT={dpt}"
    )


def _line_web_access(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    src = rng.choice(ips)
    ip = _pad_ip(src) if rng.random() < 0.15 else src
    method = rng.choice(["GET", "GET", "GET", "POST", "PUT", "DELETE"])
    path = rng.choice(PATHS)
    code = rng.choice([200, 200, 200, 301, 304, 400, 403, 404, 500])
    size = rng.randint(100, 50000)
    ts_fmt = dt.strftime("%d/%b/%Y:%H:%M:%S +0000")
    return f'{ip} - - [{ts_fmt}] "{method} {path} HTTP/1.1" {code} {size}'


def _line_route(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    gw = rng.choice(ips)
    net_ip = rng.choice(ips)
    prefix = rng.choice([8, 16, 24, 25, 30])
    cidr = _net(net_ip, prefix)
    iface = rng.choice(IFACES)
    pid = rng.randint(1000, 65535)
    return (
        f"{_ts(dt)} {hostname} zebra[{pid}]: "
        f"OSPF: new route {cidr} via {gw} dev {iface} metric {rng.randint(1, 200)}"
    )


def _line_dhcp(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    ip = rng.choice(ips)
    if rng.random() < 0.15:
        ip = _pad_ip(ip)
    mac = ':'.join(f'{rng.randint(0, 255):02x}' for _ in range(6))
    iface = rng.choice(IFACES)
    pid = rng.randint(1000, 65535)
    return (
        f"{_ts(dt)} {hostname} dhcpd[{pid}]: "
        f"DHCPACK on {ip} to {mac} via {iface}"
    )


def _line_kernel(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    ip = rng.choice(ips)
    return (
        f"{_ts(dt)} {hostname} kernel: "
        f"Neighbour table overflow detected — peer {ip} not reachable"
    )


_TEMPLATES = [
    _line_ssh_auth,
    _line_firewall,
    _line_web_access,
    _line_route,
    _line_dhcp,
    _line_kernel,
]


def _gen_line(rng: random.Random, hostname: str, dt: datetime, ips: list[str]) -> str:
    fn = rng.choice(_TEMPLATES)
    return fn(rng, hostname, dt, ips)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(
    num_files: int,
    lines_per_file: int,
    output: Path,
    seed: int | None,
) -> Path:
    rng = random.Random(seed)
    ips = _gen_ip_pool(rng)
    # Choose a subset of hostnames based on scale — at least 3, at most len(HOSTNAMES)
    n_hosts = min(len(HOSTNAMES), max(3, num_files // 15 + 1))
    hosts = rng.sample(HOSTNAMES, n_hosts)
    primary_host = hosts[0]

    base_date = datetime(2026, 6, 10, 0, 0, 0)

    # If the caller left the default output name, derive a realistic zip name
    if output.name == "test_logs.zip":
        zip_name = f"logs_{primary_host}_{base_date.strftime('%Y%m%d')}.zip"
        output = output.parent / zip_name

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(num_files):
            host = hosts[i % len(hosts)]
            file_date = base_date + timedelta(days=i // max(len(hosts), 1))
            filename = f"{host}_{file_date.strftime('%Y%m%d')}_{i:05d}.log"

            dt = file_date
            lines: list[str] = []
            for _ in range(lines_per_file):
                dt += timedelta(seconds=rng.randint(1, 30))
                lines.append(_gen_line(rng, host, dt, ips))
            zf.writestr(filename, "\n".join(lines) + "\n")

            if (i + 1) % 100 == 0 or (i + 1) == num_files:
                print(f"\r  Written {i + 1}/{num_files} log files ...", end='', flush=True)

    print()
    output.write_bytes(buf.getvalue())

    total_lines = num_files * lines_per_file
    print(f"Generated  :  {num_files} files × {lines_per_file} lines = {total_lines:,} log entries")
    print(f"Hostnames  :  {', '.join(hosts)}")
    print(f"IP pool    :  {len(ips)} unique addresses")
    print(f"Output     :  {output}")
    return output


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate synthetic log files in a zip for testing obfuscate_logs.py.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--size", default="medium",
        help=(
            "Size preset: small (50×100), medium (200×200), large (1000×500), "
            "or an integer N (N files × 200 lines). Default: medium"
        ),
    )
    ap.add_argument(
        "--output", type=Path, default=Path("test_logs.zip"),
        help="Output zip path (default: test_logs.zip, renamed to include hostname)",
    )
    ap.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducible output",
    )
    args = ap.parse_args()

    if args.size in SIZE_PRESETS:
        num_files, lines_per_file = SIZE_PRESETS[args.size]
    else:
        try:
            num_files = int(args.size)
            if num_files < 1:
                raise ValueError
            lines_per_file = 200
        except ValueError:
            ap.error(
                f"--size must be one of {list(SIZE_PRESETS)} or a positive integer, "
                f"got: {args.size!r}"
            )

    generate(num_files, lines_per_file, args.output, args.seed)


if __name__ == "__main__":
    main()
