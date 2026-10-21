"""Exercise a built deployment image using only the canonical synthetic fixture.

Usage: python tools/tests/cloud_container_check.py --image tokyo-hosting:ci
No real-data key, hosting account or externally supplied files are required.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cloud_data import APP, CloudDataTests


API_CHECK = r'''
import http.client, json
from pathlib import Path
import sys
settings = json.load(sys.stdin)
credentials = settings['credentials']
def request(method, path, data=None, cookie=None,
            origin='https://tokyo.example', host='tokyo.example'):
    connection = http.client.HTTPConnection('127.0.0.1', 10000, timeout=10)
    headers = {'Host': host}
    if cookie:
        headers['Cookie'] = cookie
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers.update({'Content-Type': 'application/json',
                        'X-Tokyo-Request': '1', 'Origin': origin})
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    raw, status, headers = response.read(), response.status, dict(response.getheaders())
    connection.close()
    try:
        result = json.loads(raw)
    except ValueError:
        result = None
    return status, result, headers

assert request('GET', '/api/state')[0] == 401
assert request('GET', '/api/health', host='attacker.invalid')[0] == 403
assert request('POST', '/api/setup', credentials)[0] == 403
status, body, headers = request('POST', '/api/login', credentials)
assert status == 200 and body['user']['role'] == 'admin'
cookie = headers['Set-Cookie']
assert all(flag in cookie for flag in ('Secure', 'HttpOnly', 'SameSite=Strict'))
cookie = cookie.split(';', 1)[0]
assert request('POST', '/api/logout', {}, cookie, origin='http://tokyo.example')[0] == 403
status, body, _ = request('GET', '/api/state', cookie=cookie)
assert status == 200
state, scenario = body['state'], body['scenario']
assert len(state['creditors']) == settings['creditors']
assert len(state['titles']) == settings['titles']
assert state['opening_cash_cents'] == 7654321
if settings['update']:
    status, _, _ = request('POST', '/api/creditor-contact', dict(
        id='base', expectedVersion=scenario['version'],
        creditorId=state['creditors'][0]['id'], trade_name='Container QA',
        reason='Teste sintetico de persistencia'), cookie)
    assert status == 200
else:
    assert state['creditors'][0]['trade_name'] == 'Container QA'
    assert scenario['version'] > 1
for path in ('/data/titulos_snapshot.json', '/server/store.py', '/bootstrap.enc'):
    assert request('GET', path)[0] == 404
uid = next(line for line in Path('/proc/1/status').read_text().splitlines()
           if line.startswith('Uid:')).split()[1:]
assert all(int(value) == 10001 for value in uid)
private = Path('/var/data/private')
database = private / 'tokyo.sqlite3'
assert (database.stat().st_uid, database.stat().st_gid) == (10001, 10001)
assert database.stat().st_mode & 0o777 == 0o600
assert private.stat().st_mode & 0o777 == 0o700
assert not Path('/opt/tokyo/app/data').exists()
assert not Path('/run/secrets/build_ca').exists()
'''


def check(image: str, docker: list[str]) -> dict:
    if json.loads((APP / 'PACOTE.json').read_text(encoding='utf-8'))['seed_data'] != 'synthetic':
        raise ValueError('The container check requires the canonical synthetic demo data.')
    fixture = CloudDataTests()
    fixture.setUp()
    name = 'tokyo-cloud-qa-' + uuid.uuid4().hex[:10]
    volume = name + '-data'
    checks = []

    def run(arguments, *, data=None, required=True):
        result = subprocess.run(docker + arguments, input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if required and result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
        return result

    def healthy():
        for _ in range(120):
            probe = run(['exec', name, 'python', '-I', '-B',
                         '/opt/tokyo/deploy/healthcheck.py'], required=False)
            if probe.returncode == 0:
                return
            running = run(['inspect', '--format', '{{.State.Running}}', name]).stdout.strip()
            if running != b'true':
                logs = run(['logs', name])
                raise RuntimeError((logs.stdout + logs.stderr).decode(errors='replace'))
            time.sleep(0.25)
        raise RuntimeError('The synthetic container did not become healthy.')

    def create(environment):
        run(['create', '--name', name, '--network', 'none', '--env-file', str(environment),
             '--mount', 'type=volume,src=' + volume + ',dst=/var/data', image])

    try:
        environment = dict(fixture.env, TOKYO_PUBLIC_URL='https://TOKYO.example:443',
                           TOKYO_BACKUP_KEY=fixture.backup_secret)
        cold = fixture.base / 'cold.env'
        cold.write_text(''.join(key + '=' + value + '\n' for key, value in environment.items()))
        cold.chmod(0o600)
        warm = fixture.base / 'warm.env'
        warm.write_text(''.join(key + '=' + environment[key] + '\n'
                                for key in ('TOKYO_PUBLIC_URL', 'TOKYO_BACKUP_KEY')))
        warm.chmod(0o600)
        credentials = dict(email=fixture.env['TOKYO_ADMIN_EMAIL'], password=fixture.password)
        settings = dict(credentials=credentials, creditors=len(fixture.snapshots['creditors']),
                        titles=len(fixture.snapshots['titles']), update=True)
        run(['volume', 'create', volume])
        create(cold)
        # Stream only synthetic authenticated ciphertext; the Docker daemon
        # does not need filesystem access to the host's temporary directory.
        contents = io.BytesIO()
        with tarfile.open(fileobj=contents, mode='w') as archive:
            data = fixture.payload.read_bytes()
            info = tarfile.TarInfo('bootstrap.enc')
            info.size, info.mode = len(data), 0o444
            archive.addfile(info, io.BytesIO(data))
        run(['cp', '-', name + ':/opt/tokyo/'], data=contents.getvalue())
        run(['start', name])
        healthy()
        checks.append('cold boot on a root-owned volume; canonical HTTPS health host')
        run(['exec', '-i', name, 'python', '-I', '-B', '-c', API_CHECK],
            data=json.dumps(settings).encode())
        checks.extend([
            'authenticated API, secure cookies, forbidden origin and public setup',
            'UID10001, private directory700 and database600',
            'synthetic data and API mutation; no plaintext image data or build CA',
        ])
        run(['exec', name, 'python', '-I', '-B', '-c',
             "from pathlib import Path; files=list(Path('/var/data/private/backups').glob('*.sqlite3.enc')); "
             "assert files and all(p.read_bytes().startswith(b'TOKYO-DB-V1\\0') for p in files)"])
        checks.append('encrypted initial backup on the persistent volume')
        run(['stop', '--time', '5', name])
        run(['rm', name])
        create(warm)
        run(['start', name])
        healthy()
        checks.append('replacement container starts without DATA_KEY or admin environment')
        settings['update'] = False
        run(['exec', '-i', name, 'python', '-I', '-B', '-c', API_CHECK],
            data=json.dumps(settings).encode())
        checks.append('original credentials, mutated state and version survive replacement')
        metadata = json.loads(run(['image', 'inspect', image]).stdout)[0]
        assert not any(value.startswith(('TOKYO_DATA_KEY=', 'TOKYO_ADMIN_EMAIL=',
                                         'TOKYO_ADMIN_PASSWORD=', 'TOKYO_BACKUP_KEY='))
                       for value in metadata['Config']['Env'])
        checks.append('no provisioning credentials or keys in the image environment')
        return dict(passed=len(checks), checks=checks, image_id=metadata['Id'], synthetic_only=True)
    finally:
        run(['rm', '--force', name], required=False)
        run(['volume', 'rm', volume], required=False)
        fixture.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='tokyo-hosting:ci')
    parser.add_argument('--docker-config', type=Path, help='Optional writable Docker CLI config directory.')
    args = parser.parse_args()
    docker = ['docker']
    if args.docker_config:
        docker += ['--config', str(args.docker_config)]
    print(json.dumps(check(args.image, docker), indent=2))


if __name__ == '__main__':
    main()
