#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path

import psutil
import yaml

CONFIG_PATH = Path('/etc/fmanager/apps.yml')
SYSTEMD_PATH = Path('/etc/systemd/system')
NGINX_AVAILABLE = Path('/etc/nginx/sites-available')
NGINX_ENABLED = Path('/etc/nginx/sites-enabled')


def run_cmd(cmd, check=False):
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=check)
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
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            for conn in proc.net_connections(kind='inet'):
                if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                    cmdline = ' '.join(proc.info.get('cmdline') or [])
                    return proc.info['pid'], proc.info['name'], cmdline
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


def cmd_list(args):
    apps = load_config().get('apps', {})
    rows = []
    for name, app in apps.items():
        service = app.get('service', name)
        port = app.get('port', '-')
        rows.append([name, systemctl_status(service), service_enabled(service), port, 'open' if port_open(port) else 'closed', app.get('domain', '-')])
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


def main():
    parser = argparse.ArgumentParser(prog='fmanager', description='Manage multiple Flask apps on one Ubuntu server')
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('list', help='List configured apps'); p.set_defaults(func=cmd_list)
    p = sub.add_parser('status', help='Show app status'); p.add_argument('name', nargs='?'); p.set_defaults(func=cmd_status)
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == '__main__':
    main()
