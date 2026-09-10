#!/usr/bin/env python3
"""Generate a per-lease sing-box relay and report traffic to GoYou.

The adapter is intentionally small and dependency-free so it can run beside
sing-box in the relay container. It owns the sing-box child process, polls the
Clash API for per-inbound connection counters, and sends only byte deltas to
the trusted management API.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MANAGEMENT_API_URL = os.environ["MANAGEMENT_API_URL"].rstrip("/")
METERING_TOKEN = os.environ["METERING_TOKEN"]
SING_BOX_BINARY = os.getenv("SING_BOX_BINARY", "sing-box")
RELAY_CONFIG_PATH = Path(os.getenv("RELAY_CONFIG_PATH", "/var/lib/goyou/relay.json"))
STATE_PATH = Path(os.getenv("METERING_STATE_PATH", "/var/lib/goyou/metering-state.json"))
CLASH_API_URL = os.getenv("CLASH_API_URL", "http://127.0.0.1:9090").rstrip("/")
SYNC_INTERVAL = max(2, int(os.getenv("SYNC_INTERVAL_SECONDS", "5")))
EGRESS_HOST = os.getenv("EGRESS_HOST", "127.0.0.1")
EGRESS_PORT = int(os.getenv("EGRESS_PORT", "19080"))
LEGACY_RELAY_PORT = int(os.getenv("LEGACY_RELAY_PORT", "24443"))
LEGACY_RELAY_PASSWORD = os.getenv("LEGACY_RELAY_PASSWORD", "")
RELAY_METHOD = os.getenv("RELAY_METHOD", "chacha20-ietf-poly1305")
stop_requested = False


def request_json(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{MANAGEMENT_API_URL}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json", "X-Metering-Token": METERING_TOKEN},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def clash_json(path: str, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{CLASH_API_URL}{path}", method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as temporary:
        json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def load_state() -> dict[str, dict[str, int]]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, dict[str, int]]) -> None:
    write_json_atomic(STATE_PATH, state)


def relay_config(leases: list[dict]) -> dict:
    inbounds = []
    if LEGACY_RELAY_PASSWORD:
        inbounds.append(
            {
                "type": "shadowsocks",
                "tag": "legacy-static",
                "listen": "0.0.0.0",
                "listen_port": LEGACY_RELAY_PORT,
                "method": RELAY_METHOD,
                "password": LEGACY_RELAY_PASSWORD,
            }
        )
    for lease in leases:
        inbounds.append(
            {
                "type": "shadowsocks",
                "tag": f"lease-{lease['lease_id']}",
                "listen": "0.0.0.0",
                "listen_port": int(lease["port"]),
                "method": RELAY_METHOD,
                "password": lease["password"],
            }
        )
    return {
        "log": {"level": "warn"},
        "experimental": {
            "clash_api": {
                "external_controller": "127.0.0.1:9090",
            }
        },
        "inbounds": inbounds,
        "outbounds": [
            {
                "type": "socks",
                "tag": "egress-tunnel",
                "server": EGRESS_HOST,
                "server_port": EGRESS_PORT,
            },
            {"type": "block", "tag": "block"},
        ],
        "route": {"final": "egress-tunnel"},
    }


def config_fingerprint(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stop_child(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def start_child() -> subprocess.Popen:
    return subprocess.Popen(
        [SING_BOX_BINARY, "run", "-c", str(RELAY_CONFIG_PATH)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )


def report_delta(
    lease_id: str,
    connection_id: str,
    target_host: str | None,
    target_port: int | None,
    upload: int,
    download: int,
) -> bool:
    if upload <= 0 and download <= 0:
        return False
    response = request_json(
        "/v1/internal/usage/report",
        "POST",
        {
            "report_id": f"{lease_id}:{connection_id}:{upload}:{download}",
            "user_id": lease_id_to_user[lease_id],
            "lease_id": lease_id,
            "connection_id": connection_id,
            "target_host": target_host,
            "target_port": target_port,
            "upload_bytes": upload,
            "download_bytes": download,
        },
    )
    return bool(response.get("exceeded"))


lease_id_to_user: dict[str, str] = {}


def connection_inbound(connection: dict) -> str:
    metadata = connection.get("metadata") or {}
    for key in ("inbound", "inboundTag", "inbound_tag", "inboundName"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    connection_type = metadata.get("type")
    if isinstance(connection_type, str) and "/" in connection_type:
        # sing-box exposes the inbound tag as the suffix of `type` in some
        # Clash API versions, for example `shadowsocks/lease-<id>`.
        return connection_type.split("/", 1)[1]
    return ""


def connection_counter(connection: dict, *keys: str) -> int:
    stats = connection.get("stats") or {}
    for key in keys:
        value = connection.get(key, stats.get(key))
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
    return 0


def split_host_port(value: object) -> tuple[str | None, int | None]:
    if not isinstance(value, str):
        return None, None
    value = value.strip()
    if not value:
        return None, None
    if value.startswith("[") and "]" in value:
        host, _, suffix = value[1:].partition("]")
        if suffix.startswith(":") and suffix[1:].isdigit():
            return host, int(suffix[1:])
        return host, None
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if host and port.isdigit():
            return host, int(port)
    return value, None


def connection_target(connection: dict) -> tuple[str | None, int | None]:
    metadata = connection.get("metadata") or {}
    sources = [metadata, connection]
    host: str | None = None
    port: int | None = None
    host_keys = (
        "host",
        "destination",
        "destination_address",
        "destinationAddress",
        "destinationIP",
        "destination_ip",
    )
    port_keys = ("destinationPort", "destination_port", "port")
    for source in sources:
        if not isinstance(source, dict):
            continue
        if host is None:
            for key in host_keys:
                candidate, embedded_port = split_host_port(source.get(key))
                if candidate:
                    host = candidate[:255]
                    if port is None:
                        port = embedded_port
                    break
        if port is None:
            for key in port_keys:
                candidate = source.get(key)
                try:
                    parsed = int(candidate)
                except (TypeError, ValueError):
                    continue
                if 1 <= parsed <= 65535:
                    port = parsed
                    break
    return host, port


def poll_connections(state: dict[str, dict[str, int]]) -> None:
    try:
        connections = clash_json("/connections").get("connections", [])
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return
    active_ids = set()
    for connection in connections:
        connection_id = str(connection.get("id", ""))
        inbound = connection_inbound(connection)
        if not connection_id or not inbound.startswith("lease-"):
            continue
        lease_id = inbound.removeprefix("lease-")
        if lease_id not in lease_id_to_user:
            continue
        active_ids.add(connection_id)
        upload = connection_counter(connection, "upload", "uploaded", "upload_bytes", "uplink")
        download = connection_counter(connection, "download", "downloaded", "download_bytes", "downlink")
        target_host, target_port = connection_target(connection)
        previous = state.get(connection_id, {"upload": 0, "download": 0})
        delta_upload = max(0, upload - int(previous.get("upload", 0)))
        delta_download = max(0, download - int(previous.get("download", 0)))
        try:
            if report_delta(
                lease_id,
                connection_id,
                target_host,
                target_port,
                delta_upload,
                delta_download,
            ):
                clash_json(f"/connections/{urllib.parse.quote(connection_id, safe='')}", "DELETE")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError) as error:
            print(f"usage report failed for {lease_id}/{connection_id}: {error}", flush=True)
            continue
        state[connection_id] = {"upload": upload, "download": download}
    for connection_id in list(state):
        if connection_id not in active_ids:
            del state[connection_id]


def handle_signal(_signum: int, _frame: object) -> None:
    global stop_requested
    stop_requested = True


def main() -> None:
    global lease_id_to_user
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    child: subprocess.Popen | None = None
    previous_fingerprint = ""
    state = load_state()
    try:
        while not stop_requested:
            try:
                payload = request_json("/v1/internal/relay/leases")
                leases = payload.get("leases", [])
                lease_id_to_user = {lease["lease_id"]: lease["user_id"] for lease in leases}
                generated = relay_config(leases)
                fingerprint = config_fingerprint(generated)
                if fingerprint != previous_fingerprint:
                    stop_child(child)
                    write_json_atomic(RELAY_CONFIG_PATH, generated)
                    child = start_child()
                    previous_fingerprint = fingerprint
            except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError) as error:
                print(f"relay sync failed: {error}", flush=True)
            poll_connections(state)
            save_state(state)
            time.sleep(SYNC_INTERVAL)
    finally:
        stop_child(child)


if __name__ == "__main__":
    main()
