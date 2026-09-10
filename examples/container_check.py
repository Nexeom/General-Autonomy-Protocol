"""Check the demo boundary from inside the agent container, without private keys.

Run only in the documented isolated demo topology. GAP_TEST_SINK_IP can name the
sink's exact container IP for a network check independent of Docker DNS. The
fixture checks exercise real LangGraph routing and HTTP authorization; they are
not model evaluations or a claim of general sandbox security.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from pathlib import Path

from gap_kernel.integrations.langgraph import GatewayClient, build_governed_graph


def directory_unavailable(path: str) -> bool:
    try:
        with os.scandir(path):
            return False
    except (FileNotFoundError, PermissionError):
        return True


def connection_blocked(host: str) -> dict:
    try:
        with socket.create_connection((host, 8091), timeout=2):
            return {"passed": False, "detail": "direct sink connection succeeded"}
    except socket.gaierror:
        return {"passed": True, "detail": "sink name does not resolve on the agent network"}
    except OSError as exc:
        return {"passed": True, "detail": type(exc).__name__}


def main() -> int:
    checks = {}
    for name, path in (
        ("gateway_private_directory_unavailable", "/run/gap-gateway"),
        ("operator_private_directory_unavailable", "/run/gap-operator"),
        ("sink_private_directory_unavailable", "/run/gap-sink"),
    ):
        try:
            checks[name] = {"passed": directory_unavailable(path)}
        except OSError as exc:
            checks[name] = {"passed": False, "detail": type(exc).__name__}
    checks["tool_token_not_mounted"] = {
        "passed": not os.path.lexists("/run/secrets/tool-token")
                  and not os.path.lexists("/run/gap-sink/tool-token")
                  and not os.path.lexists("/run/gap-gateway/tool-token"),
    }
    checks["sink_hostname_connection_blocked"] = connection_blocked("sink")
    sink_ip = os.environ.get("GAP_TEST_SINK_IP")
    if sink_ip:
        try:
            sink_ip = str(ipaddress.ip_address(sink_ip))
            checks["sink_exact_ip_connection_blocked"] = connection_blocked(sink_ip)
        except ValueError:
            checks["sink_exact_ip_connection_blocked"] = {
                "passed": False, "detail": "GAP_TEST_SINK_IP must be an IP address",
            }
    else:
        checks["sink_exact_ip_connection_blocked"] = {
            "passed": False, "detail": "required GAP_TEST_SINK_IP was not supplied",
        }
    try:
        token = Path("/run/gap-agent/agent-token").read_text().strip()
        # Plain HTTP is explicitly limited to this private-network demo. The
        # general client and host example retain HTTPS/loopback defaults.
        with GatewayClient(
            os.environ.get("GAP_GATEWAY_URL", "http://gateway:8090"), token,
            allow_insecure_http=True,
        ) as gateway:
            rejected = build_governed_graph(
                lambda state: [{"tool": "lookup", "target": "unapproved", "arguments": {}}],
                gateway, max_attempts=1,
            ).invoke({"objective": "Boundary fixture: reject an unapproved target."})
            checks["unapproved_target_rejected"] = {
                "passed": rejected["status"] == "rejected" and not rejected["completed"],
                "status": rejected["status"],
            }
            allowed = build_governed_graph(
                lambda state: [{"tool": "lookup", "target": "demo", "arguments": {}}],
                gateway,
            ).invoke({"objective": "Boundary fixture: read the allowed demo record."})
            checks["authorized_lookup_completed"] = {
                "passed": allowed["status"] == "completed" and allowed["completed"],
                "status": allowed["status"],
            }
    except Exception as exc:
        checks["gateway_fixture"] = {"passed": False, "detail": type(exc).__name__}
    passed = all(check["passed"] is not False for check in checks.values())
    print(json.dumps({"passed": passed, "checks": checks}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
