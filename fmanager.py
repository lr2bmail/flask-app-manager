#!/usr/bin/env python3
import argparse
import base64
import html
import os
import subprocess
import sys
import tarfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import yaml

CONFIG_PATH = Path('/etc/fmanager/apps.yml')
SYSTEMD_PATH = Path('/etc/systemd/system')
NGINX_AVAILABLE = Path('/etc/nginx/sites-available')
NGINX_ENABLED = Path('/etc/nginx/sites-enabled')
BACKUP_DIR = Path('/var/backups/fmanager')
CLOUDFLARE_NGINX_SNIPPET = Path('/etc/nginx/snippets/cloudflare-real-ip.conf')


def run_cmd(cmd, cwd=None, check=False):
    try:
        result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 127, '', f'Command not found: {cmd[0]}'


def require_root_for_write():
    if os.geteuid() != 0:
        print('This command needs sudo/root.')
        sys.exit(1)


def load_config():
    if not CONFIG_PATH.exists():
        return {'apps': {}}
    with CONFIG_PATH.open('r') as f:
        data = yaml.safe_load(f) or {}
    data.setdefault('apps', {})
    return data


def save_config(data):
    require_root_for_write()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open('w') as f:
        yaml.safe_dump(data, f, sort_keys=False)


def get_app(name):
    app = load_config().get('apps', {}).get(name)
    if not app:
        print(f'App not found: {name}')
        sys.exit(1)
    return app


def systemctl_status(service):
    _, out, _ = run_cmd(['systemctl', 'is-active', service])
    return out or 'unknown'


def service_enabled(service):
    _, out, _ = run_cmd(['systemctl', 'is-enabled', service])
    return out or 'unknown'


def port_open(port):
    try:
        port = int(port)
    except Exception:
        return False
    for conn in psutil.net_connections(kind='inet'):
        if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
            return True
    return False


def get_port_process(port):
    try:
        port = int(port)
    except Exception:
        return None
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info']):
        try:
            for conn in proc.net_connections(kind='inet'):
                if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    return proc.info['pid'], proc.info['name'], cmdline, proc
        except Exception:
            continue
    return None


def next_free_port(start=8001):
    port = start
    used = {int(a.get('port')) for a in load_config().get('apps', {}).values() if str(a.get('port', '')).isdigit()}
    while port in used or port_open(port):
        port += 1
    return port


def print_table(rows, headers):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(str(value)))
    line = '  '.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print('-' * len(line))
    for row in rows:
        print('  '.join(str(value).ljust(widths[i]) for i, value in enumerate(row)))


def app_rows(include_usage=False):
    rows = []
    for name, app in load_config().get('apps', {}).items():
        service = app.get('service', name)
        port = app.get('port', '-')
        proc_data = get_port_process(port)
        base = [name, systemctl_status(service), service_enabled(service), port, 'open' if port_open(port) else 'closed', app.get('domain', '-')]
        if include_usage:
            cpu = '-'
            ram = '-'
            pid = '-'
            if proc_data:
                pid, _, _, proc = proc_data
                try:
                    cpu = f'{proc.cpu_percent(interval=0.1):.1f}%'
                    ram = f'{proc.memory_info().rss / 1024 / 1024:.1f}MB'
                except Exception:
                    pass
            base += [pid, cpu, ram]
        rows.append(base)
    return rows


def cmd_list(args):
    rows = app_rows(False)
    if not rows:
        print('No apps configured.')
        return
    print_table(rows, ['APP', 'STATUS', 'ENABLED', 'PORT', 'PORT STATUS', 'DOMAIN'])


def cmd_status(args):
    names = [args.name] if args.name else list(load_config().get('apps', {}).keys())
    rows = []
    for name in names:
        app = get_app(name)
        service = app.get('service', name)
        port = app.get('port', '-')
        proc = get_port_process(port)
        rows.append([name, systemctl_status(service), service_enabled(service), port, 'open' if port_open(port) else 'closed', proc[0] if proc else '-', proc[1] if proc else '-'])
    print_table(rows, ['APP', 'STATUS', 'ENABLED', 'PORT', 'PORT STATUS', 'PID', 'PROCESS'])


