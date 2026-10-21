"""Receipt-bank metadata through the original HTTP API and disposable demo data."""
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
from engine import calculate
from store import Store


class ReceiptBankTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tokyo-bank-test-')
        self.db_path = Path(self.temporary.name) / 'demo.sqlite3'
        self.server = None
        self.cookie = ''
        self.addCleanup(self.cleanup)
        self.start_server()
        status, result, cookie = self.request('/api/setup', {
            'name': 'Validação isolada', 'email': 'bank-test@example.invalid',
            'password': secrets.token_urlsafe(24),
        })
        self.assertEqual(status, 200)
        self.assertEqual(result['user']['role'], 'admin')
        self.cookie = cookie

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

    def request(self, path, data=None):
        headers = {'Cookie': self.cookie} if self.cookie else {}
        if data is not None:
            headers.update({'Content-Type': 'application/json', 'X-Tokyo-Request': '1'})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request('GET' if data is None else 'POST', path,
                               None if data is None else json.dumps(data), headers)
            response = connection.getresponse()
            body = json.loads(response.read())
            cookie = (response.getheader('Set-Cookie') or '').split(';', 1)[0]
            return response.status, body, cookie
        finally:
            connection.close()

    def state(self, sid='base'):
        status, body, _ = self.request('/api/state?id=' + sid)
        self.assertEqual(status, 200)
        return body

    def liquidation(self, event_key, **extra):
        sid = extra.pop('id', 'base')
        current = self.state(sid)
        payload = {
            'id': sid, 'expectedVersion': current['scenario']['version'],
            'eventKey': event_key, 'amount_cents': 10000, 'date': '2026-10-20',
            'evidence': 'Comprovante sintético de validação',
            'reason': 'Fluxo já previsto', 'composition_confirmed': True,
        }
        payload.update(extra)
        return self.request('/api/liquidation', payload)

    def facts(self):
        with self.store.connect() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM facts')]

    def assert_bank_persists(self, bank, direction):
        key = 'T:DEMO-R1' if direction == 'in' else 'T:DEMO-O1'
        status, result, _ = self.liquidation(key, bank=bank)
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        current = self.state()
        self.assertEqual(current['scenario']['version'], 2)
        self.assertEqual(len(current['state']['payments']), 1)
        payment = current['state']['payments'][0]
        self.assertEqual((payment['bank'], payment['direction'], payment['event_key']), (bank, direction, key))
        self.assertEqual((payment['amount_cents'], payment['date']), (10000, '2026-10-20'))
        self.assertEqual(self.facts(), [payment])
        without_bank = copy.deepcopy(current['state'])
        del without_bank['payments'][0]['bank']
        original_calculation = dict(current['calculation'])
        unchanged_financials = calculate(without_bank)
        original_calculation.pop('calculation_hash')
        unchanged_financials.pop('calculation_hash')
        self.assertEqual(original_calculation, unchanged_financials)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state()['state']['payments'], [payment])
        self.assertEqual(self.facts(), [payment])

    def test_itau_outgoing_payment_persists_after_restart(self):
        self.assert_bank_persists('Itaú', 'out')

    def test_cora_outgoing_payment_persists_after_restart(self):
        self.assert_bank_persists('Cora', 'out')

    def test_itau_incoming_receipt_persists_after_restart(self):
        self.assert_bank_persists('Itaú', 'in')

    def test_cora_incoming_receipt_persists_after_restart(self):
        self.assert_bank_persists('Cora', 'in')

    def test_invalid_bank_rejects_without_state_fact_or_audit_changes(self):
        before = self.state()
        for invalid in ('Bradesco', 'Itaú ', 'itau', ['Itaú'], {'bank': 'Cora'}, False, 0):
            with self.subTest(bank=invalid):
                status, error, _ = self.liquidation('T:DEMO-O1', bank=invalid)
                self.assertEqual(status, 422)
                self.assertIn('Itaú ou Cora', error['error'])
                self.assertEqual(self.state(), before)
                self.assertEqual(self.facts(), [])
                self.assertEqual(self.store.audit('base'), [])

    def alternate_scenario_payment(self, **extra):
        status, body, _ = self.request('/api/scenarios', {'id': 'base', 'name': 'Cópia de validação'})
        self.assertEqual(status, 200)
        sid = body['id']
        status, _, _ = self.request('/api/entry', {
            'id': sid, 'expectedVersion': 1, 'kind': 'obligation',
            'party': 'Obrigação de teste isolado', 'amount_cents': 10000,
            'date': '2026-10-20', 'reason': 'Validação do comprovante entre cenários',
            'description': 'Título presente somente na cópia',
        })
        self.assertEqual(status, 200)
        title = next(t for t in self.state(sid)['state']['titles'] if t['party'] == 'Obrigação de teste isolado')
        status, _, _ = self.liquidation('T:' + title['id'], id=sid, **extra)
        self.assertEqual(status, 200)
        return self.state()['payment_review_details']

    def test_payment_review_exposes_recorded_bank_without_changing_fact(self):
        reviews = self.alternate_scenario_payment(bank='Itaú')
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]['movement']['bank'], 'Itaú')
        self.assertEqual(reviews[0]['movement']['amount_cents'], 10000)
        before = self.facts()
        self.assertEqual(self.state()['payment_review_details'], reviews)
        self.assertEqual(self.facts(), before)

    def test_legacy_client_without_bank_still_records_and_reads_review(self):
        reviews = self.alternate_scenario_payment()
        self.assertEqual(len(reviews), 1)
        self.assertIsNone(reviews[0]['movement']['bank'])
        self.assertNotIn('bank', self.facts()[0])
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state()['payment_review_details'], reviews)
        self.assertNotIn('bank', self.facts()[0])

    def test_empty_optional_bank_does_not_assign_legacy_bank(self):
        for empty in ('', None):
            with self.subTest(bank=empty):
                status, _, _ = self.liquidation('T:DEMO-O1', bank=empty)
                self.assertEqual(status, 200)
        self.assertEqual(len(self.facts()), 2)
        self.assertTrue(all('bank' not in fact for fact in self.facts()))


if __name__ == '__main__':
    unittest.main()
