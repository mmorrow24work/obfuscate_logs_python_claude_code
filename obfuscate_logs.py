"""
Obfuscate IP addresses and hostnames in a zip archive of log files.

Usage:
    python obfuscate_logs.py <input.zip> [--output-dir OUTPUT_DIR]

Outputs (written to output-dir, default: same folder as input zip):
    <stem>_obfuscated.zip   — all log content and filenames with IPs/hostnames replaced
    <stem>_obfuscated_encode.txt — original  →  token  (look up what a token means)
    <stem>_obfuscated_decode.txt — token     →  original (translate back)

Tokens used:
    [IP_1], [IP_2], ...     for IPv4 addresses
    [HOST_1], [HOST_2], ... for hostnames

CIDR prefix lengths are preserved: 192.168.1.1/24 → [IP_1]/24
Padded IPs are normalised before mapping: 192.168.001.001 → 192.168.1.1
"""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Compiled regular expressions
# ---------------------------------------------------------------------------

# IPv4 address — handles padded octets (001, 02, etc.) and optional CIDR /N
# Groups: 1=o1  2=o2  3=o3  4=o4  5=/prefix (optional)
_IP_RE = re.compile(
    r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(/\d{1,2})?\b'
)

# Traditional syslog header: [<priority>] Mon [D]D HH:MM:SS hostname ...
# Captures the hostname token (field after the timestamp)
_SYSLOG_HOST_RE = re.compile(
    r'^(?:<\d+>)?\s*[A-Z][a-z]{2}\s+\d+\s+[\d:]+\s+(\S+)'
)

# FQDN pattern for filename scanning (requires at least one dot).
# Uses lookbehind/lookahead instead of \b so that hyphens inside hostnames
# (e.g. fw-edge.lab.local) don't cause \b to split mid-hostname, and so the
# full FQDN is matched rather than a partial (e.g. edge.lab.local is NOT
# matched when preceded by a hyphen in fw-edge.lab.local).
_FQDN_RE = re.compile(
    r'(?<![a-zA-Z0-9\-])[a-zA-Z][a-zA-Z0-9\-]*(?:\.[a-zA-Z][a-zA-Z0-9\-]*)+(?![a-zA-Z0-9\-\.])'
)


# ---------------------------------------------------------------------------
# Registry building (Pass 1 — read only)
# ---------------------------------------------------------------------------

def _normalize_ip(o1: str, o2: str, o3: str, o4: str) -> str:
    return f"{int(o1)}.{int(o2)}.{int(o3)}.{int(o4)}"


def _looks_like_ip(s: str) -> bool:
    return bool(_IP_RE.fullmatch(s.split('/')[0]))