def cmd_top(args):
    rows = app_rows(True)
    if not rows:
        print('No apps configured.')
        return
    print_table(rows, ['APP', 'STATUS', 'ENABLED', 'PORT', 'PORT STATUS', 'DOMAIN', 'PID', 'CPU', 'RAM'])


def cmd_service(args, action):
    require_root_for_write()
    app = get_app(args.name)
    service = app.get('service', args.name)
    code, out, err = run_cmd(['systemctl', action, service])
    print(out or err or f'{action.title()}ed {args.name}')


def cmd_logs(args):
    app = get_app(args.name)
    service = app.get('service', args.name)
    cmd = ['journalctl', '-u', service, '-n', str(args.lines), '--no-pager']
    if args.follow:
        cmd = ['journalctl', '-u', service, '-f']
    subprocess.run(cmd)


def cmd_ports(args):
    rows = []
    for name, app in load_config().get('apps', {}).items():
        port = app.get('port', '-')
        proc = get_port_process(port)
        rows.append([name, port, 'open' if port_open(port) else 'closed', proc[0] if proc else '-', proc[1] if proc else '-'])
    print_table(rows, ['APP', 'PORT', 'STATUS', 'PID', 'PROCESS'])


def cmd_info(args):
    for key, value in get_app(args.name).items():
        print(f'{key}: {value}')


def add_app_config(name, path, domain, port, module='app:app', service=None, user='www-data', workers=3, venv=None, env_file=None, description=''):
    data = load_config()
    if name in data.get('apps', {}):
        print(f'App already exists: {name}')
        sys.exit(1)
    data['apps'][name] = {'path': path, 'domain': domain, 'port': port, 'module': module, 'service': service or name, 'user': user, 'workers': workers, 'venv': venv or f'{path}/venv', 'env_file': env_file or f'{path}/.env', 'description': description or ''}
    save_config(data)


def cmd_add(args):
    add_app_config(args.name, args.path, args.domain, args.port, args.module, args.service, args.user, args.workers, args.venv, args.env_file, args.description)
    print(f'Added app: {args.name}')


def cmd_create(args):
    require_root_for_write()
    path = Path(args.path or f'/apps/{args.name}')
    port = args.port or next_free_port()
    if path.exists() and any(path.iterdir()) and not args.force:
        print(f'Path exists and is not empty: {path}. Use --force to continue.')
        sys.exit(1)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'app.py').write_text("from flask import Flask\n\napp = Flask(__name__)\n\n@app.route('/')\ndef home():\n    return 'Hello from %s'\n\n@app.route('/health')\ndef health():\n    return {'status': 'ok'}\n" % args.name)
    (path / 'requirements.txt').write_text('Flask==3.0.3\ngunicorn==22.0.0\n')
    (path / '.env').write_text('FLASK_ENV=production\nSECRET_KEY=change-me\n')
    run_cmd(['python3', '-m', 'venv', str(path / 'venv')])
    run_cmd([str(path / 'venv/bin/pip'), 'install', '-r', str(path / 'requirements.txt')])
    add_app_config(args.name, str(path), args.domain, port, 'app:app', args.service or args.name, args.user, args.workers, str(path / 'venv'), str(path / '.env'), args.description or f'{args.name} Flask app')
    print(f'Created Flask app: {path}')
    print(f'Added config: {args.name} on port {port}')


def cmd_remove(args):
    data = load_config()
    if args.name not in data.get('apps', {}):
        print(f'App not found: {args.name}')
        sys.exit(1)
    del data['apps'][args.name]
    save_config(data)
    print(f'Removed app from config: {args.name}')


def build_systemd_service(name, app):
    path = app['path']
    user = app.get('user', 'www-data')
    workers = app.get('workers', 3)
    port = app['port']
    module = app.get('module', 'app:app')
    venv = app.get('venv', f'{path}/venv')
    env_file = app.get('env_file', f'{path}/.env')
    env_line = f'EnvironmentFile={env_file}' if env_file else ''
    description = app.get('description') or f'Gunicorn service for {name}'
    return f'''[Unit]\nDescription={description}\nAfter=network.target\n\n[Service]\nUser={user}\nGroup=www-data\nWorkingDirectory={path}\n{env_line}\nExecStart={venv}/bin/gunicorn --workers {workers} --bind 127.0.0.1:{port} {module}\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n'''


