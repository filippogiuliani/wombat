"""Widgets on the home-page links: a few live numbers under a link (who is watching on Jellyfin, containers running,
DNS queries blocked…), read by weHub from each service's API, like Homepage's widgets.

Credentials stay here: the browser only ever gets the lines to show, and only for the links the person sees. Every
widget is read at most once a minute (in parallel, with a short timeout), so the home page never waits on a service.

A line is {key | label, value | value_key + params, state}: `key` and `value_key` are words the page translates
(widget.<key>), `label` and `value` are shown as they are (numbers, or the text a kwIRC-style widget sends);
state is ok | warn | bad (a colour) or absent."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT = 6
KEEP = 60
MAX_LINES = 4
_cache = {}       # link id → (read at, config stamp, result)
_samples = {}     # link id → (time, bytes in, bytes out): OPNsense traffic is a difference between two readings
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='widgets')


class WidgetError(Exception):
    """Shown under the link: what to fix. A code the page translates (widget.err_<code>), with its details."""
    def __init__(self, code, **params):
        super().__init__(code)
        self.code, self.params = code, params


# type → the settings it has: secrets are never sent back to the browser (only whether they are set)
TYPES = {
    'kwirc':      {'secrets': ['token'], 'fields': []},
    'generic':    {'secrets': ['token'], 'fields': []},
    'jellyfin':   {'secrets': ['api_key'], 'fields': []},
    'npm':        {'secrets': ['email', 'password'], 'fields': []},
    'portainer':  {'secrets': ['token'], 'fields': []},
    'adguard':    {'secrets': ['username', 'password'], 'fields': []},
    'grafana':    {'secrets': ['token'], 'fields': []},
    'prometheus': {'secrets': [], 'fields': ['queries']},
    'opnsense':   {'secrets': ['key', 'secret'], 'fields': ['wan']},
    'openwrt':    {'secrets': ['username', 'password'], 'fields': []},
}
FORMATS = ('number', 'percent', 'ratio', 'bytes', 'ago', 'bool', 'duration')


# ---------- small helpers ----------

def _req(method, url, cfg, **kw):
    try:
        r = requests.request(method, url, timeout=TIMEOUT, verify=not cfg.get('insecure'), **kw)
    except requests.exceptions.SSLError:
        raise WidgetError('cert')
    except requests.Timeout:
        raise WidgetError('timeout')
    except requests.RequestException:
        raise WidgetError('unreachable')
    if r.status_code in (401, 403):
        raise WidgetError('refused')
    if r.status_code == 404:
        raise WidgetError('not_found')
    if r.status_code >= 400:
        raise WidgetError('http', code=r.status_code)
    try:
        return r.json()
    except ValueError:
        raise WidgetError('not_data')


def _base(cfg):
    return (cfg.get('url') or '').rstrip('/')


def _num(n):
    """12345 → '12.345' (the page shows numbers the Italian way; English readers get the same digits)."""
    return f'{int(round(n)):,}'.replace(',', '.')


def _bytes(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB', 'PB'):
        if abs(n) < 1024 or unit == 'PB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') or abs(n) >= 100 else f'{n:.1f} {unit}'
        n /= 1024


def _bits(per_second):
    bits = per_second * 8
    for unit in ('bit/s', 'kbit/s', 'Mbit/s', 'Gbit/s'):
        if bits < 1000 or unit == 'Gbit/s':
            return f'{bits:.0f} {unit}' if bits >= 100 or unit == 'bit/s' else f'{bits:.1f} {unit}'
        bits /= 1000


def _duration(seconds):
    s = int(seconds)
    if s < 3600:
        return f'{max(1, s // 60)} min'
    if s < 86400:
        return f'{s // 3600} h {(s % 3600) // 60:02d}'
    return f'{s // 86400} g {(s % 86400) // 3600} h'


def _line(key=None, value=None, state=None, label=None, value_key=None, **params):
    out = {}
    if key:
        out['key'] = key
    if label is not None:
        out['label'] = str(label)[:40]
    if value_key:
        out['value_key'] = value_key
        out['params'] = params
    elif value is not None:
        out['value'] = str(value)[:60]
    if state:
        out['state'] = state
    return out


# ---------- one reader per service ----------

def _generic(cfg, sec):
    """kwIRC and anything that speaks the same way: GET the address with the token, {items: [{label, value}]}."""
    headers = {'Authorization': f"Bearer {sec.get('token', '')}"} if sec.get('token') else {}
    data = _req('GET', _base(cfg), cfg, headers=headers)
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise WidgetError('no_items')
    return [_line(label=i.get('label', ''), value=i.get('value', ''),
                  state=i.get('state') if i.get('state') in ('ok', 'warn', 'bad') else None)
            for i in items if isinstance(i, dict)]


def _jellyfin(cfg, sec):
    h = {'X-Emby-Token': sec.get('api_key', '')}
    sessions = _req('GET', f'{_base(cfg)}/Sessions', cfg, headers=h, params={'activeWithinSeconds': 120})
    playing = [s for s in sessions if s.get('NowPlayingItem')]
    transcoding = sum(1 for s in playing if (s.get('PlayState') or {}).get('PlayMethod') == 'Transcode')
    counts = _req('GET', f'{_base(cfg)}/Items/Counts', cfg, headers=h)
    lines = [_line('playing', _num(len(playing)))]
    if transcoding:
        lines.append(_line('transcoding', _num(transcoding), 'warn'))
    lines.append(_line('library', value_key='library_value', movies=_num(counts.get('MovieCount', 0)),
                       series=_num(counts.get('SeriesCount', 0))))
    return lines


def _npm(cfg, sec):
    base = _base(cfg)
    token = _req('POST', f'{base}/api/tokens', cfg, json={'identity': sec.get('email', ''), 'secret': sec.get('password', '')})
    h = {'Authorization': f"Bearer {token.get('token', '')}"}
    hosts = _req('GET', f'{base}/api/nginx/proxy-hosts', cfg, headers=h)
    on = sum(1 for x in hosts if x.get('enabled'))
    certs = _req('GET', f'{base}/api/nginx/certificates', cfg, headers=h)
    now = datetime.now(timezone.utc)
    soon = expired = 0
    for c in certs:
        try:
            exp = datetime.fromisoformat(str(c.get('expires_on')).replace(' ', 'T').replace('Z', '+00:00'))
        except ValueError:
            continue
        exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        days = (exp - now).total_seconds() / 86400
        expired += days < 0
        soon += 0 <= days < 14
    lines = [_line('proxy_hosts', value_key='hosts_value', on=on, off=len(hosts) - on)]
    if expired:
        lines.append(_line('certificates', value_key='certs_expired', state='bad', n=expired))
    elif soon:
        lines.append(_line('certificates', value_key='certs_soon', state='warn', n=soon))
    else:
        lines.append(_line('certificates', value_key='certs_ok', state='ok', n=len(certs)))
    return lines


def _portainer(cfg, sec):
    base, h = _base(cfg), {'X-API-Key': sec.get('token', '')}
    running = stopped = unhealthy = 0
    for env in _req('GET', f'{base}/api/endpoints', cfg, headers=h):
        for c in _req('GET', f"{base}/api/endpoints/{env['Id']}/docker/containers/json", cfg, headers=h, params={'all': 1}):
            if c.get('State') == 'running':
                running += 1
                unhealthy += '(unhealthy)' in (c.get('Status') or '')
            else:
                stopped += 1
    lines = [_line('containers', value_key='containers_value', running=running, stopped=stopped)]
    lines.append(_line('unhealthy', _num(unhealthy), 'bad' if unhealthy else 'ok'))
    return lines


def _adguard(cfg, sec):
    auth = (sec.get('username', ''), sec.get('password', ''))
    stats = _req('GET', f'{_base(cfg)}/control/stats', cfg, auth=auth)
    status = _req('GET', f'{_base(cfg)}/control/status', cfg, auth=auth)
    queries, blocked = stats.get('num_dns_queries', 0), stats.get('num_blocked_filtering', 0)
    on = bool(status.get('protection_enabled'))
    return [_line('dns_queries', _num(queries)),
            _line('dns_blocked', f'{(100 * blocked / queries) if queries else 0:.1f}%'),
            _line('protection', value_key='on' if on else 'off', state='ok' if on else 'bad')]


def _grafana(cfg, sec):
    h = {'Authorization': f"Bearer {sec.get('token', '')}"}
    health = _req('GET', f'{_base(cfg)}/api/health', cfg, headers=h)
    alerts = _req('GET', f'{_base(cfg)}/api/alertmanager/grafana/api/v2/alerts', cfg, headers=h,
                  params={'active': 'true', 'silenced': 'false', 'inhibited': 'false'})
    firing = len(alerts) if isinstance(alerts, list) else 0
    db_ok = health.get('database') == 'ok'
    return [_line('alerts', _num(firing), 'bad' if firing else 'ok'),
            _line('health', value_key='ok' if db_ok else 'problem', state='ok' if db_ok else 'bad')]


def _format(value, fmt):
    if fmt == 'percent':
        return f'{value:.1f}%', None
    if fmt == 'ratio':
        return f'{100 * value:.1f}%', None
    if fmt == 'bytes':
        return _bytes(value), None
    if fmt == 'ago':
        return None, ('ago', _duration(max(0, time.time() - value)))
    if fmt == 'duration':
        return _duration(value), None
    if fmt == 'bool':
        return None, ('ok' if value >= 1 else 'problem')
    return (_num(value) if abs(value) >= 100 or value == int(value) else f'{value:.2f}'), None


def _prometheus(cfg, sec):
    lines = []
    for q in (cfg.get('queries') or [])[:MAX_LINES]:
        data = _req('GET', f'{_base(cfg)}/api/v1/query', cfg, params={'query': q['query']})
        result = (data.get('data') or {}).get('result') or []
        if not result:
            lines.append(_line(label=q['label'], value='—'))
            continue
        try:
            value = float(result[0]['value'][1])
        except (KeyError, IndexError, TypeError, ValueError):
            lines.append(_line(label=q['label'], value='?'))
            continue
        shown, special = _format(value, q.get('format', 'number'))
        if special == 'ok':
            lines.append(_line(label=q['label'], value_key='ok', state='ok'))
        elif special == 'problem':
            lines.append(_line(label=q['label'], value_key='problem', state='bad'))
        elif special:
            lines.append(_line(label=q['label'], value_key='ago', time=special[1]))
        else:
            lines.append(_line(label=q['label'], value=shown))
    return lines


def _opnsense(cfg, sec, link_id):
    base, auth = _base(cfg), (sec.get('key', ''), sec.get('secret', ''))
    lines = []
    gws = (_req('GET', f'{base}/api/routes/gateway/status', cfg, auth=auth) or {}).get('items') or []
    if gws:
        gw = gws[0]
        up = gw.get('status') in ('none', 'online')
        lines.append(_line('internet', gw.get('delay') or '', 'ok' if up else 'bad') if up
                     else _line('internet', value_key='down', state='bad'))
    try:
        traffic = _req('GET', f'{base}/api/diagnostics/traffic/interface', cfg, auth=auth)
        wan = (traffic.get('interfaces') or {}).get(cfg.get('wan') or 'wan') or {}
        rx, tx = float(wan.get('bytes received', 0)), float(wan.get('bytes transmitted', 0))
        now = time.monotonic()
        prev = _samples.get(link_id)
        _samples[link_id] = (now, rx, tx)
        if prev and now > prev[0]:
            dt = now - prev[0]
            lines.append(_line('traffic', f'↓ {_bits(max(0, rx - prev[1]) / dt)}  ↑ {_bits(max(0, tx - prev[2]) / dt)}'))
    except WidgetError:
        pass       # older versions: no traffic, the rest still shows
    try:
        res = _req('GET', f'{base}/api/diagnostics/system/system_resources', cfg, auth=auth)
        mem = res.get('memory') or {}
        total, used = float(mem.get('total', 0)), float(mem.get('used', 0))
        if total:
            pct = 100 * used / total
            lines.append(_line('memory', f'{pct:.0f}%', 'warn' if pct > 90 else None))
    except WidgetError:
        pass
    if not lines:
        raise WidgetError('opnsense_rights')
    return lines


def _openwrt(cfg, sec):
    url = _base(cfg) + ('' if _base(cfg).endswith('/ubus') else '/ubus')
    n = [0]

    def call(session, obj, method, args=None):
        n[0] += 1
        data = _req('POST', url, cfg, json={'jsonrpc': '2.0', 'id': n[0], 'method': 'call',
                                             'params': [session, obj, method, args or {}]})
        result = data.get('result') if isinstance(data, dict) else None
        if not isinstance(result, list) or result[0] != 0:
            code = result[0] if isinstance(result, list) else '?'
            raise WidgetError('rpcd') if code == 6 else WidgetError('ubus', code=code)
        return result[1] if len(result) > 1 else {}

    session = call('0' * 32, 'session', 'login', {'username': sec.get('username', ''), 'password': sec.get('password', '')})
    sid = session.get('ubus_rpc_session')
    lines = []
    try:
        clients = 0
        for dev in call(sid, 'iwinfo', 'devices').get('devices', []):
            clients += len(call(sid, 'iwinfo', 'assoclist', {'device': dev}).get('results', []))
        lines.append(_line('wifi_clients', _num(clients)))
    except WidgetError:
        pass       # no iwinfo rights: uptime and load still show
    info = call(sid, 'system', 'info')
    lines.append(_line('uptime', _duration(info.get('uptime', 0))))
    load = (info.get('load') or [0])[0] / 65536
    lines.append(_line('load', f'{load:.2f}', 'warn' if load > 2 else None))
    return lines


def _read(link_id, cfg, sec):
    kind = cfg.get('type')
    if kind in ('kwirc', 'generic'):
        lines = _generic(cfg, sec)
    elif kind == 'opnsense':
        lines = _opnsense(cfg, sec, link_id)
    else:
        lines = {'jellyfin': _jellyfin, 'npm': _npm, 'portainer': _portainer, 'adguard': _adguard, 'grafana': _grafana,
                 'prometheus': _prometheus, 'openwrt': _openwrt}[kind](cfg, sec)
    return {'items': lines[:MAX_LINES]}


def _safe_read(args):
    link_id, cfg, sec = args
    try:
        return _read(link_id, cfg, sec)
    except WidgetError as e:
        return {'error': e.code, 'params': e.params}
    except Exception:  # an answer shaped unlike expected: say so, never break the home page
        return {'error': 'unexpected', 'params': {}}


def read_many(links):
    """{link id: {items} | {error}} for these links [(id, config, secrets, stamp)], from the cache when fresh."""
    now = time.monotonic()
    out, todo = {}, []
    with _lock:
        for link_id, cfg, sec, stamp in links:
            hit = _cache.get(link_id)
            if hit and hit[1] == stamp and now - hit[0] < KEEP:
                out[link_id] = hit[2]
            else:
                todo.append((link_id, cfg, sec, stamp))
    results = _pool.map(_safe_read, [(i, c, s) for i, c, s, _ in todo])
    for (link_id, _, _, stamp), res in zip(todo, results):
        out[link_id] = res
        with _lock:
            _cache[link_id] = (time.monotonic(), stamp, res)
    return out


def clear():
    with _lock:
        _cache.clear()
        _samples.clear()
