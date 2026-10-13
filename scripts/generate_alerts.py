#!/usr/bin/env python3
"""Regenerate committed Grafana Unified Alerting provisioning JSON."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'roles/alerting/files/alerts.json'
OUT.parent.mkdir(parents=True, exist_ok=True)


def rule(uid, title, severity, expr, hold='2m', threshold=0, unit=None, condition=None):
    return {'uid': uid, 'title': title, 'condition': 'B', 'for': hold,
            'noDataState': 'NoData', 'execErrState': 'Error',
            'annotations': {'summary': title, 'description': 'Проверить метрики и состояние кластера. Мониторинг не управляет failover.',
                            **({'value_unit': unit,
                                'current_value': '{{ printf "%.1f" $values.A.Value }}'} if unit else {})},
            'labels': {'severity': severity, 'service': 'mysql-cluster'},
            'data': [
                {'refId': 'A', 'datasourceUid': 'prometheus', 'relativeTimeRange': {'from': 300, 'to': 0},
                 'model': {'refId': 'A', 'expr': expr, 'instant': True, 'range': False,
                           'datasource': {'type': 'prometheus', 'uid': 'prometheus'}, 'intervalMs': 1000,
                           'maxDataPoints': 43200}},
                {'refId': 'B', 'datasourceUid': '__expr__', 'relativeTimeRange': {'from': 0, 'to': 0},
                 'model': {'refId': 'B', 'expression': condition or f'$A > {threshold}', 'type': 'math',
                           'datasource': {'type': '__expr__', 'uid': '__expr__'},
                           'intervalMs': 1000, 'maxDataPoints': 43200}}
            ]}


io = '(mysql_slave_status_slave_io_running or mysql_slave_status_replica_io_running)'
sql = '(mysql_slave_status_slave_sql_running or mysql_slave_status_replica_sql_running)'
lag = '(mysql_slave_status_seconds_behind_master or mysql_slave_status_seconds_behind_source)'
root_disk = '100 * (1 - node_filesystem_avail_bytes{mountpoint="/",server=~"db[123]"} / node_filesystem_size_bytes{mountpoint="/",server=~"db[123]"})'
mysql_scrape_down = '(up{job="mysql",server=~"db[123]"} == bool 0)'
mysql_service_down = '(mysql_up{server=~"db[123]"} == bool 0)'
mysql_down = f'max by (server) (({mysql_scrape_down} + on(server) {mysql_service_down}) or on(server) {mysql_scrape_down})'
io_stopped = f'label_replace(({io} == bool 0), "check", "io", "server", ".*")'
sql_stopped = f'label_replace(({sql} == bool 0), "check", "sql", "server", ".*")'
topology_unhealthy = ('label_replace((orchestrator_instance_healthy{server!="unknown",role="replica"} '
                      '== bool 0), "check", "topology", "server", ".*")')
rules = [
    rule('primary_down', 'Primary Down', 'critical',
         'max((orchestrator_primary * on(server) group_left() (1 - mysql_up)) '
         'or (orchestrator_primary_known == bool 0))'),
    rule('mysql_down', 'MySQL Down', 'critical', mysql_down),
    rule('replication_stopped', 'Replication Stopped', 'critical',
         f'max by (server) ({io_stopped} or {sql_stopped} or {topology_unhealthy})'),
    rule('lag_critical', 'Replication Lag Critical', 'critical', lag, '2m', 60, ' с'),
    rule('orchestrator_down', 'Orchestrator Down', 'critical',
         'max((max(orchestrator_api_up) == bool 0) or (up{job="orchestrator"} == bool 0))', '1m'),
    rule('disk_critical', 'Disk Space Critical', 'critical', root_disk, '5m', 90, '%'),
    rule('multiple_writable', 'Multiple Writable Primaries', 'critical',
         'sum(mysql_global_variables_read_only{server=~"db[123]"} == bool 0)', '1m', 1, ' узла'),
    rule('lag_warning', 'Replication Lag Warning', 'warning', lag, '5m', 15, ' с', '$A > 15 && $A <= 60'),
    rule('cpu_high', 'CPU High', 'warning', '100 * (1 - avg by (server) (rate(node_cpu_seconds_total{mode="idle",server=~"db[123]"}[5m])))', '10m', 85, '%'),
    rule('ram_high', 'RAM High', 'warning', '100 * (1 - node_memory_MemAvailable_bytes{server=~"db[123]"} / node_memory_MemTotal_bytes{server=~"db[123]"})', '10m', 90, '%'),
    rule('disk_low', 'Disk Space Low', 'warning', root_disk, '10m', 80, '%', '$A > 80 && $A <= 90'),
    rule('connections_high', 'Connection Usage High', 'warning',
         '100 * mysql_global_status_threads_connected / mysql_global_variables_max_connections', '5m', 80, '%'),
    rule('primary_changed', 'Primary Changed', 'info', 'increase(orchestrator_primary_changes_total[5m])', '0s'),
    rule('failover_detected', 'Failover Detected', 'info',
         '((time() - orchestrator_last_failover_timestamp_seconds) < bool 300) * '
         '(orchestrator_last_failover_timestamp_seconds > bool 0)', '0s'),
    rule('server_recovered', 'Server Recovered', 'info',
         '(changes(mysql_up[5m]) > bool 0) * (mysql_up == bool 1)', '0s'),
]
summaries = {
    'primary_down': 'Не подтверждена доступность Primary',
    'mysql_down': 'MySQL или его exporter недоступен',
    'replication_stopped': 'Репликация остановлена',
    'lag_critical': 'Задержка репликации больше 60 с',
    'orchestrator_down': 'Orchestrator API или exporter недоступен',
    'disk_critical': 'Диск / заполнен более чем на 90%',
    'multiple_writable': 'Несколько узлов доступны для записи',
    'lag_warning': 'Задержка репликации больше 15 с',
    'cpu_high': 'Загрузка CPU выше 85%',
    'ram_high': 'Использование RAM выше 90%',
    'disk_low': 'Диск / заполнен более чем на 80%',
    'connections_high': 'Соединения MySQL выше 80% лимита',
    'primary_changed': 'Primary сменился',
    'failover_detected': 'Обнаружено переключение Primary',
    'server_recovered': 'Сервер восстановился',
}
resolved_messages = {
    'primary_down': 'Primary снова доступен',
    'mysql_down': 'MySQL снова доступен',
    'replication_stopped': 'Репликация восстановлена',
    'lag_critical': 'Задержка репликации ниже 60 с',
    'orchestrator_down': 'Orchestrator снова доступен',
    'disk_critical': 'Критический порог диска больше не превышен',
    'multiple_writable': 'Остался один доступный для записи узел',
    'lag_warning': 'Задержка репликации снизилась',
    'cpu_high': 'Загрузка CPU снизилась',
    'ram_high': 'Использование RAM снизилось',
    'disk_low': 'На диске снова достаточно места',
    'connections_high': 'Число соединений MySQL снизилось',
    'primary_changed': 'Смена Primary завершена',
    'failover_detected': 'Событие failover завершено',
    'server_recovered': 'Восстановление сервера завершено',
}
for item in rules:
    item['annotations']['summary'] = summaries[item['uid']]
    item['annotations']['resolved'] = resolved_messages[item['uid']]
OUT.write_text(json.dumps({'apiVersion': 1, 'groups': [{'orgId': 1, 'name': 'MySQL Cluster',
    'folder': 'MySQL Cluster', 'interval': '1m', 'rules': rules}]}, ensure_ascii=False, indent=2) + '\n')
