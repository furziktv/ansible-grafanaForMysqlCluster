#!/usr/bin/env python3
"""Generate six provisioned Grafana dashboards with a consistent 24-column layout."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'roles/dashboards/files'
OUT.mkdir(parents=True, exist_ok=True)
DS = {'type': 'prometheus', 'uid': 'prometheus'}
LINKS = [('Обзор', 'cluster-overview'), ('Репликация', 'replication'),
         ('MySQL', 'mysql-performance'), ('Инфраструктура', 'infrastructure'),
         ('Orchestrator', 'orchestrator'), ('Мониторинг', 'monitoring-health')]


def thresholds(*steps):
    return {'mode': 'absolute', 'steps': [{'color': color, 'value': value} for value, color in steps]}


GOOD_ONE = thresholds((None, 'red'), (1, 'green'))
GOOD_ZERO = thresholds((None, 'green'), (1, 'red'))
PERCENT_HIGH = thresholds((None, 'green'), (80, 'orange'), (90, 'red'))
LAG = thresholds((None, 'green'), (15, 'orange'), (60, 'red'))


def mappings(zero='ПРОБЛЕМА', one='НОРМА'):
    return [{'type': 'value', 'options': {
        '0': {'text': zero, 'color': 'red'},
        '1': {'text': one, 'color': 'green'},
    }}]


def _place(width, height):
    global x, y, row_height
    if x + width > 24:
        y += row_height
        x = 0
        row_height = 0
    position = {'x': x, 'y': y, 'w': width, 'h': height}
    x += width
    row_height = max(row_height, height)
    return position


def section(title):
    global x, y, row_height, next_id
    if x:
        y += row_height
        x = 0
        row_height = 0
    current['panels'].append({
        'id': next_id, 'type': 'row', 'title': title, 'collapsed': False,
        'panels': [], 'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1},
    })
    next_id += 1
    y += 1


def panel(title, expr, kind='timeseries', width=None, height=None, unit='short',
          legend=None, description=None, limits=None, colors=None, values=None,
          primary=False):
    global next_id
    width = width or {'stat': 6, 'gauge': 8, 'timeseries': 12, 'table': 12}[kind]
    height = height or {'stat': 5, 'gauge': 7, 'timeseries': 9, 'table': 8}[kind]
    defaults = {
        'unit': unit, 'noValue': 'НЕТ ДАННЫХ',
        'color': {'mode': 'thresholds' if colors else 'palette-classic'},
    }
    if colors:
        defaults['thresholds'] = colors
    if values:
        defaults['mappings'] = values
    if limits:
        defaults.update({'min': limits[0], 'max': limits[1]})
    target = {
        'expr': expr, 'refId': 'A', 'legendFormat': legend or '{{server}}',
        'instant': kind in ('stat', 'gauge', 'table'), 'range': kind == 'timeseries',
    }
    if kind == 'table':
        target['format'] = 'table'
    item = {
        'id': next_id, 'title': title, 'type': kind, 'datasource': DS,
        'gridPos': _place(width, height), 'targets': [target],
        'fieldConfig': {'defaults': defaults, 'overrides': []},
    }
    if description:
        item['description'] = description
    if kind == 'stat':
        item['options'] = {
            'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
            'orientation': 'auto', 'textMode': 'name' if primary else 'value_and_name',
            'colorMode': 'none' if primary else 'background',
            'graphMode': 'none' if primary else 'area',
        }
    elif kind == 'gauge':
        item['options'] = {
            'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
            'orientation': 'horizontal', 'showThresholdLabels': False,
            'showThresholdMarkers': True,
        }
    elif kind == 'table':
        item['options'] = {'showHeader': True, 'cellHeight': 'sm'}
    else:
        defaults['custom'] = {
            'drawStyle': 'line', 'lineInterpolation': 'smooth', 'lineWidth': 2,
            'fillOpacity': 12, 'showPoints': 'never',
            'stacking': {'mode': 'none', 'group': 'A'},
            'thresholdsStyle': {'mode': 'line' if colors else 'off'},
        }
        item['options'] = {
            'tooltip': {'mode': 'multi', 'sort': 'desc'},
            'legend': {'showLegend': True, 'displayMode': 'table',
                       'placement': 'bottom', 'calcs': ['lastNotNull']},
        }
    current['panels'].append(item)
    next_id += 1


def dashboard(slug, title, description, server_variable=False):
    global current, next_id, x, y, row_height
    next_id, x, y, row_height = 1, 0, 0, 0
    current = {
        'uid': slug, 'title': title, 'description': description,
        'tags': ['mysql', 'replication', 'monitoring'], 'timezone': 'browser',
        'schemaVersion': 40, 'version': 2, 'refresh': '15s',
        'time': {'from': 'now-6h', 'to': 'now'}, 'graphTooltip': 1,
        'panels': [], 'links': [
            {'title': name, 'type': 'link', 'url': f'/d/{uid}', 'targetBlank': False}
            for name, uid in LINKS if uid != slug
        ],
        'templating': {'list': []},
    }
    if server_variable:
        current['templating']['list'].append({
            'name': 'server', 'label': 'DB узлы', 'type': 'query', 'datasource': DS,
            'query': {'query': 'label_values(up{job="mysql",server=~"db[123]"}, server)',
                      'refId': 'PrometheusVariableQueryEditor-VariableQuery'},
            'refresh': 1, 'sort': 1, 'multi': True, 'includeAll': True,
            'allValue': 'db[123]', 'current': {'text': 'All', 'value': '$__all'},
        })


def save(slug):
    (OUT / f'{slug}.json').write_text(json.dumps(current, ensure_ascii=False, indent=2) + '\n')


lag = '(mysql_slave_status_seconds_behind_master or mysql_slave_status_seconds_behind_source)'
io = '(mysql_slave_status_slave_io_running or mysql_slave_status_replica_io_running)'
sql = '(mysql_slave_status_slave_sql_running or mysql_slave_status_replica_sql_running)'
db = 'server=~"db[123]"'
selected = 'server=~"$server"'
lag_selected = f'(mysql_slave_status_seconds_behind_master{{{selected}}} or mysql_slave_status_seconds_behind_source{{{selected}}})'
io_selected = f'(mysql_slave_status_slave_io_running{{{selected}}} or mysql_slave_status_replica_io_running{{{selected}}})'
sql_selected = f'(mysql_slave_status_slave_sql_running{{{selected}}} or mysql_slave_status_replica_sql_running{{{selected}}})'
readonly = 'mysql_global_variables_read_only'
mysql_status = '(mysql_up{server=~"db[123]"} or on(instance,job,server) (up{job="mysql",server=~"db[123]"} * 0))'
writable = ('(mysql_global_variables_read_only{server=~"db[123]"} == 0) '
            'and on(server) (mysql_up{server=~"db[123]"} == 1) '
            'and on(server) (up{job="mysql",server=~"db[123]"} == 1)')
current_primary = f'({writable}) and on() (count({writable}) == 1)'


dashboard('cluster-overview', 'Cluster Overview', 'Роль узлов и текущее состояние кластера MySQL.')
section('01  /  КЛАСТЕР СЕЙЧАС')
panel('Текущий Primary', current_primary, 'stat', legend='{{server}}', primary=True,
      description='Единственный доступный для записи MySQL. При неоднозначности показывается НЕТ ДАННЫХ.')
for name in ('db1', 'db2', 'db3'):
    status = (f'(mysql_up{{server="{name}"}} or '
              f'(up{{job="mysql",server="{name}"}} * 0))')
    panel(name.upper(), status, 'stat', colors=GOOD_ONE, values=mappings('НЕДОСТУПЕН', 'РАБОТАЕТ'))
panel('Состояние кластера', f'min({mysql_status}) * (max(orchestrator_topology_known) or vector(0))', 'stat', colors=GOOD_ONE, values=mappings())
panel('Реплики', 'min(orchestrator_instance_healthy{role="replica"})', 'stat', colors=GOOD_ONE, values=mappings('ПРОБЛЕМА', 'ЗДОРОВЫ'))
panel('Доступные API', 'sum(orchestrator_api_up)', 'stat', colors=thresholds((None, 'red'), (1, 'orange'), (3, 'green')), description='Число доступных API Orchestrator из трёх.')
panel('Writable узлы', f'sum({readonly} == bool 0)', 'stat', colors=thresholds((None, 'red'), (1, 'green'), (2, 'red')), description='В норме ровно один writable узел.')
section('02  /  РЕПЛИКАЦИЯ')
panel('Задержка реплик', lag, width=16, unit='s', legend='{{server}}', colors=LAG, description='Порог предупреждения 15 с, критический 60 с.')
panel('Топология', 'orchestrator_instance_info', 'table', width=8, description='Актуальные роли и источники по API Orchestrator.')
section('03  /  НАГРУЗКА И СОБЫТИЯ')
panel('Запросы в секунду', f'rate(mysql_global_status_queries{{{db}}}[5m])', legend='{{server}}', unit='ops')
panel('Активные соединения', f'mysql_global_status_threads_connected{{{db}}}', legend='{{server}}')
panel('Последний failover', 'orchestrator_last_failover_timestamp_seconds * 1000', 'stat', width=12, unit='dateTimeAsIso', values=[{'type': 'value', 'options': {'0': {'text': 'НЕТ СОБЫТИЙ'}}}])
panel('Изменения Primary', 'increase(orchestrator_primary_changes_total[24h])', 'stat', width=12, description='Смена Primary за последние 24 часа, замеченная exporter.')
save('cluster-overview')


dashboard('replication', 'Replication', 'Топология, задержка и потоки репликации.', True)
section('01  /  ТОПОЛОГИЯ')
panel('Текущий Primary', current_primary, 'stat', width=8, legend='{{server}}', primary=True)
panel('Реплик в топологии', 'count(orchestrator_instance_info{role="replica"})', 'stat', width=8)
panel('Максимальная задержка', f'max({lag_selected})', 'stat', width=8, unit='s', colors=LAG)
panel('Узлы и источники', f'orchestrator_instance_info{{{selected}}}', 'table', width=24, height=9, description='Топология меняется автоматически после failover.')
section('02  /  СОСТОЯНИЕ РЕПЛИКАЦИИ')
panel('Replication Lag', lag_selected, width=24, height=10, unit='s', colors=LAG)
panel('IO поток', io_selected, colors=GOOD_ONE, limits=(0, 1))
panel('SQL поток', sql_selected, colors=GOOD_ONE, limits=(0, 1))
panel('Ошибки IO', 'mysql_slave_status_last_io_errno or mysql_slave_status_last_io_error_number', 'table')
panel('Ошибки SQL', 'mysql_slave_status_last_sql_errno or mysql_slave_status_last_sql_error_number', 'table')
section('03  /  РОЛИ И GTID')
panel('read_only', f'mysql_global_variables_read_only{{{selected}}}', 'table')
panel('super_read_only', f'mysql_global_variables_super_read_only{{{selected}}}', 'table')
panel('GTID включён', f'orchestrator_instance_gtid_enabled{{{selected}}}', 'table')
panel('Проблемы узлов', f'orchestrator_instance_problems{{{selected}}}', 'table')
save('replication')


dashboard('mysql-performance', 'MySQL Performance', 'Нагрузка и внутренние метрики MySQL.', True)
sf = f'{{{selected}}}'
qps = f'rate(mysql_global_status_queries{sf}[5m])'
tps = f'sum by (server) (rate(mysql_global_status_commands_total{{command=~"commit|rollback",{selected}}}[5m]))'
reads = f'sum by (server) (rate(mysql_global_status_commands_total{{command="select",{selected}}}[5m]))'
writes = f'sum by (server) (rate(mysql_global_status_commands_total{{command=~"insert|update|delete",{selected}}}[5m]))'
section('01  /  НАГРУЗКА')
panel('QPS · всего', f'sum({qps})', 'stat', unit='ops')
panel('TPS · всего', f'sum({tps})', 'stat', unit='ops')
panel('Соединений', f'sum(mysql_global_status_threads_connected{sf})', 'stat')
panel('Медленных / с', f'sum(rate(mysql_global_status_slow_queries{sf}[5m]))', 'stat', unit='ops')
panel('Запросы в секунду', qps, legend='{{server}}', unit='ops')
panel('Транзакции в секунду', tps, legend='{{server}}', unit='ops')
panel('Чтения', reads, legend='{{server}}', unit='ops')
panel('Записи', writes, legend='{{server}}', unit='ops')
section('02  /  СОЕДИНЕНИЯ И INNODB')
panel('Соединения', f'mysql_global_status_threads_connected{sf}', legend='{{server}}')
panel('Активные потоки', f'mysql_global_status_threads_running{sf}', legend='{{server}}')
panel('Buffer Pool Hit %', f'100 * (1 - rate(mysql_global_status_innodb_buffer_pool_reads{sf}[5m]) / clamp_min(rate(mysql_global_status_innodb_buffer_pool_read_requests{sf}[5m]), 1))', legend='{{server}}', unit='percent', limits=(0, 100))
panel('Deadlocks / с', f'rate(mysql_global_status_innodb_deadlocks{sf}[5m])', legend='{{server}}', unit='ops')
section('03  /  МЕДЛЕННЫЕ ЗАПРОСЫ И БЛОКИРОВКИ')
panel('Медленные запросы / с', f'rate(mysql_global_status_slow_queries{sf}[5m])', legend='{{server}}', unit='ops')
panel('Ожидания row lock / с', f'rate(mysql_global_status_innodb_row_lock_waits{sf}[5m])', legend='{{server}}', unit='ops')
panel('Временные таблицы на диске / с', f'rate(mysql_global_status_created_tmp_disk_tables{sf}[5m])', legend='{{server}}', unit='ops')
panel('Ошибки подключения / с', f'rate(mysql_global_status_aborted_connects{sf}[5m])', legend='{{server}}', unit='ops')
panel('Версии MySQL', f'mysql_version_info{{{selected}}}', 'table', width=12)
panel('Uptime MySQL', f'mysql_global_status_uptime{sf}', 'table', width=12, unit='s')
save('mysql-performance')


dashboard('infrastructure', 'Infrastructure', 'Ресурсы DB1–DB3. Agent исключён из показателей.', True)
cpu = f'100 * (1 - avg by (server) (rate(node_cpu_seconds_total{{mode="idle",{selected}}}[5m])))'
ram = f'100 * (1 - node_memory_MemAvailable_bytes{{{selected}}} / node_memory_MemTotal_bytes{{{selected}}})'
swap = f'100 * (1 - node_memory_SwapFree_bytes{{{selected}}} / clamp_min(node_memory_SwapTotal_bytes{{{selected}}}, 1))'
disk = f'100 * (1 - node_filesystem_avail_bytes{{mountpoint="/",{selected}}} / node_filesystem_size_bytes{{mountpoint="/",{selected}}})'
disk_io = f'sum by (server) (rate(node_disk_read_bytes_total{{{selected}}}[5m]) + rate(node_disk_written_bytes_total{{{selected}}}[5m]))'
network = f'sum by (server) (rate(node_network_receive_bytes_total{{device!~"lo|veth.*|docker.*|br-.*",{selected}}}[5m]) + rate(node_network_transmit_bytes_total{{device!~"lo|veth.*|docker.*|br-.*",{selected}}}[5m]))'
section('01  /  РЕСУРСЫ БАЗ ДАННЫХ')
panel('CPU %', cpu, 'gauge', unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
panel('RAM %', ram, 'gauge', unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
panel('Диск / %', disk, 'gauge', unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
section('02  /  ИСТОРИЯ НАГРУЗКИ')
panel('CPU % · история', cpu, unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
panel('RAM % · история', ram, unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
panel('Swap %', swap, unit='percent', limits=(0, 100))
panel('Диск / %', disk, unit='percent', limits=(0, 100), colors=PERCENT_HIGH)
section('03  /  ВВОД-ВЫВОД И СЕТЬ')
panel('Диск · байт/с', disk_io, unit='Bps')
panel('Сеть · байт/с', network, unit='Bps')
panel('Uptime хостов', f'time() - node_boot_time_seconds{{{selected}}}', 'table', width=24, unit='s')
save('infrastructure')


dashboard('orchestrator', 'Orchestrator', 'API, топология, проблемы и история восстановления.')
section('01  /  API И РОЛЬ КЛАСТЕРА')
panel('Текущий Primary', current_primary, 'stat', width=8, legend='{{server}}', primary=True)
panel('API доступны', 'sum(orchestrator_api_up)', 'stat', width=8, colors=thresholds((None, 'red'), (1, 'orange'), (3, 'green')))
panel('Recoveries', 'orchestrator_recoveries_enabled', 'stat', width=8, colors=GOOD_ONE, values=mappings('ОТКЛЮЧЕНЫ', 'ВКЛЮЧЕНЫ'))
panel('Доступность API по узлам', 'orchestrator_api_up', 'table', width=12, legend='{{endpoint}}')
panel('Совместимость API', 'orchestrator_endpoint_success', 'table', width=12, legend='{{endpoint}}')
section('02  /  ТОПОЛОГИЯ И ПРОБЛЕМЫ')
panel('Топология', 'orchestrator_instance_info', 'table', width=24)
panel('Недоступные инстансы', 'orchestrator_instance_healthy == 0', 'table', width=12)
panel('Replication Analysis', 'orchestrator_analysis', 'table', width=12)
panel('Проблемы репликации', 'orchestrator_replication_problems', 'stat', width=12, colors=GOOD_ZERO)
panel('Проблемы узлов', 'orchestrator_instance_problems', 'table', width=12)
section('03  /  ИСТОРИЯ RECOVERY')
panel('Failover событий', 'orchestrator_failover_history_count', 'stat', width=8)
panel('Recovery событий', 'orchestrator_recovery_history_count', 'stat', width=8)
panel('Последний failover', 'orchestrator_last_failover_timestamp_seconds * 1000', 'stat', width=8, unit='dateTimeAsIso', values=[{'type': 'value', 'options': {'0': {'text': 'НЕТ СОБЫТИЙ'}}}])
panel('История recovery', 'orchestrator_recovery_event_timestamp_seconds', 'table', width=24)
save('orchestrator')


dashboard('monitoring-health', 'Monitoring Health', 'Состояние мониторинга DB1–DB3 без ресурсных показателей Agent.')
section('01  /  СБОР МЕТРИК')
panel('Prometheus', 'up{job="prometheus"}', 'stat', colors=GOOD_ONE, values=mappings('НЕДОСТУПЕН', 'РАБОТАЕТ'))
panel('Orchestrator Exporter', 'up{job="orchestrator"}', 'stat', colors=GOOD_ONE, values=mappings('НЕДОСТУПЕН', 'РАБОТАЕТ'))
panel('DB exporters UP', 'sum(up{job=~"mysql|node",server=~"db[123]"})', 'stat', colors=thresholds((None, 'red'), (6, 'green')), description='Шесть exporters: Node и MySQL на каждом DB-узле.')
panel('Ошибки scrapes', 'sum(up{job=~"mysql|node",server=~"db[123]"} == bool 0)', 'stat', colors=GOOD_ZERO)
section('02  /  УЗЛЫ БАЗ ДАННЫХ')
panel('MySQL подключения', mysql_status, 'table', width=12)
panel('Node Exporter', 'up{job="node",server=~"db[123]"}', 'table', width=12)
panel('MySQL Exporter', 'up{job="mysql",server=~"db[123]"}', 'table', width=12)
panel('История доступности MySQL', mysql_status, width=12, colors=GOOD_ONE, limits=(0, 1))
section('03  /  API ORCHESTRATOR')
panel('Доступность API', 'orchestrator_api_up', 'table', width=12, legend='{{endpoint}}')
panel('Ошибки endpoint', 'orchestrator_endpoint_success == 0', 'table', width=12, legend='{{endpoint}}')
save('monitoring-health')
