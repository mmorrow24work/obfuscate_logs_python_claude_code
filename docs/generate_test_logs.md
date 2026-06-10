# Code Walkthrough — `generate_test_logs.py`

## 1. Purpose

`generate_test_logs.py` creates a realistic-looking zip archive of synthetic log files.
Its sole purpose is to provide test data for `obfuscate_logs.py` without requiring access
to real production logs.

The generated zip:
- Has a filename containing a real-looking FQDN hostname
- Contains log files whose names also include FQDNs
- Mixes several syslog-style and web-server log formats
- Includes IPs in plain, padded, and CIDR forms to exercise every obfuscation code path

---

## 2. CLI flags and size presets

```
python generate_test_logs.py [--size small|medium|large|N] [--output FILE] [--seed N]
```

Parsed in `main()` via `argparse`.

### `--size`

```python
SIZE_PRESETS = {
    "small":  (50,    100),
    "medium": (200,   200),
    "large":  (1000,  500),
}
```

Each tuple is `(num_files, lines_per_file)`. If `--size` is an integer rather than a preset
name, `num_files = int(args.size)` and `lines_per_file = 200` (fixed).

The total entry count (`num_files × lines_per_file`) is printed on completion so the user
can verify scale.

### `--seed`

Passed directly to `random.Random(seed)`. Setting `--seed 42` produces byte-for-byte
identical output on every run — useful for regression testing `obfuscate_logs.py`.

### `--output`

Default is `test_logs.zip`. If the caller leaves the default, `generate()` renames the
output to include the primary hostname:

```python
if output.name == "test_logs.zip":
    zip_name = f"logs_{primary_host}_{base_date.strftime('%Y%m%d')}.zip"
    output = output.parent / zip_name
```

This mirrors real-world log archive naming conventions and gives `obfuscate_logs.py` a
hostname-containing zip name to test against.

---

## 3. Fake data pools

### Hostnames (`HOSTNAMES`)

Ten static FQDNs in the `lab.local` domain:

```python
HOSTNAMES = [
    "router-01.lab.local",
    "router-02.lab.local",
    "sw-core.lab.local",
    ...
]
```

These were chosen to test multiple hostname lengths (important for longest-first replacement
ordering in `obfuscate_logs.py`) and to look like typical enterprise network device names.

### IP pool — `_gen_ip_pool(rng, count=25)`

Generates 25 unique fake IPs across three RFC 1918 ranges:
- `10.x.y.z` — large private range
- `192.168.x.y` — common home/lab range
- `172.16.x.y` — less common but included for coverage

The pool is generated once per run and shared across all log files. This ensures the same
IP appears in many files, giving `obfuscate_logs.py` a non-trivial deduplication workload.

---

## 4. Log line templates

Six template functions are defined, one per log format. All have the same signature:

```python
def _line_xxx(rng, hostname, dt, ips) -> str
```

They are collected in `_TEMPLATES` and selected randomly per line:

```python
fn = rng.choice(_TEMPLATES)
return fn(rng, hostname, dt, ips)
```

### Template 1 — SSH auth (`_line_ssh_auth`)
```
Jun  9 14:05:03 router-01.lab.local sshd[4521]: Accepted password for alice from 10.3.7.12 port 52341 ssh2
```
Tests: syslog header hostname extraction, plain IP.

### Template 2 — Firewall (`_line_firewall`)
```
Jun  9 14:05:04 sw-core.lab.local kernel: [UFW DROP] IN=eth0 OUT= SRC=192.168.001.005 DST=10.3.7.12 PROTO=TCP DPT=443
```
Tests: two IPs on one line, padded IP form (15% chance per IP).

### Template 3 — Web access (`_line_web_access`)
```
192.168.1.5 - - [09/Jun/2026:14:05:05 +0000] "GET /api/v1/status HTTP/1.1" 200 4321
```
Tests: IP at the start of the line (non-syslog format, no hostname field).

### Template 4 — Route (`_line_route`)
```
Jun  9 14:05:06 router-02.lab.local zebra[2201]: OSPF: new route 10.3.0.0/16 via 10.3.7.1 dev eth0 metric 10
```
Tests: CIDR notation (the network address portion is derived from a pool IP).

### Template 5 — DHCP (`_line_dhcp`)
```
Jun  9 14:05:07 mgmt-host.lab.local dhcpd[8812]: DHCPACK on 192.168.003.042 to aa:bb:cc:dd:ee:ff via bond0
```
Tests: padded IP in a DHCP lease line.

### Template 6 — Kernel (`_line_kernel`)
```
Jun  9 14:05:08 fw-edge.lab.local kernel: Neighbour table overflow detected — peer 172.16.5.200 not reachable
```
Tests: IP in an unstructured message body.

---

## 5. Padded IP generation

```python
def _pad_ip(ip: str) -> str:
    return '.'.join(p.zfill(3) for p in ip.split('.'))
```

`str.zfill(3)` left-pads with zeros: `"1"` → `"001"`, `"12"` → `"012"`, `"123"` unchanged.
Applied at 15% probability per IP in each template to ensure a realistic fraction of padded
addresses without overwhelming the data.

---

## 6. File and zip naming

Each log file is named:
```python
f"{host}_{file_date.strftime('%Y%m%d')}_{i:05d}.log"
# e.g. router-01.lab.local_20260610_00001.log
```

The `:05d` zero-padded index keeps entries sorted correctly in directory listings.

The zip file is named:
```python
f"logs_{primary_host}_{base_date.strftime('%Y%m%d')}.zip"
# e.g. logs_router-01.lab.local_20260610.zip
```

`primary_host = hosts[0]` — the first host in the randomly-sampled subset.

Hosts are cycled round-robin across files (`host = hosts[i % len(hosts)]`) so each
host contributes an equal number of log files.

---

## 7. Zip construction

The entire zip is assembled in memory before writing to disk:

```python
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    ...
output.write_bytes(buf.getvalue())
```

`io.BytesIO` avoids a partial file on disk if generation fails partway through.
`ZIP_DEFLATED` gives a meaningful size reduction on text log data.

---

## 8. Using the output with `obfuscate_logs.py`

```bash
# Step 1 — generate
python generate_test_logs.py --size medium --seed 42

# Step 2 — obfuscate
python obfuscate_logs.py logs_router-01.lab.local_20260610.zip

# Step 3 — verify no raw IPs remain
unzip -p logs_\[HOST-1\]_20260610_obfuscated.zip | grep -E '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
# Should produce no output

# Step 4 — inspect the mapping
cat logs_\[HOST-1\]_20260610_obfuscated_encode.txt
```

---

## 9. Maintaining this code

| Area | What to change |
|---|---|
| Add a new log format | Write a new `_line_xxx` function and append it to `_TEMPLATES` |
| Change hostname pool | Edit the `HOSTNAMES` list |
| Change IP ranges | Edit `_gen_ip_pool()` |
| Change padded-IP frequency | Change the `0.15` threshold in each template |
| Add a new size preset | Add an entry to `SIZE_PRESETS` |
