#!/usr/bin/env python3
"""Offline checks for the VLESS URI parser and Telegram-only routing rule."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'filter_plugins'))
from telegram_vless import make_config  # noqa: E402

base = 'vless://123e4567-e89b-12d3-a456-426614174000@example.org:443'
reality = json.loads(make_config(base + '?security=reality&type=tcp&sni=example.com&pbk=publickey&sid=aabb&flow=xtls-rprx-vision'))
assert reality['outbounds'][1]['streamSettings']['realitySettings']['password'] == 'publickey'
assert reality['outbounds'][0]['tag'] == 'direct'
assert reality['routing']['rules'] == [
    {'type': 'field', 'domain': ['full:api.telegram.org'], 'outboundTag': 'telegram-vless'}]
assert reality['inbounds'][0]['port'] == 18080
ws = json.loads(make_config(base + '?security=tls&type=ws&sni=example.com&host=cdn.example.com&path=%2Fpath'))
assert ws['outbounds'][1]['streamSettings']['wsSettings'] == {'path': '/path', 'host': 'cdn.example.com'}
xhttp = json.loads(make_config(base + '?security=reality&type=xhttp&sni=example.com&pbk=publickey&path=%2Fapi&mode=stream-one'))
assert xhttp['outbounds'][1]['streamSettings']['xhttpSettings'] == {'path': '/api', 'mode': 'stream-one'}
grpc = json.loads(make_config(base + '?security=reality&type=grpc&sni=example.com&pbk=publickey&serviceName=svc'))
assert grpc['outbounds'][1]['streamSettings']['grpcSettings'] == {'serviceName': 'svc'}
for broken in (base, base + '?security=reality&type=tcp', 'vless://bad@example.org:443?security=tls'):
    try:
        make_config(broken)
    except Exception:
        pass
    else:
        raise AssertionError('unsupported/incomplete VLESS URI was accepted')
try:
    make_config(base + '?security=reality&type=ws&pbk=publickey')
except Exception:
    pass
else:
    raise AssertionError('Reality + WebSocket was accepted')
print('OK: VLESS URI parsing and Telegram-only Xray routing')
