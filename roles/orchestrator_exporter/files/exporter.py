#!/usr/bin/env python3
"""Small read-only Orchestrator API exporter. GET requests only; stdlib only."""
import base64
import datetime as dt
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = json.load(open(os.environ.get('ORCHESTRATOR_EXPORTER_CONFIG',
                                    '/etc/orchestrator-exporter/config.json'), encoding='utf-8'))
LOCK = threading.Lock()
STATE = {'primary': None, 'cluster': None, 'changes': 0}


def label(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def metric(name, value, **labels):
    suffix = '{' + ','.join(f'{k}="{label(v)}"' for k, v in labels.items()) + '}' if labels else ''
    return f'{name}{suffix} {value}\n'


def get(base, path):
    url = base.rstrip('/') + '/api/' + path
    req = urllib.request.Request(url, method='GET', headers={'Accept': 'application/json'})
    user = CONFIG.get('username')
    if user:
        encoded = base64.b64encode(f"{user}:{CONFIG.get('password', '')}".encode()).decode()
        req.add_header('Authorization', 'Basic ' + encoded)
    with urllib.request.urlopen(req, timeout=CONFIG.get('timeout', 3)) as response:
        body = response.read(2_000_000)
        try:
            return json.loads(body)
        except ValueError:
            return body.decode(errors='replace')


def key_name(value):
    if isinstance(value, dict):
        return str(value.get('Hostname') or value.get('hostname') or '')
    return str(value or '')


def server_name(value):
    names = CONFIG['host_names']
    return names.get(value, names.get(value.split('.')[0].lower(), 'unknown'))


def timestamp(value):
    if not value:
        return 0
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0


def collect():
    out = ['# Orchestrator API exporter: unknown endpoints report success=0.\n']
    urls = CONFIG['urls']
    def probe(url):
        try:
            return get(url, 'masters')
        except (OSError, ValueError, urllib.error.HTTPError):
            return None

    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        probes = list(pool.map(probe, urls))
    for url, result in zip(urls, probes):
        out.append(metric('orchestrator_api_up', int(isinstance(result, list)), endpoint=url))
    wanted = set(CONFIG['mysql_hosts'])

    def cluster_masters(result):
        if not isinstance(result, list):
            return []
        return [row for row in result if isinstance(row, dict) and
                (key_name(row.get('Key')) in wanted or
                 server_name(key_name(row.get('Key'))) != 'unknown')]

    # An API can answer HTTP 200 with an empty or stale /masters response
    # while another Orchestrator already knows the promoted primary.
    candidates = [(i, cluster_masters(result)) for i, result in enumerate(probes)
                  if isinstance(result, list)]
    healthy = [(i, rows[0]) for i, rows in candidates if len(rows) == 1 and
               rows[0].get('IsLastCheckValid') is not False]
    names = {key_name(row.get('Key')) for _, row in healthy}
    chosen = next(((i, row) for i, row in healthy), None) if len(names) == 1 else None
    active_index = (chosen[0] if chosen else
                    next((i for i, rows in candidates if rows), None))
    if active_index is None:
        active_index = next((i for i, result in enumerate(probes)
                             if isinstance(result, list)), None)
    if active_index is None:
        for endpoint in ('masters', 'topology', 'replication_analysis', 'audit_recovery', 'global_recoveries'):
            out.append(metric('orchestrator_endpoint_success', 0, endpoint=endpoint))
        out.append(metric('orchestrator_primary_known', 0))
        out.append(metric('orchestrator_topology_known', 0))
        return ''.join(out)
    active = urls[active_index]

    def fetch(name, path):
        try:
            result = get(active, path)
            out.append(metric('orchestrator_endpoint_success', 1, endpoint=name))
            return result
        except (OSError, ValueError, urllib.error.HTTPError):
            out.append(metric('orchestrator_endpoint_success', 0, endpoint=name))
            return None

    masters = probes[active_index]
    out.append(metric('orchestrator_endpoint_success', 1, endpoint='masters'))
    selected = cluster_masters(masters)
    primary_known = chosen is not None
    out.append(metric('orchestrator_primary_known', int(primary_known)))
    if primary_known:
        primary = key_name(chosen[1].get('Key'))
        if STATE['primary'] and STATE['primary'] != primary:
            STATE['changes'] += 1
        STATE['primary'] = primary
        out.append(metric('orchestrator_primary', 1, mysql_host=primary,
                          server=server_name(primary)))
        hint = CONFIG.get('cluster_hint') or chosen[1].get('ClusterName') or primary
        STATE['cluster'] = hint
    else:
        hint = CONFIG.get('cluster_hint') or STATE['cluster']
        if not hint and len(selected) == 1:
            hint = selected[0].get('ClusterName')
    if not hint:
        try:
            clusters = get(active, 'clusters')
            if isinstance(clusters, list) and len(clusters) == 1:
                hint = clusters[0] if isinstance(clusters[0], str) else None
        except (OSError, ValueError, urllib.error.HTTPError):
            pass
    out.append(metric('orchestrator_primary_changes_total', STATE['changes']))
    paths = [('topology', 'cluster/' + urllib.parse.quote(str(hint), safe=''))] if hint else []
    paths.extend([('replication_analysis', 'replication-analysis'),
                  ('audit_recovery', 'audit-recovery'),
                  ('global_recoveries', 'check-global-recoveries')])
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        results = dict(zip((name for name, _ in paths), pool.map(lambda pair: fetch(*pair), paths)))
    if not hint:
        out.append(metric('orchestrator_endpoint_success', 0, endpoint='topology'))
    topology = results.get('topology')
    out.append(metric('orchestrator_topology_known', int(isinstance(topology, list) and
                                                      len(topology) >= len(set(CONFIG['host_names'].values())) and
                                                      primary_known)))
    if isinstance(topology, list):
        for row in topology:
            instance = key_name(row.get('Key'))
            if instance not in wanted and server_name(instance) == 'unknown':
                continue
            source = key_name(row.get('MasterKey'))
            role = 'primary' if primary_known and instance == primary else 'replica' if source else 'unknown'
            server = server_name(instance)
            out.append(metric('orchestrator_instance_info', 1, mysql_host=instance, source=source, role=role, server=server))
            if isinstance(row.get('IsLastCheckValid'), bool):
                out.append(metric('orchestrator_instance_healthy', int(row['IsLastCheckValid']), mysql_host=instance, server=server, role=role))
            problems = row.get('Problems')
            if isinstance(problems, list):
                out.append(metric('orchestrator_instance_problems', len(problems), mysql_host=instance, server=server))
            gtid = row.get('UsingOracleGTID')
            if isinstance(gtid, bool):
                out.append(metric('orchestrator_instance_gtid_enabled', int(gtid), mysql_host=instance, server=server))
    analysis = results['replication_analysis']
    if isinstance(analysis, list):
        out.append(metric('orchestrator_replication_problems', len(analysis)))
        for item in analysis[:30]:
            instance = key_name(item.get('AnalyzedInstanceKey'))
            kind = item.get('Analysis', 'unknown')
            out.append(metric('orchestrator_analysis', 1, mysql_host=instance, server=server_name(instance), analysis=kind))
    audit = results['audit_recovery']
    if isinstance(audit, list):
        out.append(metric('orchestrator_recovery_history_count', len(audit)))
        successes = [x for x in audit if x.get('IsSuccessful') or x.get('SuccessorKey')]
        out.append(metric('orchestrator_failover_history_count', len(successes)))
        times = [timestamp(x.get('RecoveryStartTimestamp') or x.get('RecoveryEndTimestamp')) for x in successes]
        out.append(metric('orchestrator_last_failover_timestamp_seconds', max(times, default=0)))
        for event in audit[:20]:
            when = timestamp(event.get('RecoveryStartTimestamp') or event.get('RecoveryEndTimestamp'))
            if when:
                out.append(metric('orchestrator_recovery_event_timestamp_seconds', when,
                                  recovery_id=event.get('UID', event.get('Id', 'unknown')),
                                  successor=key_name(event.get('SuccessorKey')),
                                  analysis=event.get('Analysis', 'unknown')))
    recoveries = results['global_recoveries']
    if recoveries is not None:
        value = recoveries.get('Enabled') if isinstance(recoveries, dict) else recoveries
        if value is None and isinstance(recoveries, dict):
            message = (str(recoveries.get('Details', '')) + ' ' + str(recoveries.get('Message', ''))).lower()
            if 'disabled' in message:
                value = False
            elif 'enabled' in message:
                value = True
        if isinstance(value, bool):
            out.append(metric('orchestrator_recoveries_enabled', int(value)))
        elif isinstance(value, str) and value.lower() in ('true', 'false'):
            out.append(metric('orchestrator_recoveries_enabled', int(value.lower() == 'true')))
        else:
            out.append(metric('orchestrator_recoveries_state_known', 0))
    return ''.join(out)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/metrics':
            self.send_error(404)
            return
        with LOCK:
            body = collect().encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    ThreadingHTTPServer((CONFIG['bind'], CONFIG['port']), Handler).serve_forever()
