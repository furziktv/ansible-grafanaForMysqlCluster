#!/usr/bin/env python3
"""API behavior test using canned read-only responses, no cluster or Docker access."""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

script = Path(__file__).resolve().parents[1] / 'roles/orchestrator_exporter/files/exporter.py'
sys.dont_write_bytecode = True
with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / 'config.json'
    config.write_text(json.dumps({'urls': ['http://orc1', 'http://orc2'],
        'mysql_hosts': ['10.100.0.1', '10.100.0.2', '10.100.0.3'],
        'host_names': {'10.100.0.1': 'db1', '10.100.0.2': 'db2', '10.100.0.3': 'db3'},
        'cluster_hint': '', 'bind': '127.0.0.1', 'port': 9125}))
    os.environ['ORCHESTRATOR_EXPORTER_CONFIG'] = str(config)
    spec = importlib.util.spec_from_file_location('orchestrator_exporter', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    state = {'primary': '10.100.0.1', 'down': False, 'orc1_empty': False,
             'orc1_primary': None}

    def fake_get(base, path):
        if state['down']:
            raise OSError('unavailable')
        if path in ('masters', 'health', 'status'):
            if path != 'masters':
                return {'Code': 'OK'}
            if base == 'http://orc1' and state['orc1_empty']:
                return []
            primary = (state['orc1_primary'] if base == 'http://orc1' and
                       state['orc1_primary'] else state['primary'])
            return [{'Key': {'Hostname': primary}, 'ClusterName': 'mysql-cluster',
                     'IsLastCheckValid': True}]
        if path == 'cluster/mysql-cluster':
            return [{'Key': {'Hostname': ip}, 'MasterKey': {'Hostname': state['primary']},
                     'IsLastCheckValid': True, 'UsingOracleGTID': True}
                    for ip in ('10.100.0.1', '10.100.0.2', '10.100.0.3')]
        if path == 'replication-analysis':
            return []
        if path == 'audit-recovery':
            return []
        if path == 'check-global-recoveries':
            return {'Details': 'enabled'}
        raise OSError(path)

    module.get = fake_get
    first = module.collect()
    assert 'orchestrator_primary{mysql_host="10.100.0.1",server="db1"} 1' in first
    assert 'orchestrator_topology_known 1' in first
    assert 'orchestrator_recoveries_enabled 1' in first
    state['primary'] = '10.100.0.3'
    state['orc1_empty'] = True
    second = module.collect()
    assert 'orchestrator_primary{mysql_host="10.100.0.3",server="db3"} 1' in second
    assert 'orchestrator_primary_changes_total 1' in second
    assert 'orchestrator_topology_known 1' in second
    state['orc1_empty'] = False
    state['orc1_primary'] = '10.100.0.1'
    conflict = module.collect()
    assert 'orchestrator_primary_known 0' in conflict
    assert 'orchestrator_primary{' not in conflict
    assert 'orchestrator_endpoint_success{endpoint="topology"} 1' in conflict
    state['orc1_primary'] = None
    state['orc1_empty'] = True
    recovered = module.collect()
    assert 'orchestrator_primary{mysql_host="10.100.0.3",server="db3"} 1' in recovered
    state['down'] = True
    third = module.collect()
    assert 'orchestrator_topology_known 0' in third
    assert 'orchestrator_endpoint_success{endpoint="masters"} 0' in third
    assert 'orchestrator_api_up{endpoint="http://orc1"} 0' in third
print('OK: failover, empty/stale API and unreachable API keep Primary honest')
