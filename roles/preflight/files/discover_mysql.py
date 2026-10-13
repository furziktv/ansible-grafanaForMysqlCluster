#!/usr/bin/env python3
"""Read-only Docker inspection. Prints one JSON object; never calls Docker mutations."""
import json
import os
import socket
import subprocess
import sys


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=15)


try:
    ids = docker("ps", "--filter", "status=running", "--format", "{{.ID}}").splitlines()
    containers = json.loads(docker("inspect", *ids)) if ids else []
    requested = os.environ.get("MYSQL_CONTAINER_OVERRIDE", "")
    selected = []
    for item in containers:
        name = item["Name"].lstrip("/")
        image = (item["Config"].get("Image") or "").lower()
        labels = item["Config"].get("Labels") or {}
        ports = item["Config"].get("ExposedPorts") or {}
        looks_mysql = ("mysql" in image or "percona" in image or
                       "3306/tcp" in ports or "mysql" in name.lower())
        if (requested and name == requested) or (not requested and looks_mysql and
           "exporter" not in name.lower() and "orchestrator" not in name.lower()):
            selected.append(item)
    if len(selected) != 1:
        raise ValueError(f"Expected exactly one running MySQL container, found {[x['Name'] for x in selected]}; set mysql_container_override")
    item = selected[0]
    networks = item["NetworkSettings"]["Networks"]
    override = os.environ.get("MYSQL_NETWORK_OVERRIDE", "")
    if item["HostConfig"].get("NetworkMode") == "host":
        if override and override != "host":
            raise ValueError("MySQL uses host network; mysql_network_override must be empty or host")
        network = "host"
        mysql_host = None
    else:
        candidates = [override] if override else [x for x in networks if x not in ("host", "none", "bridge")]
        if len(candidates) != 1 or candidates[0] not in networks:
            raise ValueError(f"Cannot select one existing Docker network from {list(networks)}; set mysql_network_override")
        network = candidates[0]
        mysql_host = item["Name"].lstrip("/")
    configured_port = int(os.environ.get("MYSQL_PORT", "3306"))
    exposed = item["Config"].get("ExposedPorts") or {}
    tcp_ports = [int(value.split('/')[0]) for value in exposed if value.endswith('/tcp')]
    if configured_port in tcp_ports or not tcp_ports:
        port = configured_port
    elif len(tcp_ports) == 1:
        port = tcp_ports[0]
    else:
        raise ValueError(f"Cannot select MySQL port from {tcp_ports}; set mysql_port")
    if network == "host":
        candidates = list(dict.fromkeys(filter(None, ("127.0.0.1", os.environ.get("MYSQL_HOST_IP", "")))))
        for candidate in candidates:
            try:
                with socket.create_connection((candidate, port), timeout=2):
                    mysql_host = candidate
                    break
            except OSError:
                continue
        if mysql_host is None:
            raise ValueError(f"MySQL in host network is unreachable on port {port} at {candidates}")
    bindings = item["NetworkSettings"].get("Ports") or {}
    print(json.dumps({"container": item["Name"].lstrip("/"), "network": network,
                      "network_ip": networks.get(network, {}).get("IPAddress"), "port": port,
                      "published_ports": bindings.get(f"{port}/tcp") or [],
                      "image": item["Config"].get("Image"),
                      "mysql_host": mysql_host}))
except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(1)
