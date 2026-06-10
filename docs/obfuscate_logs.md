# Code Walkthrough — `obfuscate_logs.py`

## 1. Purpose and design rationale

`obfuscate_logs.py` takes a zip archive containing any number of log files and produces a new
zip where every IPv4 address and hostname has been replaced with a deterministic token
(`[IP_1]`, `[HOST_1]`, etc.). Two companion text files (encode and decode maps) let you
translate between the obfuscated and original values after the fact.

### Why tokens instead of fake IPs?

Fake IPs (`10.0.0.1`, `10.0.0.2`, ...) can be confused with real network addresses in the
obfuscated log. Tokens like `[IP_1]` are unambiguous — a reader immediately knows the value
was replaced — and they grep cleanly across thousands of files.

### Why a two-pass approach?

Obfuscation must be **globally consistent**: the same IP in file 1 must get the same token as
that IP in file 5,000. A single-pass streamed approach can't assign token numbers until the
full set of unique IPs is known. Pass 1 builds the complete registry; Pass 2 applies it.

---

## 2. CLI and outputs

```
python obfuscate_logs.py <input.zip> [--output-dir OUTPUT_DIR]
```

The `main()` function uses `argparse` to collect:
- `zip_file` — path to the input zip (validated via `zipfile.is_zipfile`)
- `--output-dir` — defaults to the parent directory of the input zip

`main()` then calls `obfuscate(zip_path, output_dir)` which runs all three phases.

Output file names are derived by obfuscating the **input zip's own stem** and appending
`_obfuscated`:

```
logs_router-01.lab.local_20260610.zip
    → logs_[HOST_1]_20260610_obfuscated.zip
    → logs_[HOST_1]_20260610_obfuscated_encode.txt
    → logs_[HOST_1]_20260610_obfuscated_decode.txt
```

---

## 3. Compiled regular expressions

Three module-level compiled patterns are shared across all processing:

### `_IP_RE`
```python
r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(/\d{1,2})?\b'
```
- Matches standard and padded IPv4 (e.g. `192.168.001.010`)
- `\b` word boundaries prevent partial matches inside longer strings
- Group 5 captures the optional CIDR prefix (`/24`, `/16`, etc.)
- Used for both content scanning and filename scanning

### `_SYSLOG_HOST_RE`
```python
r'^(?:<\d+>)?\s*[A-Z][a-z]{2}\s+\d+\s+[\d:]+\s+(\S+)'
```
- Matches the traditional syslog header format: `[<priority>] Mon DD HH:MM:SS hostname`
- `(?:<\d+>)?` — optional RFC 3164 priority field (e.g. `<13>`)
- `[A-Z][a-z]{2}` — three-letter month abbreviation (Jan–Dec)
- Group 1 captures the hostname token immediately after the timestamp

### `_FQDN_RE`
```python
r'(?<![a-zA-Z0-9\-])[a-zA-Z][a-zA-Z0-9\-]*(?:\.[a-zA-Z][a-zA-Z0-9\-]*)+(?![a-zA-Z0-9\-\.])'
```
- Matches fully-qualified domain names like `router-01.lab.local` and `fw-edge.lab.local`
- Requires at least one dot — bare single-word hostnames come from syslog parsing instead
- Uses **lookbehind/lookahead instead of `\b`** — this is intentional. `\b` treats `-` as a
  non-word character, which creates a word boundary inside a hyphenated hostname. Without this
  fix, `fw-edge.lab.local` would match as `fw-edge.lab` (backtracked) and `edge.lab.local`
  would additionally match at the `-e` boundary, registering the same host twice under
  different tokens. The lookbehind `(?<![a-zA-Z0-9-])` blocks any match that is preceded by
  a hyphen, so `edge` inside `fw-edge` is correctly excluded.
- Used only on filenames (zip entry names and the zip name itself)

---

## 4. Registry building — IP detection and normalisation

`_build_registries(zf, zip_name)` runs Pass 1.

```python
ip_map: dict[str, str] = {}   # normalised IP  →  "[IP_N]"
host_map: dict[str, str] = {} # hostname string →  "[HOST_N]"
```

**IP registration** via the nested `register_ip(o1, o2, o3, o4)`:

```python
def _normalize_ip(o1, o2, o3, o4) -> str:
    return f"{int(o1)}.{int(o2)}.{int(o3)}.{int(o4)}"
```

Each octet string is converted to `int` then back to `str`, collapsing `001` → `1`.
The normalised IP is the dictionary key. This ensures `192.168.001.010` and `192.168.1.10`
both map to the same token `[IP_1]`.

