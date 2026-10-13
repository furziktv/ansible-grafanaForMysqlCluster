#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export ANSIBLE_HOME="${ANSIBLE_HOME:-/tmp/ansible-monitoring-home}"
export ANSIBLE_LOCAL_TEMP="${ANSIBLE_LOCAL_TEMP:-/tmp/ansible-monitoring-local}"
python3 scripts/check_config.py
python3 scripts/test_orchestrator_exporter.py
python3 scripts/test_telegram_vless.py
python3 - <<'PY'
from pathlib import Path
for path in (Path('roles/preflight/files/discover_mysql.py'),
             Path('roles/orchestrator_exporter/files/exporter.py')):
    compile(path.read_text(), str(path), 'exec')
PY
qa_playbooks_dir="$(mktemp -d)"
trap 'rm -rf "$qa_playbooks_dir"' EXIT
for playbook in site access check update telegram notifications failover_view notification_audit telegram_dedupe telegram_diagnostics telegram_grafana_diagnostics telegram_grafana_logs telegram_template_render performance; do
  sed "s#../inventory/group_vars/vault.yml#$PWD/inventory/group_vars/vault.example.yml#g" \
    "playbooks/$playbook.yml" > "$qa_playbooks_dir/$playbook.yml"
  ansible-playbook -i inventory/hosts.yml "$qa_playbooks_dir/$playbook.yml" --syntax-check
done
if command -v ansible-lint >/dev/null 2>&1; then
  ansible-lint "$qa_playbooks_dir/site.yml" "$qa_playbooks_dir/access.yml" \
    "$qa_playbooks_dir/check.yml" "$qa_playbooks_dir/update.yml" \
    "$qa_playbooks_dir/telegram.yml" "$qa_playbooks_dir/notifications.yml" \
    "$qa_playbooks_dir/failover_view.yml" "$qa_playbooks_dir/notification_audit.yml" \
    "$qa_playbooks_dir/telegram_dedupe.yml" \
    "$qa_playbooks_dir/telegram_diagnostics.yml" \
    "$qa_playbooks_dir/telegram_grafana_diagnostics.yml" \
    "$qa_playbooks_dir/telegram_grafana_logs.yml" \
    "$qa_playbooks_dir/telegram_template_render.yml" \
    "$qa_playbooks_dir/performance.yml"
else
  echo 'ansible-lint отсутствует; установите его на контроллере и повторите qa.sh' >&2
fi
