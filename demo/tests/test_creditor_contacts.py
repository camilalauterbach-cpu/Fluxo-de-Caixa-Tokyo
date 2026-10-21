"""Supplier contact metadata, HTTP permissions and persistence on demo-only data."""
from __future__ import annotations

import copy
import http.client
import json
import secrets
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from app import Handler
from store import Store

CONTACT_FIELDS = ('trade_name', 'negotiation_contact', 'negotiation_phone')


class CreditorContactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tokyo-contacts-test-')
        self.db_path = Path(self.temporary.name) / 'demo.sqlite3'
        self.server = None
        self.cookie = ''
        self.addCleanup(self.cleanup)
        self.start_server()
        status, user, self.cookie = self.request('/api/setup', {
            'name': 'Gestora de validação', 'email': 'admin@example.invalid',
            'password': secrets.token_urlsafe(24),
        })
        self.assertEqual(status, 200)
        self.admin = user['user']

    def start_server(self):
        self.store = Store(ROOT, self.db_path)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.server.store = self.store
        self.server.allowed_hosts = {f'127.0.0.1:{self.port}', f'localhost:{self.port}'}
        self.server.attempts = {}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.server = None

    def cleanup(self):
        self.stop_server()
        self.temporary.cleanup()

    def request(self, path, data=None, *, cookie=None):
        selected_cookie = self.cookie if cookie is None else cookie
        headers = {'Cookie': selected_cookie} if selected_cookie else {}
        if data is not None:
            headers.update({'Content-Type': 'application/json', 'X-Tokyo-Request': '1'})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('GET' if data is None else 'POST', path,
                               None if data is None else json.dumps(data), headers)
            response = connection.getresponse()
            body = json.loads(response.read())
            received_cookie = (response.getheader('Set-Cookie') or '').split(';', 1)[0]
            return response.status, body, received_cookie
        finally:
            connection.close()

    def state(self):
        status, body, _ = self.request('/api/state?id=base')
        self.assertEqual(status, 200)
        return body

    def contact(self, creditor_id='DEMO-01', *, expected=None, cookie=None, **fields):
        if expected is None:
            expected = self.state()['scenario']['version']
        return self.request('/api/creditor-contact', {
            'id': 'base', 'expectedVersion': expected, 'creditorId': creditor_id,
            'reason': 'Atualizar contatos da negociação', **fields,
        }, cookie=cookie)

    def creditor(self, state, creditor_id='DEMO-01', calculation=False):
        return next(c for c in state['calculation' if calculation else 'state']['creditors']
                    if c['id'] == creditor_id)

    def account(self, role, suffix=''):
        email = role + suffix + '@example.invalid'
        password = secrets.token_urlsafe(24)
        status, _, _ = self.request('/api/users', {
            'name': role + suffix, 'email': email, 'password': password, 'role': role,
        })
        self.assertEqual(status, 200)
        status, result, cookie = self.request('/api/login', {
            'email': email, 'password': password,
        }, cookie='')
        self.assertEqual(status, 200)
        return result['user'], cookie

    def assign(self, user_id, creditor_id='DEMO-01'):
        status, result, _ = self.request('/api/assignment', {
            'id': 'base', 'expectedVersion': self.state()['scenario']['version'],
            'creditorId': creditor_id, 'userId': user_id,
            'reason': 'Atribuição sintética para testar permissões',
        })
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])

    def comparable_calculation(self, state):
        result = copy.deepcopy(state['calculation'])
        result.pop('calculation_hash')
        for creditor in result['creditors']:
            for key in CONTACT_FIELDS:
                creditor.pop(key, None)
        return result

    def test_legacy_creditors_without_contacts_remain_readable(self):
        current = self.state()
        self.assertEqual(len(current['state']['creditors']), 2)
        for collection in (current['state']['creditors'], current['calculation']['creditors']):
            self.assertTrue(all(all(key not in c for key in CONTACT_FIELDS) for c in collection))
        self.assertEqual(current['calculation']['end_balance_cents'], 8650000)

    def test_contacts_save_read_and_restart_without_financial_changes(self):
        before = self.state()
        contacts = {'trade_name': 'Alfa Comercial', 'negotiation_contact': 'Ana — negociação',
                    'negotiation_phone': ' +55 (11) 90000-1234 ramal 7 '}
        status, result, _ = self.contact(**contacts)
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        after = self.state()
        self.assertEqual(after['scenario']['version'], before['scenario']['version'] + 1)
        for calculated in (False, True):
            creditor = self.creditor(after, calculation=calculated)
            self.assertEqual({key: creditor[key] for key in CONTACT_FIELDS}, contacts)
            self.assertEqual(creditor['id'], self.creditor(before)['id'])
            self.assertEqual(creditor['name'], self.creditor(before)['name'])
        self.assertEqual(self.comparable_calculation(after), self.comparable_calculation(before))
        self.assertEqual(after['state']['titles'], before['state']['titles'])
        self.assertEqual(after['state']['proposals'], before['state']['proposals'])
        with self.store.connect() as db:
            persisted = json.loads(db.execute("SELECT payload FROM scenarios WHERE id='base'").fetchone()[0])
            saved = next(c for c in persisted['creditors'] if c['id'] == 'DEMO-01')
            self.assertEqual({key: saved[key] for key in CONTACT_FIELDS}, contacts)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM facts').fetchone()[0], 0)
        audit = self.store.audit('base')
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]['action'], audit[0]['reason']),
                         ('creditor-contact', 'Atualizar contatos da negociação'))
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state(), after)

    def test_empty_strings_clear_and_omitted_fields_preserve_contacts(self):
        initial = {'trade_name': 'Nome da loja', 'negotiation_contact': 'Contato de teste',
                   'negotiation_phone': '(11) 1234-5678'}
        self.assertEqual(self.contact(**initial)[0], 200)
        self.assertEqual(self.contact(trade_name='Outro nome')[0], 200)
        after = self.creditor(self.state())
        self.assertEqual(after['trade_name'], 'Outro nome')
        self.assertEqual(after['negotiation_contact'], initial['negotiation_contact'])
        self.assertEqual(after['negotiation_phone'], initial['negotiation_phone'])
        self.assertEqual(self.contact(**dict.fromkeys(CONTACT_FIELDS, ''))[0], 200)
        after = self.creditor(self.state())
        self.assertTrue(all(after[key] == '' for key in CONTACT_FIELDS))

    def test_length_limits_accept_boundaries_and_reject_invalid_values_atomically(self):
        boundaries = {'trade_name': 'A' * 150, 'negotiation_contact': 'B' * 150,
                      'negotiation_phone': '+' * 50}
        self.assertEqual(self.contact(**boundaries)[0], 200)
        before = self.state()
        audit = self.store.audit('base')
        for field, maximum in (('trade_name', 150), ('negotiation_contact', 150), ('negotiation_phone', 50)):
            for invalid in ('X' * (maximum + 1), None, 123, ['contato'], {'value': 'contato'}):
                with self.subTest(field=field, value=invalid):
                    status, _, _ = self.contact(**{field: invalid})
                    self.assertEqual(status, 422)
                    self.assertEqual(self.state(), before)
                    self.assertEqual(self.store.audit('base'), audit)

    def test_stale_revision_returns_conflict_without_overwriting_contacts(self):
        revision = self.state()['scenario']['version']
        self.assertEqual(self.contact(expected=revision, trade_name='Primeira gravação')[0], 200)
        before = self.state()
        status, result, _ = self.contact(expected=revision, trade_name='Gravação desatualizada')
        self.assertEqual(status, 409)
        self.assertTrue(result['conflict'])
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.store.audit('base')), 1)

    def test_editor_can_edit_only_supplier_assigned_to_same_user(self):
        editor, editor_cookie = self.account('editor')
        _, other_cookie = self.account('editor', 'other')
        self.assign(editor['id'])
        self.assertEqual(self.contact(cookie=editor_cookie, negotiation_contact='Meu contato')[0], 200)
        before = self.state()
        audit = self.store.audit('base')
        for cookie, creditor_id in ((editor_cookie, 'DEMO-02'), (other_cookie, 'DEMO-01')):
            with self.subTest(creditor=creditor_id):
                self.assertEqual(self.contact(creditor_id, cookie=cookie, trade_name='Sem permissão')[0], 403)
                self.assertEqual(self.state(), before)
                self.assertEqual(self.store.audit('base'), audit)

    def test_admin_can_edit_assigned_and_unassigned_suppliers(self):
        editor, _ = self.account('editor')
        self.assign(editor['id'])
        for creditor_id in ('DEMO-01', 'DEMO-02'):
            with self.subTest(creditor=creditor_id):
                self.assertEqual(self.contact(creditor_id, trade_name='Atualizado pela gestora')[0], 200)
                self.assertEqual(self.creditor(self.state(), creditor_id)['trade_name'], 'Atualizado pela gestora')

    def test_reader_cannot_edit_contacts(self):
        _, cookie = self.account('reader')
        before = self.state()
        self.assertEqual(self.contact(cookie=cookie, negotiation_phone='12345')[0], 403)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.store.audit('base'), [])

    def test_unknown_supplier_rejected_without_changing_state_or_audit(self):
        before = self.state()
        self.assertEqual(self.contact('MISSING', trade_name='Inexistente')[0], 422)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.store.audit('base'), [])


if __name__ == '__main__':
    unittest.main()
