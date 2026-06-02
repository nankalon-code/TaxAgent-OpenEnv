#!/usr/bin/env python3
"""
================================================================================
  harness.py  —  TaxAgent-OpenEnv  |  Userspace Orchestration Harness
================================================================================

PURPOSE
-------
This script is the userspace controller for the eBPF-based execution sandbox.
It performs the following high-level operations:

  1.  Load and JIT-compile ``ebpf_shield.c`` into the kernel via BCC.
  2.  Attach the compiled probe to the ``openat`` syscall entry point.
  3.  Create a short-lived "dummy agent" script that mimics an LLM agent
      opening a file (a stand-in for the real tax-evaluation subprocess).
  4.  Spawn the dummy agent as an isolated subprocess.
  5.  Insert the subprocess's PID into the kernel's BPF_HASH map to
      activate targeted monitoring.
  6.  Poll the BPF perf ring buffer and print each intercepted file-open
      event in real time.
  7.  Clean up all resources (subprocess, temp files, eBPF maps) on exit.

PREREQUISITES (WSL2 / Ubuntu)
------------------------------
  sudo apt-get install -y python3-bpfcc bpfcc-tools linux-headers-$(uname -r)

  Or if using pip-installed bcc:
    pip install bcc

RUN AS ROOT
-----------
  sudo python3 harness.py

  eBPF requires CAP_BPF + CAP_PERFMON (or simply root) to load programs and
  attach probes.

DESIGN DECISIONS
----------------
  - We prefer the ``syscalls:sys_enter_openat`` tracepoint over a raw kprobe
    on ``__x64_sys_openat`` because tracepoints have a stable, kernel-version-
    independent ABI that survives kernel upgrades.  We fall back to a kprobe
    if the tracepoint is unavailable (e.g., older kernels without tracepoint
    support compiled in).

  - The perf ring buffer is used instead of bpf_trace_printk because:
      * bpf_trace_printk writes to /sys/kernel/debug/tracing/trace_pipe which
        is a global serial resource — not suitable for high-throughput
        production streaming.
      * Perf buffers are per-CPU, lock-free, and expose a proper callback API.

  - Temporary files are written under /tmp with a UUID suffix to avoid
    collisions in multi-tenant evaluation environments.

================================================================================
"""

# ── Standard library ──────────────────────────────────────────────────────────
import os
import sys
import time
import signal
import ctypes
import textwrap
import tempfile
import subprocess
import threading
import http.server
import socketserver
import json
import resource

# ── Third-party (BCC / eBPF) ─────────────────────────────────────────────────
try:
    from bcc import BPF
except ImportError:
    sys.exit(
        "[FATAL] The 'bcc' Python package is not installed.\n"
        "Install it with:\n"
        "  sudo apt-get install python3-bpfcc\n"
        "or:\n"
        "  pip install bcc"
    )


# ==============================================================================
#  STARTUP VALIDATION  (Con #1 + Con #4 fixes)
# ==============================================================================

def check_kernel_config() -> dict:
    """
    Validate that the running kernel has the eBPF features we need.

    Con #1: CONFIG_BPF_KPROBE_OVERRIDE must be enabled or bpf_override_return()
    silently does nothing. The harness previously had no check for this and
    would run in 'enforcement mode' while actually only logging.

    Returns a dict of {feature: bool} and prints warnings for missing features.
    """
    features = {}

    config_paths = [
        "/proc/config.gz",
        f"/boot/config-{os.uname().release}",
        "/boot/config",
    ]

    config_text = ""
    for path in config_paths:
        if os.path.exists(path):
            try:
                if path.endswith(".gz"):
                    import gzip
                    with gzip.open(path, "rt") as f:
                        config_text = f.read()
                else:
                    with open(path) as f:
                        config_text = f.read()
                break
            except (IOError, OSError):
                continue

    if not config_text:
        print("[WARN] Cannot read kernel config — skipping feature validation.")
        print("       If enforcement mode fails silently, this is why.")
        return {}

    checks = {
        "CONFIG_BPF_KPROBE_OVERRIDE": (
            "bpf_override_return() — REQUIRED for enforcement mode.\n"
            "       Without this, the sandbox LOGS violations but does NOT block them.\n"
            "       To enable: recompile kernel with CONFIG_BPF_KPROBE_OVERRIDE=y\n"
            "       WSL2: see https://github.com/microsoft/WSL2-Linux-Kernel\n"
            "       Fallback: sandbox runs in monitor-only mode."
        ),
        "CONFIG_TRACEPOINTS": (
            "Tracepoints — required for sched_process_fork and sched_process_exit.\n"
            "       Without this, child process tracking is disabled."
        ),
        "CONFIG_DEBUG_INFO_BTF": (
            "BTF (BPF Type Format) — needed for CO-RE (future libbpf migration).\n"
            "       BCC works without this but libbpf does not."
        ),
    }

    enforcement_ok = True
    for flag, warning in checks.items():
        enabled = f"{flag}=y" in config_text
        features[flag] = enabled
        if not enabled:
            if flag == "CONFIG_BPF_KPROBE_OVERRIDE":
                enforcement_ok = False
                print(f"[WARN] {flag} is NOT enabled in this kernel.")
                print(f"       {warning}")
            else:
                print(f"[INFO] {flag} not detected — optional feature unavailable.")

    features["enforcement_capable"] = enforcement_ok
    return features


