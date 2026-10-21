"""Production hosting through real HTTP and disposable, synthetic SQLite data."""
from __future__ import annotations

import hashlib
import http.client
import json
import secrets
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import app
from hosting import HostingError, load_config, validate_production_database
from store import Store

PUBLIC = 'https://tokyo.example.invalid'
HOST = 'tokyo.example.invalid'


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class HostingConfigurationTests(unittest.TestCase):
    def test_local_defaults_keep_legacy_loopback_and_origin(self):
        config = load_config(environ={})
        self.assertFalse(config.production)
        self.assertEqual((config.host, config.port, config.db_path), ('127.0.0.1', 8844, None))
        self.assertEqual(config.allowed_origins(), {'http://127.0.0.1:8844', 'http://localhost:8844'})

    def test_render_environment_and_explicit_overrides(self):
        environment = {'TOKYO_PRODUCTION': '1', 'TOKYO_DB_PATH': '/private/tokyo.sqlite3',
                       'PORT': '10000', 'RENDER_EXTERNAL_URL': PUBLIC + '/'}
        config = load_config(environ=environment)
        self.assertEqual((config.production, config.host, config.port), (True, '0.0.0.0', 10000))
        self.assertEqual(config.public_origin, PUBLIC)
        self.assertEqual(config.allowed_origins(), {PUBLIC})
        environment.update(TOKYO_PORT='10001', TOKYO_PUBLIC_URL='https://configured.example.invalid')
        self.assertEqual(load_config(environ=environment).port, 10001)
        config = load_config(port=10002, host='127.0.0.1', db='/explicit/db.sqlite3',
                             public_url=PUBLIC, environ=environment)
        self.assertEqual((config.port, config.host, config.db_path, config.public_origin),
                         (10002, '127.0.0.1', Path('/explicit/db.sqlite3'), PUBLIC))

    def test_production_rejects_missing_relative_database_and_invalid_public_origins(self):
        for database in (None, 'relative.sqlite3'):
            with self.subTest(database=database), self.assertRaises(HostingError):
                load_config(production=True, db=database, public_url=PUBLIC, environ={})
        for origin in (None, 'http://tokyo.example.invalid', PUBLIC + '/app', PUBLIC + '?q=1',
                       PUBLIC + '#part', 'https://user:password@example.invalid',
                       'https://*.example.invalid', 'https://evil\\host.invalid',
                       'https://example.invalid:0', 'https://bad\x00host.invalid'):
            with self.subTest(origin=origin), self.assertRaises(HostingError):
                load_config(production=True, db='/private/db.sqlite3', public_url=origin, environ={})

    def test_invalid_ports_and_production_mode_fail_at_configuration(self):
        for port in (True, 0, 65536, 'invalid', 3.5):
            with self.subTest(port=port), self.assertRaises(HostingError):
                load_config(port=port, environ={})
        with self.assertRaises(HostingError):
            load_config(environ={'TOKYO_PRODUCTION': 'sometimes'})


class ProductionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tokyo-production-db-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def settings(self, path):
        return load_config(production=True, db=path, host='127.0.0.1', port=free_port(),
                           public_url=PUBLIC, environ={})

    def assert_rejected_before_store(self, path):
        before = path.read_bytes() if path.is_file() else None
        with patch.object(app, 'Store', side_effect=AssertionError('Store must not be called')) as factory:
            with self.assertRaises(HostingError):
                app.create_server(config=self.settings(path))
            factory.assert_not_called()
        self.assertEqual(path.read_bytes() if path.is_file() else None, before)

    def test_missing_empty_and_non_sqlite_fail_without_creating_or_changing_files(self):
        missing = self.directory / 'not-created' / 'db.sqlite3'
        self.assert_rejected_before_store(missing)
        self.assertFalse(missing.parent.exists())
        empty = self.directory / 'empty.sqlite3'
        empty.touch()
        self.assert_rejected_before_store(empty)
        invalid = self.directory / 'invalid.sqlite3'
        invalid.write_bytes(b'not a SQLite database')
        self.assert_rejected_before_store(invalid)

    def test_missing_admin_scenarios_and_incompatible_schema_fail_readonly(self):
        path = self.directory / 'existing.sqlite3'
        store = Store(ROOT, path)
        self.assert_rejected_before_store(path)
        admin = store.setup('QA sintético', 'qa@example.invalid', secrets.token_urlsafe(24))
        with store.connect() as database:
            database.execute('UPDATE users SET active=0 WHERE id=?', (admin['id'],))
        self.assert_rejected_before_store(path)
        with store.connect() as database:
            database.execute('UPDATE users SET active=1 WHERE id=?', (admin['id'],))
            database.execute('DELETE FROM scenarios')
        self.assert_rejected_before_store(path)
        incompatible = self.directory / 'incompatible.sqlite3'
        with sqlite3.connect(incompatible) as database:
            database.execute('CREATE TABLE users(id TEXT)')
        self.assert_rejected_before_store(incompatible)

    def test_bad_scenario_payload_is_rejected_before_store_and_valid_database_is_readonly(self):
        path = self.directory / 'existing.sqlite3'
        store = Store(ROOT, path)
        store.setup('QA sintético', 'qa@example.invalid', secrets.token_urlsafe(24))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(validate_production_database(path), path.resolve())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        for payload in ('not-json', '[]', '{}'):
            with store.connect() as database:
                database.execute('UPDATE scenarios SET payload=?', (payload,))
            self.assert_rejected_before_store(path)


class ProductionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tokyo-production-http-')
        self.addCleanup(self.cleanup)
        self.db_path = Path(self.temporary.name) / 'tokyo.sqlite3'
        self.password = secrets.token_urlsafe(24)
        self.email = 'hosting@example.invalid'
        self.store = Store(ROOT, self.db_path)
        self.store.setup('QA hospedagem', self.email, self.password)
        self.server = None
        self.cookie = ''
        self.start()

    def start(self, production=True):
        settings = load_config(production=production, db=self.db_path, port=free_port(),
                               host='127.0.0.1', public_url=PUBLIC, environ={})
        with patch('store.seed', side_effect=AssertionError('Existing databases must not be seeded')):
            self.server = app.create_server(config=settings)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.server = None

    def cleanup(self):
        self.stop()
        self.temporary.cleanup()

    def request(self, path, data=None, *, host=HOST, origin=PUBLIC, raw=None, headers=None):
        selected = {'Host': host, 'Cookie': self.cookie}
        if origin is not None:
            selected['Origin'] = origin
        if data is not None or raw is not None:
            selected.update({'Content-Type': 'application/json', 'X-Tokyo-Request': '1'})
        selected.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('POST' if data is not None or raw is not None else 'GET', path,
                               raw if raw is not None else json.dumps(data) if data is not None else None,
                               selected)
            response = connection.getresponse()
            content = response.read()
            result = json.loads(content) if response.getheader('Content-Type', '').startswith('application/json') else content
            return response.status, result, response.getheader('Set-Cookie', '')
        finally:
            connection.close()

    def login(self, **options):
        status, user, cookie = self.request('/api/login', {'email': self.email, 'password': self.password}, **options)
        self.assertEqual(status, 200)
        self.cookie = cookie.split(';', 1)[0]
        return user, cookie

    def state(self):
        status, result, _ = self.request('/api/state?id=base')
        self.assertEqual(status, 200)
        return result

    def test_single_origin_frontend_health_login_and_secure_logout(self):
        status, page, _ = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'<div id="root">', page)
        self.assertEqual(self.request('/api/health')[0], 200)
        user, cookie = self.login()
        self.assertEqual(user['user']['role'], 'admin')
        for attribute in ('; Secure', '; HttpOnly', '; SameSite=Strict', '; Path=/'):
            self.assertIn(attribute, cookie)
        self.assertEqual(len(self.state()['state']['titles']), 6)
        status, result, logout = self.request('/api/logout', {})
        self.assertEqual((status, result), (200, {'ok': True}))
        self.assertIn('; Secure', logout)
        self.assertIn('Max-Age=0', logout)
        self.assertEqual(self.request('/api/state?id=base')[0], 401)

    def test_production_setup_is_forbidden_even_with_loopback_proxy_and_invalid_json(self):
        before = self.store.users()
        for host in (HOST, f'127.0.0.1:{self.port}'):
            for raw in ('{}', '{invalid'):
                with self.subTest(host=host, malformed=raw != '{}'):
                    self.assertEqual(self.request('/api/setup', raw=raw, host=host, origin=None)[0], 403)
        self.assertEqual(self.request('/api/setup')[0], 403)
        self.assertEqual(self.store.users(), before)

    def test_foreign_hosts_origins_and_forwarded_spoofing_cannot_mutate(self):
        self.login()
        before = self.state()
        payload = {'id': 'base', 'expectedVersion': before['scenario']['version'],
                   'creditorId': 'DEMO-01', 'trade_name': 'QA somente', 'reason': 'QA bloqueio origem'}
        for host, origin, headers in (
            ('evil.example.invalid', PUBLIC, {'X-Forwarded-Host': HOST, 'X-Forwarded-Proto': 'https'}),
            (HOST + '.evil.invalid', PUBLIC, {}),
            (HOST, 'https://evil.example.invalid', {}),
            (HOST, 'http://' + HOST, {'X-Forwarded-Proto': 'https'}),
            (HOST, PUBLIC + '.evil.invalid', {}),
            (HOST, f'http://127.0.0.1:{self.port}', {}),
            (HOST, 'null', {}),
        ):
            with self.subTest(host=host, origin=origin):
                self.assertEqual(self.request('/api/creditor-contact', payload, host=host,
                                              origin=origin, headers=headers)[0], 403)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.store.audit('base'), [])
        for host in (f'localhost:{self.port}', f'127.0.0.1:{self.port}'):
            self.assertEqual(self.request('/api/health', host=host, origin=None)[0], 200)

    def test_authenticated_contact_payment_fact_session_and_calculation_survive_restart(self):
        self.login()
        before = self.state()
        status, _, _ = self.request('/api/creditor-contact', {
            'id': 'base', 'expectedVersion': before['scenario']['version'], 'creditorId': 'DEMO-01',
            'trade_name': 'Contato QA fictício', 'negotiation_contact': 'Pessoa QA',
            'negotiation_phone': '000-QA', 'reason': 'QA persistência hospedagem',
        })
        self.assertEqual(status, 200)
        status, _, _ = self.request('/api/liquidation', {
            'id': 'base', 'expectedVersion': self.state()['scenario']['version'], 'eventKey': 'T:DEMO-O1',
            'amount_cents': 10000, 'date': '2026-10-20', 'bank': 'Cora',
            'evidence': 'Comprovante fictício QA', 'reason': 'Fluxo já previsto', 'composition_confirmed': True,
        })
        self.assertEqual(status, 200)
        saved = self.state()
        audit = self.store.audit('base')
        self.stop()
        self.start()
        self.assertEqual(self.state(), saved)
        self.assertEqual(self.store.audit('base'), audit)
        self.assertEqual(saved['state']['payments'][0]['bank'], 'Cora')
        self.assertEqual(saved['scenario']['version'], 3)
        self.assertEqual(len(audit), 2)

    def test_existing_database_runs_without_bundled_seed_data(self):
        self.login()
        expected = self.state()['calculation']
        self.stop()
        image_root = Path(self.temporary.name) / 'image-app'
        image_root.mkdir()
        (image_root / 'package.json').write_text('{"version":"hosting-test"}', encoding='utf8')
        with patch.object(app, 'ROOT', image_root):
            self.start()
            self.assertEqual(self.request('/api/health')[1]['version'], 'hosting-test')
            self.assertEqual(self.state()['calculation'], expected)
        self.assertFalse((image_root / 'data').exists())

    def test_local_legacy_http_cookie_and_origin_remain_compatible(self):
        self.stop()
        with patch.dict('os.environ', {}, clear=True):
            self.server = app.create_server(free_port(), self.db_path)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        _, cookie = self.login(host=f'127.0.0.1:{self.port}', origin=f'http://127.0.0.1:{self.port}')
        self.assertNotIn('; Secure', cookie)
        self.assertIn('; HttpOnly', cookie)
        self.assertIn('; SameSite=Strict', cookie)
        self.assertEqual(self.request('/api/session', host=f'localhost:{self.port}', origin=None)[0], 200)
        status, _, logout = self.request('/api/logout', {}, host=f'localhost:{self.port}',
                                         origin=f'http://localhost:{self.port}')
        self.assertEqual(status, 200)
        self.assertNotIn('; Secure', logout)


if __name__ == '__main__':
    unittest.main()
