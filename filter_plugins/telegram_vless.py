"""Build a restricted Xray HTTP proxy config from one VLESS share URI."""
import json
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

from ansible.errors import AnsibleFilterError


def make_config(uri):
    try:
        link = urlsplit(uri.strip())
        if link.scheme.lower() != 'vless' or not link.hostname or not link.port:
            raise ValueError('ожидается полная ссылка vless://UUID@host:port?...')
        user_id = str(uuid.UUID(unquote(link.username or '')))
        params = {key: values[-1] for key, values in parse_qs(link.query, keep_blank_values=True).items()}
        security = params.get('security', 'none').lower()
        transport = params.get('type', 'tcp').lower()
        if security not in ('tls', 'reality'):
            raise ValueError('поддерживаются только security=tls или security=reality')
        if transport not in ('tcp', 'xhttp', 'ws', 'grpc'):
            raise ValueError('поддерживаются только type=tcp, xhttp, ws или grpc')
        if security == 'reality' and transport == 'ws':
            raise ValueError('Xray не поддерживает Reality + WebSocket; используйте ссылку с type=tcp, xhttp или grpc')
        outbound_settings = {'address': link.hostname, 'port': link.port,
                             'id': user_id, 'encryption': 'none'}
        if params.get('flow'):
            if transport != 'tcp':
                raise ValueError('flow поддерживается только с type=tcp')
            outbound_settings['flow'] = params['flow']
        stream = {'network': transport, 'security': security}
        sni = params.get('sni') or params.get('serverName') or link.hostname
        if security == 'reality':
            public_key = params.get('pbk') or params.get('publicKey')
            if not public_key or not sni:
                raise ValueError('для Reality нужны pbk и sni')
            stream['realitySettings'] = {
                'serverName': sni, 'fingerprint': params.get('fp', 'chrome'),
                'password': public_key, 'shortId': params.get('sid', ''),
                'spiderX': params.get('spx', ''),
            }
        else:
            stream['tlsSettings'] = {'serverName': sni, 'allowInsecure': False}
            if params.get('fp'):
                stream['tlsSettings']['fingerprint'] = params['fp']
            if params.get('alpn'):
                stream['tlsSettings']['alpn'] = params['alpn'].split(',')
        if transport == 'ws':
            stream['wsSettings'] = {'path': params.get('path', '/')}
            if params.get('host'):
                stream['wsSettings']['host'] = params['host']
        elif transport == 'grpc':
            stream['grpcSettings'] = {'serviceName': params.get('serviceName', '')}
            if params.get('host'):
                stream['grpcSettings']['authority'] = params['host']
        elif transport == 'xhttp':
            xhttp = {'path': params.get('path', '/')}
            if params.get('host'):
                xhttp['host'] = params['host']
            if params.get('mode'):
                xhttp['mode'] = params['mode']
            if params.get('extra'):
                extra = json.loads(params['extra'])
                if not isinstance(extra, dict):
                    raise ValueError('XHTTP extra должен быть JSON-объектом')
                xhttp['extra'] = extra
            stream['xhttpSettings'] = xhttp
        config = {
            'log': {'loglevel': 'warning'},
            'inbounds': [{'tag': 'grafana-http', 'listen': '0.0.0.0',
                          'port': 18080, 'protocol': 'http', 'settings': {}}],
            'outbounds': [
                {'tag': 'direct', 'protocol': 'freedom'},
                {'tag': 'telegram-vless', 'protocol': 'vless',
                 'settings': outbound_settings,
                 'streamSettings': stream},
            ],
            'routing': {'domainStrategy': 'AsIs', 'rules': [
                {'type': 'field', 'domain': ['full:api.telegram.org'],
                 'outboundTag': 'telegram-vless'},
            ]},
        }
        return json.dumps(config, ensure_ascii=False, indent=2) + '\n'
    except (ValueError, TypeError) as exc:
        raise AnsibleFilterError(f'Некорректная VLESS-ссылка: {exc}') from None


class FilterModule:
    def filters(self):
        return {'telegram_vless_config': make_config}
