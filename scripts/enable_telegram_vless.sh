#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

cat <<'TEXT'
Сейчас откроется Ansible Vault. Добавьте или заполните:
  vault_telegram_bot_token: 'токен бота'
  vault_telegram_chat_id: 'chat ID'
  vault_telegram_vless_uri: 'полная ссылка vless://...'

Ссылку VLESS вставьте целиком, вместе с параметрами после ? и фрагментом после #.
Не вставляйте ключ в этот скрипт или в командную строку.
TEXT

ansible-vault edit inventory/group_vars/vault.yml

python3 - <<'PY'
from pathlib import Path

path = Path('inventory/group_vars/all.yml')
data = path.read_text()
for key in ('telegram_enabled', 'telegram_vless_enabled'):
    before = f'{key}: false'
    after = f'{key}: true'
    if data.count(before) == 1:
        data = data.replace(before, after, 1)
    elif data.count(after) != 1:
        raise SystemExit(f'Не найден единственный параметр {key} в {path}')
path.write_text(data)
PY

ansible-playbook -i inventory/hosts.yml playbooks/telegram.yml \
  --ask-vault-pass --ask-pass --ask-become-pass