def cmd_gen_systemd(args):
    require_root_for_write()
    app = get_app(args.name)
    service = app.get('service', args.name)
    target = SYSTEMD_PATH / f'{service}.service'
    if target.exists() and not args.force:
        print(f'File exists: {target}. Use --force to overwrite.')
        sys.exit(1)
    target.write_text(build_systemd_service(args.name, app))
    print(f'Created: {target}')
    print('Run: sudo systemctl daemon-reload && sudo systemctl enable --now ' + service)


def build_nginx_config(name, app):
    cf_include = '    include /etc/nginx/snippets/cloudflare-real-ip.conf;\n' if app.get('cloudflare') else ''
    return f'''server {{\n    listen 80;\n    server_name {app['domain']};\n{cf_include}\n    client_max_body_size 50M;\n\n    location / {{\n        proxy_pass http://127.0.0.1:{app['port']};\n        proxy_http_version 1.1;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_connect_timeout 60s;\n        proxy_send_timeout 60s;\n        proxy_read_timeout 60s;\n    }}\n\n    location /static/ {{\n        alias {app['path']}/static/;\n        expires 7d;\n    }}\n}}\n'''


def cmd_gen_nginx(args):
    require_root_for_write()
    app = get_app(args.name)
    target = NGINX_AVAILABLE / args.name
    link = NGINX_ENABLED / args.name
    if target.exists() and not args.force:
        print(f'File exists: {target}. Use --force to overwrite.')
        sys.exit(1)
    target.write_text(build_nginx_config(args.name, app))
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    print(f'Created: {target}')
    print(f'Enabled: {link}')
    print('Run: sudo nginx -t && sudo systemctl reload nginx')


def cmd_ssl(args):
    require_root_for_write()
    app = get_app(args.name)
    domain = app.get('domain')
    run_cmd(['apt', 'install', 'certbot', 'python3-certbot-nginx', '-y'])
    cmd = ['certbot', '--nginx', '-d', domain]
    cmd += ['--email', args.email, '--agree-tos', '--no-eff-email'] if args.email else ['--register-unsafely-without-email']
    if args.redirect:
        cmd += ['--redirect']
    subprocess.run(cmd)


def cmd_cloudflare(args):
    data = load_config()
    if args.action == 'enable':
        require_root_for_write()
        app = data.get('apps', {}).get(args.name)
        if not app:
            print(f'App not found: {args.name}')
            sys.exit(1)
        app['cloudflare'] = True
        save_config(data)
        print(f'Cloudflare mode enabled for {args.name}. Regenerate nginx: sudo fmanager gen-nginx {args.name} --force')
    elif args.action == 'snippet':
        require_root_for_write()
        CLOUDFLARE_NGINX_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
        CLOUDFLARE_NGINX_SNIPPET.write_text(CLOUDFLARE_SNIPPET)
        print(f'Created: {CLOUDFLARE_NGINX_SNIPPET}')
    elif args.action == 'info':
        print('Cloudflare: DNS A record -> server IP, SSL mode Full/Full strict, then run cloudflare snippet + enable APP.')


def cmd_check(args):
    app = get_app(args.name)
    path = Path(app['path'])
    venv = Path(app.get('venv', path / 'venv'))
    env_file = Path(app.get('env_file', path / '.env'))
    port = app.get('port')
    checks = [['app path', path.exists(), str(path)], ['venv', venv.exists(), str(venv)], ['gunicorn', (venv / 'bin/gunicorn').exists(), str(venv / 'bin/gunicorn')], ['.env', env_file.exists(), str(env_file)], ['port open', port_open(port), str(port)]]
    print_table([[n, 'OK' if ok else 'NO', d] for n, ok, d in checks], ['CHECK', 'STATUS', 'DETAIL'])


def doctor_results(fix=False):
    rows = []
    for name, app in load_config().get('apps', {}).items():
        service = app.get('service', name)
        status = systemctl_status(service)
        enabled = service_enabled(service)
        path_ok = Path(app.get('path', '')).exists()
        port_ok = port_open(app.get('port'))
        nginx_ok = (NGINX_ENABLED / name).exists()
        action = '-'
        if fix and status != 'active':
            run_cmd(['systemctl', 'restart', service])
            action = 'restarted'
            status = systemctl_status(service)
            port_ok = port_open(app.get('port'))
        health = 'OK' if status == 'active' and port_ok and path_ok else 'BAD'
        rows.append([name, health, status, enabled, 'yes' if path_ok else 'no', 'yes' if port_ok else 'no', 'yes' if nginx_ok else 'no', action])
    return rows