def check_privileges() -> bool:
    """
    Con #4: The original code required full root (uid 0).
    Modern Linux allows eBPF with specific capabilities:
      CAP_BPF     — load and run BPF programs (kernel >= 5.8)
      CAP_PERFMON — access perf events and ring buffers
      CAP_SYS_ADMIN — fallback for older kernels

    We check for these specifically and print actionable guidance.
    Returns True if privileges are sufficient.
    """
    uid = os.geteuid()

    if uid == 0:
        print("[INFO] Running as root — all eBPF capabilities available.")
        print("[INFO] Production note: consider using CAP_BPF+CAP_PERFMON instead.")
        return True

    # Check for specific capabilities via /proc/self/status
    # CapEff field is a hex bitmask of effective capabilities
    try:
        with open("/proc/self/status") as f:
            status = f.read()
        cap_line = [l for l in status.splitlines() if l.startswith("CapEff:")]
        if cap_line:
            cap_eff = int(cap_line[0].split()[1], 16)
            CAP_BPF     = (1 << 39)   # CAP_BPF = 39
            CAP_PERFMON = (1 << 38)   # CAP_PERFMON = 38
            CAP_SYS_ADMIN = (1 << 21) # CAP_SYS_ADMIN = 21 (older kernels)

            has_bpf     = bool(cap_eff & CAP_BPF)
            has_perfmon = bool(cap_eff & CAP_PERFMON)
            has_admin   = bool(cap_eff & CAP_SYS_ADMIN)

            if (has_bpf and has_perfmon) or has_admin:
                print(f"[INFO] Running with CAP_BPF={has_bpf}, "
                      f"CAP_PERFMON={has_perfmon}, CAP_SYS_ADMIN={has_admin}")
                return True
    except (IOError, ValueError):
        pass

    print("[FATAL] Insufficient privileges to load eBPF programs.")
    print("        Option 1 (simple):")
    print("          sudo python3 harness.py")
    print("        Option 2 (production — no full root):")
    print("          sudo setcap cap_bpf,cap_perfmon+eip $(which python3)")
    print("          python3 harness.py")
    return False


# ==============================================================================
#  CONSTANTS
# ==============================================================================

# Path to the eBPF C source file, assumed to be in the same directory as this
# script so that the sandbox can be invoked from any working directory.
EBPF_SOURCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ebpf_shield.c")

# How long (seconds) to wait for the dummy agent to complete before forcing
# a timeout shutdown.  In production, replace with real task completion signals.
AGENT_TIMEOUT_SECONDS = 10

# Sentinel value stored in pid_map for each watched PID.
# The kernel side only checks for key *presence*, so the value is arbitrary.
SENTINEL_VALUE = ctypes.c_uint32(1)


# ==============================================================================
#  DUMMY AGENT SCRIPT
# ==============================================================================