Tokens are assigned in first-discovery order: the first unique IP seen becomes `[IP_1]`,
the second becomes `[IP_2]`, and so on. This makes re-runs on the same zip deterministic.

---

## 5. Registry building — hostname extraction

Hostnames are collected from two sources in `_build_registries`:

### Source 1 — syslog header (log content)

For every line in every log file:
```python
m = _SYSLOG_HOST_RE.match(stripped)
if m:
    register_host(m.group(1))
```

This captures bare hostnames (`myrouter01`) as well as FQDNs that appear as the syslog
hostname field. They are stored verbatim as the map key.

### Source 2 — FQDN in filenames

Both the zip filename and every entry name are scanned with `_FQDN_RE`:
```python
for fqdn in _FQDN_RE.findall(text):
    register_host(fqdn)
```

This catches cases where the zip was named after a host (e.g.
`logs_router-01.lab.local_20260610.zip`) even if no syslog content was parsed yet.

### Filtering

`register_host` skips:
- Empty strings
- Single-character strings (too likely to be false positives)
- Strings that look like IP addresses (checked via `_looks_like_ip`)

---

## 6. Token assignment and ordering

After Pass 1, `hosts_lf` is built:

```python
hosts_lf = sorted(host_map.items(), key=lambda kv: len(kv[0]), reverse=True)
```

Sorting longest-first is critical. Without it, a short hostname like `router-01` could be
replaced inside a longer string like `router-01.lab.local` before the full FQDN is processed,
producing `[HOST_2].lab.local` instead of `[HOST_1]`.

The IP replacer is a closure over `ip_map`:
```python
def _make_ip_replacer(ip_map):
    def replacer(m: re.Match) -> str:
        norm = _normalize_ip(...)
        token = ip_map.get(norm)
        cidr = m.group(5) or ""
        return f"{token}{cidr}"
    return replacer
```

Passing a callable to `re.sub` means every match is normalised before lookup, so padded
and non-padded forms are handled in a single substitution pass.

---

## 7. Pass 2 — content substitution

`_obfuscate_text(text, ip_replacer, hosts_lf)` applies substitution to a single file's content:

```python
def _obfuscate_text(text, ip_replacer, hosts_lf):
    text = _IP_RE.sub(ip_replacer, text)          # replace all IPs first
    for original, token in hosts_lf:              # then hostnames, longest first
        text = text.replace(original, token)
    return text
```

IPs are processed before hostnames. This avoids a theoretical edge case where a hostname
contains a substring that looks like an IP.

---

## 8. Filename obfuscation

`_obfuscate_name(name, ip_replacer, hosts_lf)` is identical in structure to `_obfuscate_text`
but operates on a path string (zip entry name or the zip filename stem).

This ensures:
- `router-01.lab.local_20260610_00001.log` → `[HOST_1]_20260610_00001.log`
- `192.168.1.1_access.log` → `[IP_1]_access.log`

Tokens `[IP_N]` and `[HOST_N]` are valid in Linux/macOS filenames. On Windows they may need
quoting in shell contexts due to bracket glob expansion.

---

## 9. Encode/decode file format

`_write_mapping(path, ip_map, host_map, encode)` writes a human-readable text file.

```
# ENCODE MAP — original data  →  obfuscated token
# IP addresses
Original                                         →  Token
---------------------------------------------
192.168.1.1                                      →  [IP_1]
10.5.2.100                                       →  [IP_2]

# Hostnames
router-01.lab.local                              →  [HOST_1]
```

The `encode=True` flag swaps the column order for the decode file. Both files are UTF-8.

**Important:** The IP keys in both files are **normalised** (no padded octets). If your
original logs contained `192.168.001.001`, the encode file shows `192.168.1.1` → `[IP_1]`.

---

## 10. Maintaining this code

| Area | What to change |
|---|---|
| Support RFC 5424 syslog | Add a second regex in `_SYSLOG_HOST_RE` or a second `match` check in `_build_registries` |
| Add IPv6 support | Add `_IP6_RE` and a parallel registry; apply in both passes |
| Change token format | Edit the `f"[IP_{...}]"` and `f"[HOST_{...}]"` f-strings in `register_ip` / `register_host` |
| Handle JSON logs | Add a JSON-aware hostname extractor alongside `_SYSLOG_HOST_RE` |
| Progress bar | Replace the `\r` print lines with `tqdm` if the package is available |
