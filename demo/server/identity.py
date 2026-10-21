"""Identifica código e localização da base sem expor caminhos ou conteúdo."""
import hashlib
from pathlib import Path

def instance_id(db_path):
    return hashlib.sha256(str(Path(db_path).resolve()).casefold().encode('utf8')).hexdigest()[:16]

def build_id(root):
    root=Path(root)
    files=[root/'package.json',*sorted((root/'server').glob('*.py')),*sorted(p for p in (root/'dist').rglob('*') if p.is_file())]
    h=hashlib.sha256()
    for path in files:
        h.update(path.relative_to(root).as_posix().encode('utf8')+b'\0');h.update(path.read_bytes());h.update(b'\0')
    return h.hexdigest()[:24]
