# obfuscate_logs_python_claude_code

Obfuscate sensitive data in large log archives before sharing them with third parties.

Given a zip file containing thousands of log files, the pipeline:
- Replaces every IPv4 address with a token (`[IP_1]`, `[IP_2]`, ...)
- Replaces every hostname with a token (`[HOST_1]`, `[HOST_2]`, ...)
- Obfuscates hostnames embedded in log filenames and the zip filename itself
- Produces encode and decode mapping files so obfuscated data can be traced back if needed

No third-party packages required — standard library only.

---

## Quick start

```bash
# 1. Generate test data
python generate_test_logs.py --size small --seed 42

# 2. Run obfuscation (input zip name includes a hostname — see output)
python obfuscate_logs.py logs_router-01.lab.local_20260610.zip

# 3. Inspect outputs
ls *.zip *.txt
```

---

## `obfuscate_logs.py`

### Usage

```bash
python obfuscate_logs.py <input.zip> [--output-dir OUTPUT_DIR]
```

| Argument | Default | Description |
|---|---|---|
| `input.zip` | required | Zip archive containing log files |
| `--output-dir` | same folder as input | Where to write the three output files |

### What it does

1. **Pass 1 — build registries**: scans every log file and filename to discover all unique IPs and hostnames; assigns tokens in first-discovery order.
2. **Pass 2 — apply obfuscation**: rewrites every log file in memory, replaces all IPs and hostnames with their tokens, then writes a new zip with obfuscated entry names.
3. **Write mapping files**: writes the encode and decode text files.

### IP handling

| Input form | Output |
|---|---|
| `192.168.1.1` | `[IP_1]` |
| `192.168.001.001` | `[IP_1]` (padded octets normalised before mapping) |
| `192.168.1.1/24` | `[IP_1]/24` (CIDR prefix preserved) |
| `10.0.0.1` (different IP) | `[IP_2]` |

### Hostname detection

Hostnames are discovered from two sources:

| Source | Method |
|---|---|
| Log content | Syslog header parser — extracts field after `Mon DD HH:MM:SS` |
| Filenames | FQDN regex — matches `word.word(.word)*` in entry names and zip name |

Bare (non-dotted) hostnames found in syslog headers are also obfuscated in filenames if they appear there.

### Outputs

All three files share the same stem derived from the (obfuscated) input zip name:

```
logs_[HOST_1]_20260610_obfuscated.zip
logs_[HOST_1]_20260610_obfuscated_encode.txt
logs_[HOST_1]_20260610_obfuscated_decode.txt
```

**Encode file** — look up what a token means (original → token):
```
# ENCODE MAP — original data  →  obfuscated token
# IP addresses
192.168.1.1                                →  [IP_1]
10.5.2.100                                 →  [IP_2]
# Hostnames
router-01.lab.local                        →  [HOST_1]
```

**Decode file** — translate a token back to the original (token → original):
```
# DECODE MAP — obfuscated token  →  original data
# IP addresses
[IP_1]               →  192.168.1.1
[IP_2]               →  10.5.2.100
# Hostnames
[HOST_1]             →  router-01.lab.local
```

---

## `generate_test_logs.py`

### Usage

```bash
python generate_test_logs.py [--size small|medium|large|N] [--output FILE] [--seed N]
```

| Argument | Default | Description |
|---|---|---|
| `--size` | `medium` | Size preset or integer file count |
| `--output` | `test_logs.zip` | Output zip path (renamed to include hostname) |
| `--seed` | random | Integer seed for reproducible output |

### Size presets

| Preset | Files | Lines/file | Total entries |
|---|---|---|---|
| `small` | 50 | 100 | ~5,000 |
| `medium` | 200 | 200 | ~40,000 |
| `large` | 1,000 | 500 | ~500,000 |
| `N` (integer) | N | 200 | N × 200 |

### Generated log types

The generator produces a realistic mix of log formats:

- **SSH auth** — `sshd[PID]: Accepted/Failed password for user from IP`
- **Firewall** — `kernel: [UFW ACCEPT/DROP] IN=eth0 SRC=IP DST=IP`
- **Web access** — Apache/nginx combined log format: `IP - - [timestamp] "GET /path" 200`
- **Route** — `zebra[PID]: OSPF: new route 192.168.1.0/24 via IP`
- **DHCP** — `dhcpd[PID]: DHCPACK on IP to mac via iface`
- **Kernel** — `kernel: Neighbour table overflow — peer IP not reachable`

15% of IPs are written in padded form (`192.168.001.010`) to exercise the normalisation logic.

---

## Known limitations

- **Binary files** inside the zip are copied unchanged (only the entry name is obfuscated). Binary log formats (e.g. Windows Event Log `.evtx`) are not parsed.
- **Non-syslog formats**: only the traditional syslog header (`Mon DD HH:MM:SS hostname`) and FQDN patterns in filenames are used for hostname discovery. Application-specific log headers (e.g. JSON logs, Windows event IDs) are not parsed for hostnames, though any IPs on those lines are still obfuscated.
- **Tokens contain brackets** (`[IP_1]`), which are valid in Linux filenames but may need quoting in shell globs: `ls 'logs_[HOST_1]_*.zip'`.
- The encode/decode files use **normalised IPs** as keys (no padded octets). `192.168.001.001` and `192.168.1.1` both map to `[IP_1]`; the encode file shows `192.168.1.1`.

---

## Code walkthroughs

- [`docs/obfuscate_logs.md`](docs/obfuscate_logs.md) — detailed walkthrough of `obfuscate_logs.py`
- [`docs/generate_test_logs.md`](docs/generate_test_logs.md) — detailed walkthrough of `generate_test_logs.py`