def cmd_doctor(args):
    if args.fix:
        require_root_for_write()
    rows = doctor_results(args.fix)
    if not rows:
        print('No apps configured.')
        return
    print_table(rows, ['APP', 'HEALTH', 'SERVICE', 'ENABLED', 'PATH', 'PORT', 'NGINX', 'ACTION'])


def cmd_autorestart(args):
    require_root_for_write()
    service_path = SYSTEMD_PATH / 'fmanager-doctor.service'
    timer_path = SYSTEMD_PATH / 'fmanager-doctor.timer'
    service_path.write_text('[Unit]\nDescription=Flask App Manager doctor auto restart\n\n[Service]\nType=oneshot\nExecStart=/usr/local/bin/fmanager doctor --fix\n')
    timer_path.write_text(f'[Unit]\nDescription=Run fmanager doctor every {args.minutes} minute(s)\n\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec={args.minutes}min\nUnit=fmanager-doctor.service\n\n[Install]\nWantedBy=timers.target\n')
    run_cmd(['systemctl', 'daemon-reload'])
    run_cmd(['systemctl', 'enable', '--now', 'fmanager-doctor.timer'])
    print('Auto restart enabled: fmanager-doctor.timer')
    print('Check with: systemctl status fmanager-doctor.timer')


def cmd_deploy(args):
    require_root_for_write()
    app = get_app(args.name)
    path = Path(app['path'])
    service = app.get('service', args.name)
    venv = Path(app.get('venv', path / 'venv'))
    req = path / 'requirements.txt'
    if (path / '.git').exists():
        print(run_cmd(['git', 'pull'], cwd=str(path))[1])
    if req.exists() and (venv / 'bin/pip').exists():
        print(run_cmd([str(venv / 'bin/pip'), 'install', '-r', str(req)], cwd=str(path))[1])
    if not args.no_restart:
        print(run_cmd(['systemctl', 'restart', service])[1] or f'Restarted {service}')