def write_dummy_agent(agent_script_path: str, target_file_path: str) -> None:
    """
    Write a minimal Python script that simulates an LLM agent performing a
    file-open operation.

    In the real system this would be replaced by the actual agent code
    generated by the LLM.  For this PoC it simply:
      1.  Announces itself.
      2.  Opens a local file (target_file_path) for reading.
      3.  Reads and prints the contents.
      4.  Sleeps briefly to give the perf-poll loop time to deliver the event.

    Parameters
    ----------
    agent_script_path : str
        Filesystem path where the dummy script will be written.
    target_file_path : str
        Path to the file the dummy agent will attempt to open.
    """
    agent_code = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"Dummy agent — simulates an LLM-generated Python script.\"\"\"
        import time
        import os

        print(f"[AGENT] PID={{os.getpid()}} starting.")
        print(f"[AGENT] Attempting to open: {target_file_path!r}")

        try:
            with open({target_file_path!r}, "r") as fh:
                contents = fh.read()
            print(f"[AGENT] File contents: {{contents!r}}")
        except FileNotFoundError:
            print("[AGENT] File not found — that's fine for the PoC.")
        except PermissionError:
            print("[AGENT] Permission denied — kernel sandbox is working!")

        # Sleep so the perf ring buffer has time to deliver the event
        time.sleep(1)

        # ── Seccomp test ─────────────────────────────────────────────────────
        print("[AGENT] Testing Seccomp: Attempting to call ptrace (restricted syscall)...")
        try:
            import ctypes
            libc = ctypes.CDLL(None)
            import os
            machine = os.uname().machine
            syscall_num = 117 if ("arm" in machine or "aarch64" in machine) else 101
            # If seccomp works, this next line will trigger SIGSYS and kill the process.
            libc.syscall(syscall_num, 0, 0, 0, 0)
            print("[AGENT] Error: ptrace succeeded! Seccomp filter failed.")
        except Exception as e:
            print(f"[AGENT] Exception while calling ptrace: {e}")

        print("[AGENT] Done.")
    """)

    with open(agent_script_path, "w") as fh:
        fh.write(agent_code)

    # SECURITY UPDATE: To support privilege dropping to 'nobody' (UID 65534),
    # the script must be readable and executable by others (0o755), since
    # it executes in the dropped context of the sandbox child.
    os.chmod(agent_script_path, 0o755)
    print(f"[HARNESS] Dummy agent written to: {agent_script_path}")


# ==============================================================================
#  eBPF LOADER
# ==============================================================================

def load_ebpf_program() -> BPF:
    """
    Read ``ebpf_shield.c`` from disk and compile + load it into the kernel
    via BCC's JIT pipeline.

    BCC performs the following steps internally:
      1.  Pre-processes the C source (injects kernel headers, rewrites BPF
          map macros into LLVM IR).
      2.  Compiles to eBPF bytecode using the Clang front-end.
      3.  Verifies the bytecode using the in-kernel BPF verifier.
      4.  JIT-compiles to native machine code.
      5.  Loads the program object into the kernel via the bpf(2) syscall.

    Returns
    -------
    BPF
        An initialised BCC BPF object ready for probe attachment and map access.

    Raises
    ------
    FileNotFoundError
        If ``ebpf_shield.c`` cannot be found at the expected path.
    Exception
        If BCC fails to compile or load the program (e.g., verifier rejection,
        missing kernel headers, insufficient privileges).
    """
    if not os.path.isfile(EBPF_SOURCE_PATH):
        raise FileNotFoundError(
            f"eBPF source not found at: {EBPF_SOURCE_PATH}\n"
            "Ensure ebpf_shield.c is in the same directory as harness.py."
        )

    print(f"[HARNESS] Loading eBPF program from: {EBPF_SOURCE_PATH}")

    # Con #1 Fix: detect if kernel supports kprobe override, pass compile-time define
    features = check_kernel_config()
    cflags = [
        "-Wno-unused-value",
        "-Wno-pointer-sign",
        "-Wno-compare-distinct-pointer-types",
    ]
    if features.get("enforcement_capable", False):
        print("[HARNESS] Target kernel supports kprobe overrides. Enabling active enforcement.")
        cflags.append("-DKPROBE_OVERRIDE_SUPPORTED")
    else:
        print("[HARNESS] Active enforcement NOT supported by kernel. Fallback to monitor-only audit mode.")

    with open(EBPF_SOURCE_PATH, "r") as fh:
        bpf_source = fh.read()

    bpf_obj = BPF(text=bpf_source, cflags=cflags)

    print("[HARNESS] eBPF program compiled and loaded into kernel ✓")
    return bpf_obj


# ==============================================================================
#  PROBE ATTACHMENT
# ==============================================================================

def attach_probe(bpf_obj: BPF) -> str:
    """
    Attach the compiled eBPF probe to the openat(2) syscall entry point.

    Strategy:
      1.  Try the stable ``syscalls:sys_enter_openat`` tracepoint first.
          Tracepoints have a kernel-version-independent ABI and are preferred.
      2.  Fall back to a kprobe on the architecture-specific syscall wrapper
          (``__x64_sys_openat`` on x86-64) if the tracepoint is unavailable.

    Parameters
    ----------
    bpf_obj : BPF
        The loaded BCC BPF object.

    Returns
    -------
    str
        A human-readable description of the attachment point used.
    """
    probe_fn_name = b"trace_openat_entry"   # Must match the C function name

    # ── Attempt 1: Tracepoint ────────────────────────────────────────────────
    try:
        bpf_obj.attach_tracepoint(
            tp=b"syscalls:sys_enter_openat",
            fn_name=probe_fn_name
        )
        attach_desc = "tracepoint:syscalls:sys_enter_openat"
        print(f"[HARNESS] Probe attached via {attach_desc} ✓")
        return attach_desc

    except Exception as tp_err:
        print(f"[HARNESS] Tracepoint unavailable ({tp_err}), "
              f"falling back to kprobe …")

    # ── Attempt 2: kprobe fallback ───────────────────────────────────────────
    # On x86-64 the syscall is dispatched through __x64_sys_openat.
    # On arm64 it's __arm64_sys_openat.  We attempt x86-64 first.
    kprobe_sym = bpf_obj.get_syscall_fnname("openat")
    try:
        bpf_obj.attach_kprobe(
            event=kprobe_sym,
            fn_name=probe_fn_name
        )
        attach_desc = f"kprobe:{kprobe_sym.decode()}"
        print(f"[HARNESS] Probe attached via {attach_desc} ✓")
        return attach_desc

    except Exception as kp_err:
        raise RuntimeError(
            f"Failed to attach probe via both tracepoint and kprobe.\n"
            f"  Tracepoint error: {tp_err}\n"
            f"  kprobe error:     {kp_err}\n"
            f"Ensure the kernel has CONFIG_HAVE_EBPF_JIT=y and you are root."
        ) from kp_err


# ==============================================================================
#  PID MAP MANAGEMENT
# ==============================================================================

def register_pid(bpf_obj: BPF, pid: int, expected_exe: str) -> None:
    """
    Insert the agent's PID into the kernel-side BPF_HASH map (``pid_map``).

    SECURITY FIX #5 — PID Recycling (TOCTOU) Guard
    ------------------------------------------------
    There is a race window between Popen() returning a PID and this function
    inserting it into the kernel map.  If the agent exits in that window and
    the OS recycles the PID to an unrelated process, we would incorrectly
    monitor that innocent process.

    We mitigate this by reading /proc/<pid>/cmdline immediately before
    registering and verifying it contains our expected executable path.
    If validation fails, we refuse to arm the kernel probe.

    Parameters
    ----------
    bpf_obj : BPF
        The loaded BCC BPF object (provides map access).
    pid : int
        The userspace PID (TGID) of the spawned agent subprocess.
    expected_exe : str
        Substring expected in /proc/<pid>/cmdline (e.g. the script path).
    """
    # Validate PID identity before arming the kernel probe.
    cmdline_path = f"/proc/{pid}/cmdline"
    try:
        with open(cmdline_path, "rb") as fh:
            cmdline = fh.read().replace(b"\x00", b" ").decode(errors="replace")
        if expected_exe not in cmdline:
            raise RuntimeError(
                f"[SECURITY] PID {pid} cmdline does not match expected agent.\n"
                f"  Expected : {expected_exe!r}\n"
                f"  Got      : {cmdline!r}\n"
                "  Refusing to arm kernel probe (possible PID recycling attack)."
            )
    except FileNotFoundError:
        raise RuntimeError(
            f"[SECURITY] PID {pid} vanished before kernel probe could be armed.\n"
            "  The agent may have exited too quickly. Aborting."
        )

    pid_map = bpf_obj["pid_map"]   # BCC exposes maps as dict-like objects

    # BPF map keys and values must be ctypes instances matching the C types.
    key   = ctypes.c_uint32(pid)
    value = SENTINEL_VALUE

    pid_map[key] = value
    print(f"[HARNESS] PID {pid} validated and registered in kernel watch-list ✓")


def deregister_pid(bpf_obj: BPF, pid: int) -> None:
    """
    Remove the agent's PID from the kernel map when monitoring is no longer
    needed.  This is called in the finally block to avoid leaking stale
    entries (important in long-running evaluation servers).

    Parameters
    ----------
    bpf_obj : BPF
        The loaded BCC BPF object.
    pid : int
        The PID to remove.
    """
    try:
        pid_map = bpf_obj["pid_map"]
        key = ctypes.c_uint32(pid)
        del pid_map[key]
        print(f"[HARNESS] PID {pid} removed from kernel watch-list ✓")
    except KeyError:
        pass   # Already absent — not an error


# ==============================================================================
#  PERF EVENT CALLBACK
# ==============================================================================

# We define the Python-side mirror of the C ``event_t`` struct so that ctypes
# can interpret the raw bytes emitted by the kernel correctly.
#
# Field layout MUST exactly match the C struct (including padding):
#   - pid      : u32   → ctypes.c_uint32  (4 bytes)
#   - filename : char[256] → ctypes.c_char * 256 (256 bytes)
#
# Total size: 260 bytes — matches sizeof(struct event_t) in C on all platforms
# targeted (x86-64 / arm64) because there is no inter-field padding here.

class FileEvent(ctypes.Structure):
    """Python mirror of the C ``struct event_t`` sent over the perf buffer.

    SECURITY FIX #1 / #2 / #4 — This layout MUST exactly match the updated
    C struct, including the explicit _pad[3] field.  If the layouts diverge,
    ctypes will silently misinterpret field values, producing phantom events
    or hiding real ones.
    """
    _fields_ = [
        ("pid",          ctypes.c_uint32),
        ("timestamp_ns", ctypes.c_uint64),   # monotonic kernel timestamp
        ("flags",        ctypes.c_uint32),   # raw open(2) flags
        ("is_write",     ctypes.c_uint8),    # 1 if any write flag set
        ("_pad",         ctypes.c_uint8 * 3),# explicit padding — must match C
        ("filename",     ctypes.c_char * 256),
    ]


def build_perf_callback(stop_event: threading.Event):
    """
    Factory that returns a perf-buffer callback closure.

    Using a closure allows us to pass the ``stop_event`` without relying on
    global state, which would be fragile in a multi-agent evaluation loop.

    Parameters
    ----------
    stop_event : threading.Event
        Signal used to tell the poll loop to exit after the agent finishes.

    Returns
    -------
    Callable
        The callback function with signature ``(cpu, data, size)`` expected
        by BCC's ``open_perf_buffer()``.
    """

    def handle_file_event(cpu: int, data, size: int) -> None:
        """
        Called by BCC for every event the kernel pushes to the ring buffer.

        Parameters
        ----------
        cpu : int
            Index of the CPU core that submitted this event (informational).
        data : ctypes pointer
            Raw pointer to the event bytes in shared perf memory.
        size : int
            Number of bytes in the event payload (should equal sizeof(event_t)).
        """
        # Cast the raw memory pointer to our FileEvent structure.
        # ctypes.cast + ctypes.POINTER gives us a typed view with zero copy.
        event = ctypes.cast(data, ctypes.POINTER(FileEvent)).contents

        # Decode the filename bytes; replace non-UTF-8 bytes with '?' to
        # avoid crashing on exotic paths (e.g., binary filenames).
        filename = event.filename.decode("utf-8", errors="replace")

        # SECURITY FIX #1 — Flag WRITE-intent opens as potential threats.
        severity = "WRITE 🔴" if event.is_write else "READ  🟢"

        # Pretty-print the intercepted event.
        print(
            f"\n{'\u2500' * 64}\n"
            f"  \U0001f6e1  KERNEL INTERCEPT\n"
            f"  PID        : {event.pid}\n"
            f"  Timestamp  : {event.timestamp_ns} ns (monotonic)\n"
            f"  Access     : {severity}  (raw flags=0x{event.flags:04x})\n"
            f"  File       : {filename}\n"
            f"  CPU core   : {cpu}\n"
            f"{'\u2500' * 64}"
        )

        if event.is_write:
            print("  \u26a0\ufe0f  [SECURITY ALERT] Agent attempting WRITE access! "
                  "Consider blocking.", flush=True)

        # Broadcast event to any connected SSE dashboard clients
        broadcast_sse_event("syscall", {
            "pid": event.pid,
            "filename": filename,
            "is_write": bool(event.is_write),
            "flags": event.flags,
            "ts": event.timestamp_ns
        })

    def handle_lost_events(cpu: int, count: int) -> None:
        """
        Called when the ring buffer overflows and events are lost.
        In production, this should increment a Prometheus counter.
        """
        print(f"[WARN] Lost {count} events on CPU {cpu} — "
              f"consider increasing the ring buffer page count.")

    return handle_file_event, handle_lost_events


# ==============================================================================
#  PERF POLL THREAD
# ==============================================================================

def run_perf_poll(bpf_obj: BPF, stop_event: threading.Event) -> None:
    """
    Blocking loop that drains the perf ring buffer and invokes the callback
    for each event.  Intended to run on a dedicated background thread so
    the main thread remains free to monitor the subprocess.

    Parameters
    ----------
    bpf_obj : BPF
        The loaded BCC BPF object.
    stop_event : threading.Event
        When set, the loop exits cleanly.
    """
    print("[HARNESS] Perf poll loop started (background thread) …")
    while not stop_event.is_set():
        # perf_buffer_poll() blocks for up to `timeout` milliseconds waiting
        # for events, then returns.  Short timeout keeps the stop check
        # responsive without burning CPU in a tight spin.
        bpf_obj.perf_buffer_poll(timeout=200)

    print("[HARNESS] Perf poll loop exited.")


# ==============================================================================
#  LIVE DASHBOARD SSE SERVER
# ==============================================================================

active_clients = []
clients_lock = threading.Lock()

class SSEHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress logging request noise to keep console clean
        return

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            with clients_lock:
                active_clients.append(self.wfile)
            
            try:
                while True:
                    time.sleep(1)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
            finally:
                with clients_lock:
                    if self.wfile in active_clients:
                        active_clients.remove(self.wfile)
            return

        return super().do_GET()

    def translate_path(self, path):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
        if path == "/":
            return os.path.join(root, "index.html")
        parts = path.lstrip("/").split("/")
        return os.path.join(root, *parts)


def broadcast_sse_event(event_type: str, data: dict):
    payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with clients_lock:
        dead_clients = []
        for client in active_clients:
            try:
                client.write(payload.encode("utf-8"))
                client.flush()
            except Exception:
                dead_clients.append(client)
        for client in dead_clients:
            if client in active_clients:
                active_clients.remove(client)


def run_http_server():
    server_address = ("", 8000)
    class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
    
    try:
        httpd = ThreadedHTTPServer(server_address, SSEHTTPRequestHandler)
        print("[HARNESS] Live Dashboard Server running on http://localhost:8000 ...")
        httpd.serve_forever()
    except Exception as e:
        print(f"[HARNESS][WARN] Failed to start Live Dashboard Server: {e}")


# ==============================================================================
#  SECCOMP SYSTEM ISOLATION
# ==============================================================================

# BPF assembly instructions and constants for filtering syscalls
BPF_LD  = 0x00
BPF_W   = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x15
BPF_RET = 0x06
BPF_K   = 0x00

SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ALLOW        = 0x7fff0000

class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]

class SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_uint16),
        ("filter", ctypes.POINTER(SockFilter)),
    ]

def restrict_child() -> None:
    """
    Enforce dual-layer seccomp filtering on the child process before execution.
    We drop privileges and forbid dangerous administrative syscalls:
      - reboot()
      - ptrace() (stops subprocesses debugging other processes or escaping)
      - syslog() (stops accessing system logs)
    Also applies memory/CPU limits and de-escalates privileges to 'nobody'.
    """
    try:
        # Load local standard C library
        libc = ctypes.CDLL(None)

        # 1. Enable PR_SET_NO_NEW_PRIVS to allow loading seccomp without root inside child
        PR_SET_NO_NEW_PRIVS = 38
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            sys.stderr.write("[HARNESS][CHILD] Failed to set PR_SET_NO_NEW_PRIVS\n")
            sys.exit(1)

        # 2. Enforce CPU and Memory Resource Limits (prevents Denial of Service)
        # 256 MB virtual memory ceiling
        MEM_LIMIT = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
        # 5 seconds of CPU execution time limit
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))

        # 3. Determine syscall numbers based on architecture
        machine = os.uname().machine
        if "arm" in machine or "aarch64" in machine:
            sys_reboot = 142
            sys_ptrace = 117
            sys_syslog = 116
        else:
            sys_reboot = 169
            sys_ptrace = 101
            sys_syslog = 103

        # BPF filters block reboot, ptrace, and syslog
        filter_insts = [
            SockFilter(BPF_LD | BPF_W | BPF_ABS, 0, 0, 0),                       # Load syscall nr
            SockFilter(BPF_JMP | BPF_JEQ | BPF_K, 4, 0, sys_reboot),             # If reboot, jump to KILL
            SockFilter(BPF_JMP | BPF_JEQ | BPF_K, 3, 0, sys_ptrace),             # If ptrace, jump to KILL
            SockFilter(BPF_JMP | BPF_JEQ | BPF_K, 2, 0, sys_syslog),             # If syslog, jump to KILL
            SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW),                # Otherwise ALLOW
            SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_KILL_PROCESS),         # KILL target
        ]

        insts_array = (SockFilter * len(filter_insts))(*filter_insts)
        prog = SockFprog(len(filter_insts), insts_array)

        PR_SET_SECCOMP = 22
        SECCOMP_MODE_FILTER = 2
        if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(prog)) != 0:
            sys.stderr.write("[HARNESS][CHILD] Failed to load seccomp filter\n")
            sys.exit(1)

        # 4. Drop child process privileges to unprivileged 'nobody' user (UID 65534)
        # Drop group ID first, then user ID to completely de-escalate privilege
        os.setgid(65534)
        os.setuid(65534)
            
    except Exception as e:
        sys.stderr.write(f"[HARNESS][CHILD] Error applying restrictions: {e}\n")
        sys.exit(1)


# ==============================================================================
#  MAIN ORCHESTRATION
# ==============================================================================

def main() -> int:
    """
    Entry point.  Orchestrates the full sandbox lifecycle:

      1.  Validate root privileges.
      2.  Create temporary workspace (dummy agent + test file).
      3.  Load and attach the eBPF program.
      4.  Spawn the dummy agent subprocess.
      5.  Register the agent's PID with the kernel.
      6.  Start the perf-poll background thread.
      7.  Wait for the agent to finish (or timeout).
      8.  Tear down everything cleanly.

    Returns
    -------
    int
        Exit code: 0 on success, non-zero on failure.
    """

    # ── Privilege Check ───────────────────────────────────────────────────────
    if not check_privileges():
        return 1

    # ── State variables (used in finally block) ───────────────────────────────
    bpf_obj:    BPF | None               = None
    agent_proc: subprocess.Popen | None  = None
    poll_thread: threading.Thread | None = None
    stop_event:  threading.Event         = threading.Event()
    agent_pid:   int | None              = None
    tmp_dir:     str | None              = None

    try:
        # Start Live Dashboard Server in background
        server_thread = threading.Thread(
            target=run_http_server,
            daemon=True,
            name="dashboard-server"
        )
        server_thread.start()

        # ── Step 1: Create a temporary workspace ─────────────────────────────
        #
        # We use tempfile.mkdtemp() for an isolated, uniquely-named directory.
        # All temporary files live here so we can clean up atomically.
        tmp_dir = tempfile.mkdtemp(prefix="taxagent_sandbox_")
        os.chmod(tmp_dir, 0o755)  # Let 'nobody' access files within
        agent_script_path = os.path.join(tmp_dir, "dummy_agent.py")
        test_file_path    = os.path.join(tmp_dir, "test.txt")

        # Create the test file that the dummy agent will try to read.
        with open(test_file_path, "w") as fh:
            fh.write("Hello from the kernel-monitored sandbox!\n")
        os.chmod(test_file_path, 0o644)  # Let 'nobody' read the file
        print(f"[HARNESS] Test file created at: {test_file_path}")

        # Write the dummy agent script.
        write_dummy_agent(agent_script_path, test_file_path)

        # ── Step 2: Load the eBPF program ─────────────────────────────────────
        bpf_obj = load_ebpf_program()

        # ── Step 3: Register the perf buffer callback ─────────────────────────
        #
        # We must open the perf buffer BEFORE spawning the subprocess to avoid
        # a race condition where the agent opens files before the ring buffer
        # is ready to receive events.
        handle_event, handle_lost = build_perf_callback(stop_event)
        bpf_obj["file_events"].open_perf_buffer(
            handle_event,
            lost_cb=handle_lost,
            page_cnt=64   # Number of 4 KiB pages per CPU for the ring buffer.
                          # 64 pages = 256 KiB of buffer per CPU — ample for PoC.
        )
        print("[HARNESS] Perf ring buffer opened ✓")

        # ── Step 4: Attach the kernel probe ──────────────────────────────────
        attach_desc = attach_probe(bpf_obj)

        # ── Step 5: Spawn the dummy agent subprocess ─────────────────────────
        #
        # Con #6 Fix: Network Namespace containment via 'unshare -n'
        # Check if unshare command works or is available
        use_netns = True
        try:
            subprocess.run(["unshare", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, OSError):
            use_netns = False
            print("[HARNESS][WARN] 'unshare' utility not found. Network Namespace isolation disabled.")

        cmd = [sys.executable, agent_script_path]
        if use_netns:
            print("[HARNESS] Network Namespace containment active. Synergizing loopback-only environment.")
            cmd = ["unshare", "-n"] + cmd

        # We use sys.executable to ensure we invoke the same Python interpreter
        # that is running this harness, avoiding version mismatches.
        #
        # stdout/stderr are inherited so the agent's print() calls appear
        # directly in the terminal alongside harness output.
        print(f"\n[HARNESS] Spawning dummy agent: {agent_script_path}")
        agent_proc = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
            preexec_fn=restrict_child  # Set seccomp filter in child context before exec
        )
        agent_pid = agent_proc.pid
        print(f"[HARNESS] Agent spawned with PID: {agent_pid}")

        # Broadcast spawn event to live dashboard
        broadcast_sse_event("spawn", {"pid": agent_pid})

        # ── Step 6: Activate kernel monitoring for this PID ──────────────────
        #
        # This is the critical coupling step: by inserting agent_pid into the
        # BPF_HASH, we "arm" the kernel probe for exactly this one process.
        # Any openat() call by any other process will be ignored (fast path).
        register_pid(bpf_obj, agent_pid, agent_script_path)

        # ── Step 7: Start the background perf poll thread ─────────────────────
        poll_thread = threading.Thread(
            target=run_perf_poll,
            args=(bpf_obj, stop_event),
            daemon=True,   # Killed automatically if the main thread exits
            name="perf-poll"
        )
        poll_thread.start()

        # ── Step 8: Wait for the agent to complete ────────────────────────────
        print(f"\n[HARNESS] Waiting up to {AGENT_TIMEOUT_SECONDS}s for agent …\n")
        exit_code = 0
        try:
            agent_proc.wait(timeout=AGENT_TIMEOUT_SECONDS)
            exit_code = agent_proc.returncode
            print(f"\n[HARNESS] Agent exited with code: {exit_code}")

        except subprocess.TimeoutExpired:
            print(f"\n[HARNESS][WARN] Agent exceeded {AGENT_TIMEOUT_SECONDS}s timeout.")
            exit_code = -1

        # Broadcast exit event to live dashboard
        broadcast_sse_event("exit", {"pid": agent_pid, "code": exit_code})

        # Give the perf poll loop one final drain cycle to collect any events
        # that were in-flight when the agent exited.
        time.sleep(0.5)

        print("\n[HARNESS] ✓ Execution complete.  Shutting down …")
        return 0

    except KeyboardInterrupt:
        print("\n[HARNESS] Interrupted by user (Ctrl+C).")
        # Attempt to broadcast exit on interrupt
        if agent_pid:
            broadcast_sse_event("exit", {"pid": agent_pid, "code": 130})
        return 130   # Standard SIGINT exit code

    except Exception as exc:
        print(f"\n[HARNESS][ERROR] Unhandled exception: {exc}")
        if agent_pid:
            broadcast_sse_event("exit", {"pid": agent_pid, "code": 1})
        import traceback
        traceback.print_exc()
        return 1

    finally:
        # ── CLEANUP — always runs regardless of how we exit ──────────────────
        #
        # Ordering matters:
        #   1. Signal the poll thread to stop first (it holds no locks).
        #   2. Wait for the poll thread to finish draining.
        #   3. Remove the PID from the kernel map to stop new events.
        #   4. Terminate the agent subprocess if still running.
        #   5. Let the BPF object go out of scope (BCC detaches probes via __del__).
        #   6. Remove temporary files.

        print("\n[HARNESS] Running cleanup …")

        # 1 & 2. Stop perf poll thread.
        stop_event.set()
        if poll_thread is not None and poll_thread.is_alive():
            poll_thread.join(timeout=2.0)

        # 3. Remove the PID from the kernel watch-list.
        if bpf_obj is not None and agent_pid is not None:
            deregister_pid(bpf_obj, agent_pid)

        # 4. Terminate the agent subprocess (idempotent).
        if agent_proc is not None:
            if agent_proc.poll() is None:   # Still running?
                print(f"[HARNESS] Terminating agent (PID {agent_proc.pid}) …")
                agent_proc.terminate()
                try:
                    agent_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    print("[HARNESS][WARN] Agent did not terminate gracefully; "
                          "sending SIGKILL …")
                    agent_proc.kill()
                    agent_proc.wait()
                print(f"[HARNESS] Agent terminated.")

        # 5. BCC detaches probes when the BPF object is garbage collected.
        #    We delete the reference explicitly to trigger __del__ deterministically.
        if bpf_obj is not None:
            del bpf_obj
            print("[HARNESS] eBPF program detached and unloaded ✓")

        # 6. Remove temporary workspace.
        if tmp_dir is not None and os.path.isdir(tmp_dir):
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"[HARNESS] Temporary directory removed: {tmp_dir}")

        print("[HARNESS] Cleanup complete.  Goodbye.")


# ==============================================================================
#  ENTRYPOINT
# ==============================================================================

if __name__ == "__main__":
    sys.exit(main())
