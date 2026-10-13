#!/usr/bin/env python3
"""Offline policy and rendered-configuration checks; never contacts cluster nodes."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
hosts = {'db1': {'ansible_host': '10.100.0.1'}, 'db2': {'ansible_host': '10.100.0.2'},
         'db3': {'ansible_host': '10.100.0.3'}, 'agent': {'ansible_host': '10.100.0.4'}}
groups = {'mysql_nodes': ['db1', 'db2', 'db3'], 'monitoring': ['agent']}
settings = yaml.safe_load((ROOT / 'inventory/group_vars/all.yml').read_text())
telegram_tasks = yaml.safe_load((ROOT / 'roles/telegram_vless/tasks/main.yml').read_text())
telegram_probe = next(task for task in telegram_tasks if task['name'] == 'Проверить HTTPS к Telegram через отдельный прокси')
probe_code = telegram_probe['ansible.builtin.command']['argv'][-1]
compile(probe_code, 'telegram_proxy_probe', 'exec')
assert 'NoRedirect' in probe_code and 'https://api.telegram.org/' in probe_code
alerting_handlers = yaml.safe_load((ROOT / 'roles/alerting/handlers/main.yml').read_text())
assert any(item['name'] == 'Restart monitoring Grafana' for item in alerting_handlers)
inventory = yaml.safe_load((ROOT / 'inventory/hosts.yml').read_text())
assert inventory['all']['children']['monitoring']['hosts']['agent']['ansible_connection'] == 'ssh'
for playbook_name in ('site', 'access', 'check', 'update', 'telegram', 'notifications',
                      'failover_view', 'notification_audit', 'telegram_dedupe',
                      'telegram_diagnostics', 'telegram_grafana_diagnostics',
                      'telegram_grafana_logs', 'telegram_template_render',
                      'performance'):
    plays = yaml.safe_load((ROOT / f'playbooks/{playbook_name}.yml').read_text())
    assert all('import_playbook' in play or
               '../inventory/group_vars/vault.yml' in play.get('vars_files', [])
               for play in plays), f'{playbook_name}: Vault file is not loaded by every play'
settings.update({'groups': groups, 'hostvars': hosts, 'ansible_host': '10.100.0.4',
                 'monitoring_bind_address': '10.100.0.4',
                 'mysql_discovery': {'network': 'mysql-existing', 'mysql_host': 'mysql-existing', 'port': 3306},
                 'vault_grafana_admin_user': 'admin', 'vault_grafana_admin_password': 'EXAMPLE',
                 'vault_mysql_exporter_user': 'exporter', 'vault_mysql_exporter_password': 'EXAMPLE',
                 'vault_orchestrator_api_user': '', 'vault_orchestrator_api_password': '',
                 'vault_telegram_bot_token': 'EXAMPLE', 'vault_telegram_chat_id': 'EXAMPLE'})
env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
env.filters['to_json'] = json.dumps
env.filters['extract'] = lambda key, obj, attr: obj[key][attr]
with tempfile.TemporaryDirectory(prefix='monitoring-config-') as temporary:
    for source in ROOT.glob('roles/*/templates/*'):
        if source.suffix != '.j2':
            continue
        rendered = env.from_string(source.read_text()).render(**settings)
        if source.name.endswith('json.j2'):
            json.loads(rendered)
        elif source.name != 'my.cnf.j2':
            yaml.safe_load(rendered)
        if source.name == 'compose.yml.j2':
            target = Path(temporary) / (source.parents[1].name + '.yml')
            target.write_text(rendered)
            subprocess.run(['docker', 'compose', '-f', str(target), 'config', '--quiet'],
                           check=True, capture_output=True, text=True)
    host_settings = dict(settings)
    host_settings['mysql_discovery'] = {'network': 'host', 'mysql_host': '127.0.0.1', 'port': 3306}
    host_mysql_compose = env.from_string(
        (ROOT / 'roles/mysql_exporter/templates/compose.yml.j2').read_text()
    ).render(**host_settings)
    host_compose_data = yaml.safe_load(host_mysql_compose)
    assert host_compose_data['services']['mysql-exporter']['network_mode'] == 'host'
    assert 'networks' not in host_compose_data
    assert 'ports' not in host_compose_data['services']['mysql-exporter']
    host_target = Path(temporary) / 'mysql-host-network.yml'
    host_target.write_text(host_mysql_compose)
    subprocess.run(['docker', 'compose', '-f', str(host_target), 'config', '--quiet'],
                   check=True, capture_output=True, text=True)
    proxy_compose = yaml.safe_load(env.from_string(
        (ROOT / 'roles/telegram_vless/templates/compose.yml.j2').read_text()
    ).render(**settings))
    assert 'ports' not in proxy_compose['services']['telegram-vless']
    proxy_settings = dict(settings, telegram_vless_enabled=True)
    grafana_compose = yaml.safe_load(env.from_string(
        (ROOT / 'roles/grafana/templates/compose.yml.j2').read_text()
    ).render(**proxy_settings))
    grafana_env = grafana_compose['services']['grafana']['environment']
    assert grafana_env['HTTPS_PROXY'] == 'http://telegram-vless:18080'
    assert 'prometheus' in grafana_env['NO_PROXY']
    prometheus_file = yaml.safe_load(env.from_string(
        (ROOT / 'roles/prometheus/templates/prometheus.yml.j2').read_text()
    ).render(**settings))
    assert prometheus_file['global']['scrape_interval'] == '10s'
    scrape_jobs = {job['job_name']: job for job in prometheus_file['scrape_configs']}
    assert scrape_jobs['orchestrator']['scrape_interval'] == '15s'
    telegram_file = yaml.safe_load(env.from_string(
        (ROOT / 'roles/alerting/templates/telegram.yml.j2').read_text()
    ).render(**settings))
    receiver = telegram_file['contactPoints'][0]['receivers'][0]
    assert receiver['uid'] == 'mysql_cluster_telegram'
    assert len(telegram_file['contactPoints'][0]['receivers']) == 1
    assert receiver['settings']['parse_mode'] == 'None'
    assert ':=' not in receiver['settings']['message']
    assert 'ℹ️ Тест уведомления' in receiver['settings']['message']
    assert telegram_file['policies'][0]['group_by'] == ['...']
    assert telegram_file['policies'][0]['group_wait'] == '0s'
    assert telegram_file['policies'][0]['group_interval'] == '5s'
    assert 'api.telegram.org' not in receiver['settings']['message']
    assert not any(word in receiver['settings']['message'] for word in ('DashboardURL', 'PanelURL', 'SilenceURL', 'ExternalURL'))
    assert 'DatasourceNoData' in receiver['settings']['message']
    assert 'DatasourceError' in receiver['settings']['message']
    assert {route['object_matchers'][0][2] for route in telegram_file['policies'][0]['routes']} == {
        'DatasourceNoData', 'DatasourceError'}
    assert all(route['receiver'] == 'telegram' for route in telegram_file['policies'][0]['routes'])

dashboards = list((ROOT / 'roles/dashboards/files').glob('*.json'))
assert len(dashboards) == 6, f'Expected six dashboards, found {len(dashboards)}'
for path in dashboards:
    board = json.loads(path.read_text())
    assert board['refresh'] == '10s'
    panels = [p for p in board['panels'] if p['type'] != 'row']
    assert panels and all(p['targets'][0]['expr'] for p in panels)
    assert len({p['id'] for p in board['panels']}) == len(board['panels'])
    occupied = set()
    for p in board['panels']:
        pos = p['gridPos']
        assert 0 <= pos['x'] < 24 and 0 < pos['w'] <= 24 and pos['x'] + pos['w'] <= 24
        cells = {(col, row) for col in range(pos['x'], pos['x'] + pos['w'])
                 for row in range(pos['y'], pos['y'] + pos['h'])}
        assert not (occupied & cells), f'{path.name}: overlapping panels'
        occupied.update(cells)
    for p in panels:
        assert '){' not in p['targets'][0]['expr'], f'{path.name}: selector after expression'
        if p['targets'][0]['expr'] == 'orchestrator_primary':
            assert '{{mysql_host}}' in p['targets'][0]['legendFormat']
        if path.stem in ('infrastructure', 'monitoring-health'):
            expr = p['targets'][0]['expr']
            assert 'server="agent"' not in expr
            if 'node_' in expr or 'job="node"' in expr:
                assert 'server=~"$server"' in expr or 'server=~"db[123]"' in expr
    if path.stem in ('cluster-overview', 'replication', 'orchestrator'):
        current_primary_panels = [p for p in panels if p['title'] == 'Текущий Primary']
        assert len(current_primary_panels) == 1
        assert 'count(' in current_primary_panels[0]['targets'][0]['expr']
        assert 'mysql_global_variables_read_only' in current_primary_panels[0]['targets'][0]['expr']
alerts = json.loads((ROOT / 'roles/alerting/files/alerts.json').read_text())
assert len(alerts['groups'][0]['rules']) == 15
assert alerts['groups'][0]['interval'] == '10s'
assert all(rule['noDataState'] == 'NoData' and rule['execErrState'] == 'Error'
           for rule in alerts['groups'][0]['rules'])
assert all(rule['annotations']['summary'] != rule['title'] for rule in alerts['groups'][0]['rules'])
assert all(rule['annotations']['resolved'] for rule in alerts['groups'][0]['rules'])
assert {r['labels']['severity'] for r in alerts['groups'][0]['rules']} == {'critical', 'warning', 'info'}
assert '== bool 0' in next(r['data'][0]['model']['expr'] for r in alerts['groups'][0]['rules']
                            if r['uid'] == 'multiple_writable')
by_uid = {r['uid']: r for r in alerts['groups'][0]['rules']}
assert all(rule['for'] == '5s' for rule in by_uid.values())
assert by_uid['multiple_writable']['data'][1]['model']['expression'] == '$A > 1'
assert by_uid['lag_warning']['data'][1]['model']['expression'] == '$A > 15 && $A <= 60'
assert by_uid['disk_low']['data'][1]['model']['expression'] == '$A > 80 && $A <= 90'
for uid in ('lag_critical', 'lag_warning', 'disk_critical', 'disk_low',
            'cpu_high', 'ram_high', 'connections_high'):
    assert by_uid[uid]['annotations']['value_unit'] in {' с', '%'}
for rule in alerts['groups'][0]['rules']:
    if rule['uid'] in {'cpu_high', 'ram_high', 'disk_low', 'disk_critical'}:
        assert 'server=~"db[123]"' in rule['data'][0]['model']['expr'], rule['uid']

# Fail closed if future role changes introduce commands that mutate the existing cluster.
tasks = '\n'.join(p.read_text() for p in ROOT.glob('roles/*/tasks/*.yml'))
for forbidden in [r'docker\s+(stop|rm|network\s+(rm|connect|disconnect)|volume\s+rm)',
                  r'(?i)\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|STOP\s+REPLICA|CHANGE\s+REPLICATION)\b']:
    assert not re.search(forbidden, tasks), forbidden
print('OK: rendered templates, six dashboards, 15 alerts, read-only task policy')
