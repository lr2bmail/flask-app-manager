#!/usr/bin/env python3
import argparse
import base64
import html
import os
import shutil
import subprocess
import sys
import tarfile
import time
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


def cmd_add(args):
    data = load_config()
    if args.name in data.get('apps', {}):
        print(f'App already exists: {args.name}')
        sys.exit(1)
    data['apps'][args.name] = {
        'path': args.path,
        'domain': args.domain,
        'port': args.port,
        'module': args.module,
        'service': args.service or args.name,
        'user': args.user,
        'workers': args.workers,
        'venv': args.venv or f'{args.path}/venv',
        'env_file': args.env_file or f'{args.path}/.env',
        'description': args.description or '',
    }
    save_config(data)
    print(f'Added app: {args.name}')


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
    return f'''server {{\n    listen 80;\n    server_name {app['domain']};\n\n    client_max_body_size 50M;\n\n    location / {{\n        proxy_pass http://127.0.0.1:{app['port']};\n        proxy_http_version 1.1;\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_connect_timeout 60s;\n        proxy_send_timeout 60s;\n        proxy_read_timeout 60s;\n    }}\n\n    location /static/ {{\n        alias {app['path']}/static/;\n        expires 7d;\n    }}\n}}\n'''


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


def cmd_check(args):
    app = get_app(args.name)
    path = Path(app['path'])
    venv = Path(app.get('venv', path / 'venv'))
    env_file = Path(app.get('env_file', path / '.env'))
    port = app.get('port')
    checks = [
        ['app path', path.exists(), str(path)],
        ['venv', venv.exists(), str(venv)],
        ['gunicorn', (venv / 'bin/gunicorn').exists(), str(venv / 'bin/gunicorn')],
        ['.env', env_file.exists(), str(env_file)],
        ['port open', port_open(port), str(port)],
    ]
    print_table([[n, 'OK' if ok else 'NO', d] for n, ok, d in checks], ['CHECK', 'STATUS', 'DETAIL'])


def cmd_deploy(args):
    require_root_for_write()
    app = get_app(args.name)
    path = Path(app['path'])
    service = app.get('service', args.name)
    venv = Path(app.get('venv', path / 'venv'))
    req = path / 'requirements.txt'

    if not path.exists():
        print(f'App path not found: {path}')
        sys.exit(1)

    if (path / '.git').exists():
        print('Pulling latest code...')
        print(run_cmd(['git', 'pull'], cwd=str(path))[1])
    else:
        print('No .git folder found, skipping git pull.')

    if req.exists() and (venv / 'bin/pip').exists():
        print('Installing requirements...')
        code, out, err = run_cmd([str(venv / 'bin/pip'), 'install', '-r', str(req)], cwd=str(path))
        print(out or err)
    else:
        print('No requirements.txt or venv pip found, skipping pip install.')

    if not args.no_restart:
        print('Restarting service...')
        code, out, err = run_cmd(['systemctl', 'restart', service])
        print(out or err or f'Restarted {service}')


def cmd_backup(args):
    require_root_for_write()
    app = get_app(args.name)
    path = Path(app['path'])
    if not path.exists():
        print(f'App path not found: {path}')
        sys.exit(1)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    target = BACKUP_DIR / f'{args.name}_{stamp}.tar.gz'
    exclude = {'venv', '__pycache__', '.git', 'node_modules'}
    with tarfile.open(target, 'w:gz') as tar:
        for item in path.rglob('*'):
            if any(part in exclude for part in item.relative_to(path).parts):
                continue
            tar.add(item, arcname=f'{args.name}/{item.relative_to(path)}')
    print(f'Backup created: {target}')


def render_dashboard():
    rows = app_rows(True)
    trs = ''
    for r in rows:
        trs += '<tr>' + ''.join(f'<td>{html.escape(str(x))}</td>' for x in r) + '</tr>'
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="10"><title>Flask App Manager</title><style>body{{font-family:Arial;margin:24px;background:#f6f7fb}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}.card{{background:white;padding:18px;border-radius:12px;box-shadow:0 2px 10px #0001}}code{{background:#eee;padding:2px 5px;border-radius:4px}}</style></head><body><div class="card"><h1>Flask App Manager</h1><p>Auto refresh every 10 seconds. Actions are CLI-only for safety.</p><table><thead><tr><th>APP</th><th>STATUS</th><th>ENABLED</th><th>PORT</th><th>PORT STATUS</th><th>DOMAIN</th><th>PID</th><th>CPU</th><th>RAM</th></tr></thead><tbody>{trs}</tbody></table><p>Use <code>fmanager restart appname</code>, <code>fmanager logs appname</code>, or <code>fmanager deploy appname</code> from SSH.</p></div></body></html>'''


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
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="fmanager"')
                self.end_headers()
                self.wfile.write(b'Auth required')
                return
            body = render_dashboard().encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Web dashboard: http://{args.host}:{args.port}  user={user}')
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(prog='fmanager', description='Manage multiple Flask apps on one Ubuntu server')
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('list', help='List configured apps'); p.set_defaults(func=cmd_list)
    p = sub.add_parser('status', help='Show app status'); p.add_argument('name', nargs='?'); p.set_defaults(func=cmd_status)
    p = sub.add_parser('top', help='Show CPU/RAM per configured app'); p.set_defaults(func=cmd_top)
    p = sub.add_parser('start', help='Start app'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'start'))
    p = sub.add_parser('stop', help='Stop app'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'stop'))
    p = sub.add_parser('restart', help='Restart app'); p.add_argument('name'); p.set_defaults(func=lambda a: cmd_service(a, 'restart'))
    p = sub.add_parser('logs', help='Show logs'); p.add_argument('name'); p.add_argument('--lines', type=int, default=50); p.add_argument('-f', '--follow', action='store_true'); p.set_defaults(func=cmd_logs)
    p = sub.add_parser('ports', help='Show configured app ports'); p.set_defaults(func=cmd_ports)
    p = sub.add_parser('info', help='Show app config'); p.add_argument('name'); p.set_defaults(func=cmd_info)
    p = sub.add_parser('add', help='Add app to config'); p.add_argument('name'); p.add_argument('--path', required=True); p.add_argument('--domain', required=True); p.add_argument('--port', required=True, type=int); p.add_argument('--module', default='app:app'); p.add_argument('--service'); p.add_argument('--user', default='www-data'); p.add_argument('--workers', type=int, default=3); p.add_argument('--venv'); p.add_argument('--env-file'); p.add_argument('--description'); p.set_defaults(func=cmd_add)
    p = sub.add_parser('remove', help='Remove app from config only'); p.add_argument('name'); p.set_defaults(func=cmd_remove)
    p = sub.add_parser('gen-systemd', help='Generate systemd service'); p.add_argument('name'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_gen_systemd)
    p = sub.add_parser('gen-nginx', help='Generate Nginx config'); p.add_argument('name'); p.add_argument('--force', action='store_true'); p.set_defaults(func=cmd_gen_nginx)
    p = sub.add_parser('check', help='Check app files and port'); p.add_argument('name'); p.set_defaults(func=cmd_check)
    p = sub.add_parser('deploy', help='git pull, pip install, restart'); p.add_argument('name'); p.add_argument('--no-restart', action='store_true'); p.set_defaults(func=cmd_deploy)
    p = sub.add_parser('backup', help='Create tar.gz backup of app files'); p.add_argument('name'); p.set_defaults(func=cmd_backup)
    p = sub.add_parser('web', help='Run simple web dashboard'); p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=5050); p.add_argument('--user', default='admin'); p.add_argument('--password'); p.set_defaults(func=cmd_web)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == '__main__':
    main()
