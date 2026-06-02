# eBPF Kernel Sandbox — TaxAgent-OpenEnv

## Overview

This sub-package implements a **lightweight, zero-overhead execution sandbox** using Linux eBPF (Extended Berkeley Packet Filter) to monitor AI-agent subprocesses at the syscall level.  It is Step 1 (Proof of Concept) of the TaxAgent-OpenEnv security layer.

```
ebpf_sandbox/
├── ebpf_shield.c   ← Kernel-space eBPF hook (C)
├── harness.py      ← Userspace orchestration controller (Python)
└── README.md       ← This file
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         KERNEL SPACE                            │
│                                                                 │
│  sys_enter_openat()  ──►  trace_openat_entry()  [ebpf_shield.c] │
│                                  │                              │
│                     ┌────────────▼──────────────────────────┐   │
│                     │ 1. bpf_get_current_pid_tgid()         │   │
│                     │ 2. Lookup PID in BPF_HASH (pid_map)   │   │
│                     │ 3. Not found → return 0 (fast exit)   │   │
│                     │ 4. bpf_probe_read_user_str(filename)  │   │
│                     │ 5. BPF_PERF_OUTPUT → file_events      │   │
│                     └───────────────────────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────┘
                                   │  (async, lock-free ring buffer)
┌──────────────────────────────────▼──────────────────────────────┐
│                         USER SPACE (harness.py)                 │
│                                                                 │
│  bcc.BPF.load()  →  attach_probe()  →  Popen(dummy_agent.py)    │
│       │                                      │                  │
│  pid_map.insert(agent.pid)  ◄────────────────┘                  │
│       │                                                         │
│  perf_buffer_poll()  →  print(PID, filename)                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

### System (WSL2 / Ubuntu 20.04 or 22.04)

```bash
# Install BCC + kernel headers
sudo apt-get update
sudo apt-get install -y \
    python3-bpfcc \
    bpfcc-tools \
    linux-headers-$(uname -r)

# Verify BCC is accessible
python3 -c "from bcc import BPF; print('BCC OK')"
```

### WSL2 Kernel Requirements

WSL2 ships with a custom Microsoft kernel that has eBPF enabled.  Verify:

```bash
# Should print y or m for all of these
grep CONFIG_BPF /boot/config-$(uname -r)
grep CONFIG_BPF_SYSCALL /boot/config-$(uname -r)
grep CONFIG_BPF_JIT /boot/config-$(uname -r)
grep CONFIG_HAVE_EBPF_JIT /boot/config-$(uname -r)
```

If the config file is missing, check `/proc/config.gz`:

```bash
zcat /proc/config.gz | grep -E "CONFIG_BPF|CONFIG_KPROBE"
```

---

## Running

> **Must be executed as root** — eBPF requires `CAP_BPF` + `CAP_PERFMON`.

```bash
cd ebpf_sandbox/
sudo python3 harness.py
```

### Expected Output

```
[HARNESS] Test file created at: /tmp/taxagent_sandbox_XXXX/test.txt
[HARNESS] Dummy agent written to: /tmp/taxagent_sandbox_XXXX/dummy_agent.py
[HARNESS] Loading eBPF program from: /path/to/ebpf_shield.c
[HARNESS] eBPF program compiled and loaded into kernel ✓
[HARNESS] Perf ring buffer opened ✓
[HARNESS] Probe attached via tracepoint:syscalls:sys_enter_openat ✓

[HARNESS] Spawning dummy agent: /tmp/taxagent_sandbox_XXXX/dummy_agent.py
[HARNESS] Agent spawned with PID: 12345
[HARNESS] PID 12345 registered in kernel watch-list ✓
[HARNESS] Perf poll loop started (background thread) …

[HARNESS] Waiting up to 10s for agent …

[AGENT] PID=12345 starting.
[AGENT] Attempting to open: '/tmp/taxagent_sandbox_XXXX/test.txt'

────────────────────────────────────────────────────────────
  🛡  KERNEL INTERCEPT
  PID      : 12345
  File     : /tmp/taxagent_sandbox_XXXX/test.txt
  CPU core : 3
────────────────────────────────────────────────────────────

[AGENT] File contents: 'Hello from the kernel-monitored sandbox!\n'
[AGENT] Done.

[HARNESS] Agent exited with code: 0
[HARNESS] ✓ Execution complete.  Shutting down …

[HARNESS] Running cleanup …
[HARNESS] PID 12345 removed from kernel watch-list ✓
[HARNESS] eBPF program detached and unloaded ✓
[HARNESS] Temporary directory removed: /tmp/taxagent_sandbox_XXXX
[HARNESS] Cleanup complete.  Goodbye.
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Tracepoint preferred over kprobe** | Stable kernel ABI — survives kernel upgrades without recompilation |
| **BPF_PERF_OUTPUT ring buffer** | Lock-free, per-CPU — suitable for production-throughput streaming; `bpf_trace_printk` is a debugging tool only |
| **PID-filtered BPF_HASH** | O(1) early-exit for unmonitored processes — zero overhead impact on the rest of the OS |
| **`bpf_probe_read_user_str`** | Only safe way to copy userspace pointers in kernel context; verifier-required |
| **Background poll thread** | Keeps main thread free for subprocess lifecycle management |
| **`finally` cleanup block** | Guarantees subprocess termination + eBPF resource release even on crash/interrupt |

---

## Next Steps (Future Scope)

- **Step 2:** Add a `connect()` syscall hook to intercept outbound network calls.
- **Step 3:** Implement a DENY action (via `bpf_override_return` or seccomp integration) rather than just logging.
- **Step 4:** Replace the dummy agent with the real LLM code-generation loop.
- **Step 5:** Add a Prometheus metrics exporter for the `handle_lost_events` counter.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Operation not permitted` | Run with `sudo` |
| `cannot find kernel headers` | `sudo apt-get install linux-headers-$(uname -r)` |
| `Failed to attach probe` | Kernel may lack `CONFIG_KPROBE_EVENTS`; check `/proc/sys/kernel/kptr_restrict` |
| `No module named bcc` | `sudo apt-get install python3-bpfcc` |
| `WSL2 eBPF not supported` | Ensure WSL2 (not WSL1): `wsl --set-version <distro> 2` |
