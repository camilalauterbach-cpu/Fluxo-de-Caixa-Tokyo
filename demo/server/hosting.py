"""Explicit local/production hosting configuration for the existing application."""
from __future__ import annotations

import json
import ipaddress
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class HostingError(ValueError):
    """A startup error safe to report without exposing database contents."""


@dataclass(frozen=True)
class HostingConfig:
    production: bool
    host: str
    port: int
    db_path: Path | None
    public_origin: str | None = None

    def allowed_hosts(self, port=None):
        port = self.port if port is None else port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.public_origin:
            hosts.add(urlsplit(self.public_origin).netloc)
        return frozenset(hosts)

    def allowed_origins(self, port=None):
        if self.production:
            return frozenset({self.public_origin})
        return frozenset('http://' + host for host in self.allowed_hosts(port))


def public_origin(value):
    try:
        if not isinstance(value, str) or not value or any(character.isspace() for character in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or
                parsed.password is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError
        hostname = parsed.hostname.encode('idna').decode('ascii').lower()
        if ':' in hostname:
            ipaddress.IPv6Address(hostname)
        elif not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*', hostname):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        authority = '[' + hostname + ']' if ':' in hostname else hostname
        if parsed.port not in (None, 443):
            authority += ':' + str(parsed.port)
        return 'https://' + authority
    except (ValueError, UnicodeError):
        raise HostingError('Configure TOKYO_PUBLIC_URL ou RENDER_EXTERNAL_URL com a origem HTTPS completa, sem caminho.') from None


def load_config(*, port=None, db=None, host=None, public_url=None, production=None, environ=None):
    environment = os.environ if environ is None else environ
    if production is None:
        mode = environment.get('TOKYO_PRODUCTION', '0').lower()
        if mode not in ('', '0', '1', 'false', 'true'):
            raise HostingError('TOKYO_PRODUCTION deve ser 0 ou 1.')
        production = mode in ('1', 'true')
    if not isinstance(production, bool):
        raise HostingError('O modo de produção deve ser explícito.')
    selected_port = port if port is not None else environment.get('TOKYO_PORT') or environment.get('PORT') or 8844
    try:
        if not isinstance(selected_port, (str, int)) or isinstance(selected_port, bool):
            raise ValueError
        selected_port = int(selected_port)
        if not 1 <= selected_port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise HostingError('A porta do Tokyo deve estar entre 1 e 65535.') from None
    selected_host = host if host is not None else environment.get('TOKYO_HOST') or ('0.0.0.0' if production else '127.0.0.1')
    if not isinstance(selected_host, str) or not selected_host or any(c.isspace() for c in selected_host):
        raise HostingError('Configure um endereço de escuta válido em TOKYO_HOST.')
    selected_db = db if db is not None else environment.get('TOKYO_DB_PATH') or None
    try:
        path = Path(selected_db) if selected_db is not None else None
    except TypeError:
        raise HostingError('Configure o caminho do banco em TOKYO_DB_PATH.') from None
    origin = None
    if production:
        if path is None or not path.is_absolute():
            raise HostingError('Produção exige TOKYO_DB_PATH absoluto para uma base previamente preparada.')
        origin = public_origin(public_url if public_url is not None else
                               environment.get('TOKYO_PUBLIC_URL') or environment.get('RENDER_EXTERNAL_URL'))
    return HostingConfig(production, selected_host, selected_port, path, origin)


def validate_production_database(path):
    """Read-only prerequisite check, performed before Store can create or seed files."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
        raise HostingError('A base de produção não existe ou está vazia. Prepare a base e o administrador antes de iniciar.')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as database:
            if database.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise HostingError('A verificação de integridade da base de produção falhou.')
            required_columns = {
                'users': {'id', 'name', 'email', 'hash', 'role', 'active'},
                'scenarios': {'id', 'name', 'version', 'payload', 'updated'},
            }
            for table, required in required_columns.items():
                columns = {row[1] for row in database.execute(f'PRAGMA table_info({table})')}
                if not required <= columns:
                    raise HostingError('A base de produção é incompatível com o Tokyo.')
            if not database.execute("SELECT 1 FROM users WHERE role='admin' AND active=1 LIMIT 1").fetchone():
                raise HostingError('Prepare um administrador ativo antes de iniciar a produção.')
            records = database.execute('SELECT payload FROM scenarios').fetchall()
            if not records:
                raise HostingError('A base de produção não contém cenários. Nenhuma demonstração foi criada.')
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            facts = [json.loads(row[0]) for row in database.execute('SELECT payload FROM facts')] if 'facts' in tables else []
            from engine import calculate
            for row in records:
                state = json.loads(row[0])
                if not isinstance(state, dict):
                    raise ValueError
                calculate(dict(state, payments=facts))
    except HostingError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
        raise HostingError('A base de produção está inválida ou possui dados incompatíveis. Nenhuma demonstração foi criada.') from None
    return path.resolve()