def _build_registries(
    zf: zipfile.ZipFile,
    zip_name: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Scan all filenames and log content; return (ip_map, host_map).

    Both maps are {original_string: token}, assigned in first-discovery order.
    ip_map keys are normalised IPs (no padded octets).
    host_map keys are the raw hostname strings as seen.
    """
    ip_map: dict[str, str] = {}
    host_map: dict[str, str] = {}

    def register_ip(o1: str, o2: str, o3: str, o4: str) -> None:
        key = _normalize_ip(o1, o2, o3, o4)
        if key not in ip_map:
            ip_map[key] = f"[IP_{len(ip_map) + 1}]"

    def register_host(name: str) -> None:
        # Skip empty strings, pure IPs, and single-character tokens
        if not name or len(name) < 2 or _looks_like_ip(name):
            return
        if name not in host_map:
            host_map[name] = f"[HOST_{len(host_map) + 1}]"

    # --- Scan zip filename and entry names for FQDNs and IPs ---
    for text in [zip_name] + zf.namelist():
        for fqdn in _FQDN_RE.findall(text):
            register_host(fqdn)
        for m in _IP_RE.finditer(text):
            register_ip(m.group(1), m.group(2), m.group(3), m.group(4))

    # --- Scan file content ---
    entries = [e for e in zf.namelist() if not e.endswith('/')]
    total = len(entries)
    for idx, entry in enumerate(entries, 1):
        if idx % 200 == 0 or idx == total:
            print(f"\r      Scanning {idx}/{total} files ...", end='', flush=True)
        try:
            raw = zf.read(entry)
            content = raw.decode('utf-8', errors='replace')
        except Exception:
            continue

        for line in content.splitlines():
            stripped = line.strip()
            # Syslog hostname extraction
            m = _SYSLOG_HOST_RE.match(stripped)
            if m:
                register_host(m.group(1))
            # All IPv4 addresses on the line
            for m in _IP_RE.finditer(stripped):
                register_ip(m.group(1), m.group(2), m.group(3), m.group(4))

    if total:
        print()  # newline after progress

    return ip_map, host_map


# ---------------------------------------------------------------------------
# Substitution helpers (Pass 2 — write)
# ---------------------------------------------------------------------------

def _make_ip_replacer(ip_map: dict[str, str]):
    """Return a re.sub callback that replaces matched IPs with tokens."""
    def replacer(m: re.Match) -> str:
        norm = _normalize_ip(m.group(1), m.group(2), m.group(3), m.group(4))
        token = ip_map.get(norm)
        if token is None:
            return m.group(0)  # not in map — leave unchanged (shouldn't happen)
        cidr = m.group(5) or ""
        return f"{token}{cidr}"
    return replacer


def _obfuscate_text(
    text: str,
    ip_replacer,
    hosts_longest_first: list[tuple[str, str]],
) -> str:
    text = _IP_RE.sub(ip_replacer, text)
    for original, token in hosts_longest_first:
        text = text.replace(original, token)
    return text


def _obfuscate_name(
    name: str,
    ip_replacer,
    hosts_longest_first: list[tuple[str, str]],
) -> str:
    """Obfuscate a filename or zip entry name."""
    result = _IP_RE.sub(ip_replacer, name)
    for original, token in hosts_longest_first:
        result = result.replace(original, token)
    return result


# ---------------------------------------------------------------------------
# Mapping file writer
# ---------------------------------------------------------------------------

def _write_mapping(
    path: Path,
    ip_map: dict[str, str],
    host_map: dict[str, str],
    encode: bool,
) -> None:
    col_w = 45
    lines: list[str] = []

    if encode:
        lines.append("# ENCODE MAP — original data  →  obfuscated token")
        lines.append("# Use this to look up what a token in the obfuscated logs represents.")
    else:
        lines.append("# DECODE MAP — obfuscated token  →  original data")
        lines.append("# Use this to translate obfuscated tokens back to real values.")

    lines.append("")
    lines.append("# IP addresses")
    lines.append(f"{'Original' if encode else 'Token':<{col_w}}  →  {'Token' if encode else 'Original'}")
    lines.append("-" * (col_w + 10))
    for orig, token in ip_map.items():
        left, right = (orig, token) if encode else (token, orig)
        lines.append(f"{left:<{col_w}}  →  {right}")

    lines.append("")
    lines.append("# Hostnames")
    lines.append(f"{'Original' if encode else 'Token':<{col_w}}  →  {'Token' if encode else 'Original'}")
    lines.append("-" * (col_w + 10))
    for orig, token in host_map.items():
        left, right = (orig, token) if encode else (token, orig)
        lines.append(f"{left:<{col_w}}  →  {right}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def obfuscate(zip_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        all_entries = zf.namelist()
        print(f"[1/3] Building registries ({len(all_entries)} zip entries) ...")
        ip_map, host_map = _build_registries(zf, zip_path.name)
        print(f"      {len(ip_map)} unique IP(s), {len(host_map)} unique hostname(s) found")

        # Longest-first ordering prevents partial replacements (e.g. host.sub.domain
        # replaced before host, so "host.sub.domain" doesn't become "[HOST_2].sub.domain")
        hosts_lf = sorted(host_map.items(), key=lambda kv: len(kv[0]), reverse=True)
        ip_replacer = _make_ip_replacer(ip_map)

        # Derive output stem by obfuscating the input zip's own name
        stem = zip_path.stem
        obf_stem = _obfuscate_name(stem, ip_replacer, hosts_lf)
        if not obf_stem.endswith("_obfuscated"):
            obf_stem += "_obfuscated"

        out_zip_path = output_dir / f"{obf_stem}.zip"
        encode_path  = output_dir / f"{obf_stem}_encode.txt"
        decode_path  = output_dir / f"{obf_stem}_decode.txt"

        print(f"[2/3] Writing obfuscated zip → {out_zip_path.name} ...")
        data_entries = [e for e in all_entries if not e.endswith('/')]
        total = len(data_entries)

        with zipfile.ZipFile(out_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as out_zf:
            for idx, entry in enumerate(all_entries, 1):
                obf_entry = _obfuscate_name(entry, ip_replacer, hosts_lf)

                if entry.endswith('/'):
                    # Directory entry — just re-add with obfuscated name
                    out_zf.mkdir(obf_entry) if hasattr(out_zf, 'mkdir') else \
                        out_zf.writestr(zipfile.ZipInfo(obf_entry + '/'), b'')
                    continue

                if idx % 200 == 0 or idx == total:
                    print(f"\r      Processing {idx}/{total} files ...", end='', flush=True)

                try:
                    raw = zf.read(entry)
                    content = raw.decode('utf-8', errors='replace')
                    obf_content = _obfuscate_text(content, ip_replacer, hosts_lf)
                    obf_bytes = obf_content.encode('utf-8')
                except Exception:
                    # Binary or unreadable file — copy raw, obfuscate name only
                    obf_bytes = zf.read(entry)

                out_zf.writestr(obf_entry, obf_bytes)

        if total:
            print()

    print(f"[3/3] Writing mapping files ...")
    _write_mapping(encode_path, ip_map, host_map, encode=True)
    _write_mapping(decode_path, ip_map, host_map, encode=False)

    print(f"\nDone.")
    print(f"  Obfuscated zip  :  {out_zip_path}")
    print(f"  Encode map      :  {encode_path}")
    print(f"  Decode map      :  {decode_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Obfuscate IPs and hostnames in a zip archive of log files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("zip_file", type=Path, help="Input zip archive containing log files")
    ap.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory to write output files (default: same directory as input zip)",
    )
    args = ap.parse_args()

    zip_path = args.zip_file.resolve()
    if not zip_path.exists():
        ap.error(f"File not found: {zip_path}")
    if not zipfile.is_zipfile(zip_path):
        ap.error(f"Not a valid zip file: {zip_path}")

    output_dir = args.output_dir.resolve() if args.output_dir else zip_path.parent
    obfuscate(zip_path, output_dir)


if __name__ == "__main__":
    main()