def cmd_backup(args):
    require_root_for_write()
    app = get_app(args.name)
    path = Path(app['path'])
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{args.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.gz"
    exclude = {'venv', '__pycache__', '.git', 'node_modules'}
    with tarfile.open(target, 'w:gz') as tar:
        for item in path.rglob('*'):
            if any(part in exclude for part in item.relative_to(path).parts):
                continue
            tar.add(item, arcname=f'{args.name}/{item.relative_to(path)}')
    print(f'Backup created: {target}')


def render_dashboard():
    trs = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(x))}</td>' for x in r) + '</tr>' for r in app_rows(True))
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="10"><title>Flask App Manager</title><style>body{{font-family:Arial;margin:24px;background:#f6f7fb}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}.card{{background:white;padding:18px;border-radius:12px;box-shadow:0 2px 10px #0001}}code{{background:#eee;padding:2px 5px;border-radius:4px}}</style></head><body><div class="card"><h1>Flask App Manager</h1><table><thead><tr><th>APP</th><th>STATUS</th><th>ENABLED</th><th>PORT</th><th>PORT STATUS</th><th>DOMAIN</th><th>PID</th><th>CPU</th><th>RAM</th></tr></thead><tbody>{trs}</tbody></table></div></body></html>'


def cmd_web(args):
    user = args.user or os.environ.get('FMANAGER_USER', 'admin')
    password = args.password or os.environ.get('FMANAGER_PASS')
    if not password:
        print('Set password with --password or FMANAGER_PASS env var.')
        sys.exit(1)
    token = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Authorization') != token:
                self.send_response(401); self.send_header('WWW-Authenticate', 'Basic realm="fmanager"'); self.end_headers(); self.wfile.write(b'Auth required'); return
            body = render_dashboard().encode(); self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    print(f'Web dashboard: http://{args.host}:{args.port}  user={user}')
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


CLOUDFLARE_SNIPPET = '''real_ip_header CF-Connecting-IP;\nset_real_ip_from 173.245.48.0/20;\nset_real_ip_from 103.21.244.0/22;\nset_real_ip_from 103.22.200.0/22;\nset_real_ip_from 103.31.4.0/22;\nset_real_ip_from 141.101.64.0/18;\nset_real_ip_from 108.162.192.0/18;\nset_real_ip_from 190.93.240.0/20;\nset_real_ip_from 188.114.96.0/20;\nset_real_ip_from 197.234.240.0/22;\nset_real_ip_from 198.41.128.0/17;\nset_real_ip_from 162.158.0.0/15;\nset_real_ip_from 104.16.0.0/13;\nset_real_ip_from 104.24.0.0/14;\nset_real_ip_from 172.64.0.0/13;\nset_real_ip_from 131.0.72.0/22;\nset_real_ip_from 2400:cb00::/32;\nset_real_ip_from 2606:4700::/32;\nset_real_ip_from 2803:f800::/32;\nset_real_ip_from 2405:b500::/32;\nset_real_ip_from 2405:8100::/32;\nset_real_ip_from 2a06:98c0::/29;\nset_real_ip_from 2c0f:f248::/32;\n'''


def main():
    parser = argparse.ArgumentParser(prog='fmanager', description='Manage multiple Flask apps on one Ubuntu server')
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('list'); p.set_defaults(func=cmd_list)
    p = sub.add_parser('status'); p.add_argument('name', nargs='?'); p.set_defaults(func=cmd_status)
    p = sub.add_parser('top'); p.set_defaults(func=cmd_top)
    p = sub.add_parser('start'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'start'))
    p = sub.add_parser('stop'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'stop'))
    p = sub.add_parser('restart'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'restart'))
    p = sub.add_parser('logs'); p.add_argument('name'); p.add_argument('--lines', type=int, default=50); p.add_argument('-f', '--follow', action='store_true'); p.set_defaults(func=cmd_logs)
    p = sub.add_parser('ports'); p.set_defaults(func=cmd_ports)
    p = sub.add_parser('info'); p.add_argument('name'); p.set_defaults(func=cmd_info)
    p = sub.add_parser('add'); p.add_argument('name'); p.add_argument('--path', required=True); p.add_argument('--domain', required=True); p.add_argument('--port', required=True, type=int); p.add_argument('--module', default='app:app'); p.add_argument('--service'); p.add_argument('--user', default='www-data'); p.add_argument('--workers', type=int, default=3); p.add_argument('--venv'); p.add_argument('--env-file'); p.add_argument('--description'); p.set_defaults(func=cmd_add)
    p = sub.add_parser('create'); p.add_argument('name'); p.add_argument('--domain', required=True); p.add_argument('--path'); p.add_argument('--port', type=int); p.add_argument('--service'); p.add_argument('--user', default='www-data'); p.add_argument('--workers', type=int, default=3); p.add_argument('--description'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_create)
    p = sub.add_parser('remove'); p.add_argument('name'); p.set_defaults(func=cmd_remove)
    p = sub.add_parser('gen-systemd'); p.add_argument('name'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_gen_systemd)
    p = sub.add_parser('gen-nginx'); p.add_argument('name'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_gen_nginx)
    p = sub.add_parser('ssl'); p.add_argument('name'); p.add_argument('--email'); p.add_argument('--redirect', action='store_true'); p.set_defaults(func=cmd_ssl)
    p = sub.add_parser('cloudflare'); p.add_argument('action', choices=['info', 'snippet', 'enable']); p.add_argument('name', nargs='?'); p.set_defaults(func=cmd_cloudflare)
    p = sub.add_parser('check'); p.add_argument('name'); p.set_defaults(func=cmd_check)
    p = sub.add_parser('doctor'); p.add_argument('--fix', action='store_true'); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser('autorestart'); p.add_argument('--minutes', type=int, default=1); p.set_defaults(func=cmd_autorestart)
    p = sub.add_parser('deploy'); p.add_argument('name'); p.add_argument('--no-restart', action='store_true'); p.set_defaults(func=cmd_deploy)
    p = sub.add_parser('backup'); p.add_argument('name'); p.set_defaults(func=cmd_backup)
    p = sub.add_parser('web'); p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=5050); p.add_argument('--user', default='admin'); p.add_argument('--password'); p.set_defaults(func=cmd_web)
    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(0)
    args.func(args)


if __name__ == '__main__':
    main()
