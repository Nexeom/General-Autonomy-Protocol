"""Local HTTP processes for repeatable functional demonstrations.

These processes share an OS user. deploy/compose.yaml supplies a reference
isolation topology that must be verified in its deployment environment.
"""
import os
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

import httpx

from gap_kernel.gateway.provision import provision


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalDeployment:
    def __init__(self):
        parent = Path(__file__).resolve().parents[1] / ".gap-runs"
        parent.mkdir(exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(dir=parent)
        self.directory = Path(self._temporary.name)
        self.gateway_port, self.sink_port = free_port(), free_port()
        self.url = f"http://127.0.0.1:{self.gateway_port}"
        self.sink_url = f"http://127.0.0.1:{self.sink_port}"
        self.config = provision(self.directory / "config", tool_url=self.sink_url)
        self.token = (self.config / "agent/agent-token").read_text()
        self.approver = self.config / "operator/approver.json"
        self.processes = []
        self.streams = []

    def _start(self, module, arguments, url, name):
        stream = (self.directory / f"{name}-{len(self.streams)}.log").open("w")
        self.streams.append(stream)
        command = [sys.executable, "-m", module, *arguments]
        if os.name == "nt":
            # Launch the real interpreter, avoiding Windows venv redirectors
            # whose child may outlive the Popen handle. Load this environment's
            # site directory (including the editable package .pth) explicitly.
            site_path = sysconfig.get_path("purelib")
            bootstrap = (
                "import sys,site,runpy; "
                f"sys.path.insert(0,{site_path!r});site.addsitedir({site_path!r}); "
                "module=sys.argv.pop(1);runpy.run_module(module,run_name='__main__')"
            )
            command = [sys._base_executable, "-c", bootstrap, module, *arguments]
        process = subprocess.Popen(
            command, stdout=stream, stderr=stream,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.processes.append(process)
        deadline = time.monotonic() + 20
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} stopped during startup; inspect {stream.name}")
                try:
                    if client.get(url + "/health").status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        raise RuntimeError(f"{name} startup timed out")

    def start_gateway(self):
        self.gateway_process = self._start("gap_kernel.gateway.app", [
            "--config", str(self.config / "gateway/config.json"),
            "--state-dir", str(self.directory / "gateway-state"),
            "--port", str(self.gateway_port)], self.url, "gateway")

    def restart_gateway(self):
        self._stop(self.gateway_process)
        self.start_gateway()

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        process.terminate()
        process.wait(timeout=10)

    def __enter__(self):
        try:
            self._start("gap_kernel.gateway.sink", [
                "--token-file", str(self.config / "sink/tool-token"),
                "--db", str(self.directory / "sink-state/notes.sqlite"),
                "--port", str(self.sink_port)], self.sink_url, "sink")
            self.start_gateway()
            self.http = httpx.Client(base_url=self.url, trust_env=False, timeout=10,
                                     headers={"Authorization": "Bearer " + self.token})
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if hasattr(self, "http"):
            self.http.close()
        for process in reversed(self.processes):
            self._stop(process)
        for stream in self.streams:
            stream.close()
        for attempt in range(10):
            try:
                self._temporary.cleanup()
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1)

    def __exit__(self, *exc):
        self.close()


def propose(http, tool="lookup", target="demo", arguments=None, request_id=None):
    body = {"actions": [{"tool": tool, "target": target, "arguments": arguments or {}}]}
    if request_id:
        body["request_id"] = request_id
    response = http.post("/v1/proposals", json=body)
    response.raise_for_status()
    return response.json()
