from __future__ import annotations
import contextlib,hashlib,hmac,json,secrets,sqlite3,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from engine import seed,DomainError
class Conflict(Exception):pass

def stamp():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex

def password_hash(password,salt=None):
    if len(password)<10 or len(password)>200:raise DomainError('Use uma senha com 10 a 200 caracteres.')
    salt=salt or secrets.token_hex(16)
    digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return salt+':'+digest

def password_matches(password,saved):
    try:return hmac.compare_digest(password_hash(password,saved.split(':')[0]),saved)
    except (ValueError,DomainError):return False

class Store:
    def __init__(self,root,db_path=None,initial_state=None):
        self.root=Path(root);self.path=Path(db_path or self.root/'private'/'tokyo.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,email TEXT UNIQUE,hash TEXT,role TEXT,active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT,expires INTEGER);
            CREATE TABLE IF NOT EXISTS scenarios(id TEXT PRIMARY KEY,name TEXT,version INTEGER,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,scenario_id TEXT,version INTEGER,actor TEXT,action TEXT,reason TEXT,created TEXT,before_payload TEXT,after_payload TEXT);
            CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,requested_by TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS requests_owner ON requests(requested_by,status);
            CREATE INDEX IF NOT EXISTS requests_scenario ON requests(scenario_id,status);
            ''')
            if not db.execute('SELECT id FROM scenarios LIMIT 1').fetchone():
                s=seed(self.root) if initial_state is None else initial_state;db.execute('INSERT INTO scenarios VALUES(?,?,?,?,?)',('base',s['name'],1,json.dumps(s,ensure_ascii=False),stamp()))
    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row;db.execute('PRAGMA busy_timeout=15000')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:db.close()
    def empty(self):
        with self.connect() as db:return db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
    def setup(self,name,email,password):
        hashed=password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:raise DomainError('O acesso inicial já foi criado.')
            user=dict(id=uid(),name=name.strip()[:100],email=email.strip().lower()[:150],role='admin')
            if not user['name'] or '@' not in user['email']:raise DomainError('Informe nome e e-mail válidos.')
            db.execute('INSERT INTO users(id,name,email,hash,role) VALUES(?,?,?,?,?)',(user['id'],user['name'],user['email'],hashed,'admin'))
        return user
    def login(self,email,password):
        with self.connect() as db:r=db.execute('SELECT * FROM users WHERE email=? AND active=1',(email.strip().lower(),)).fetchone()
        if not r or not password_matches(password,r['hash']):raise DomainError('E-mail ou senha não conferem.')
        return {k:r[k] for k in ['id','name','email','role']}
    def session(self,user):
        token=secrets.token_urlsafe(40)
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(int(time.time()),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],int(time.time())+43200))
        return token
    def auth(self,token):
        if not token:return None
        with self.connect() as db:r=db.execute('SELECT u.id,u.name,u.email,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        return dict(r) if r else None
    def logout(self,token):
        with self.connect() as db:db.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
    def users(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,name,email,role,active FROM users ORDER BY name')]
    def add_user(self,data):
        if data.get('role') not in ['admin','editor','reader']:raise DomainError('Perfil inválido.')
        hashed=password_hash(data.get('password',''))
        if not data.get('name','').strip() or '@' not in data.get('email',''):raise DomainError('Informe nome e e-mail.')
        with self.connect() as db:
            try:db.execute('INSERT INTO users(id,name,email,hash,role) VALUES(?,?,?,?,?)',(uid(),data['name'][:100],data['email'].strip().lower(),hashed,data['role']))
            except sqlite3.IntegrityError:raise DomainError('Este e-mail já está cadastrado.')
    def scenarios(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,name,version,updated FROM scenarios ORDER BY updated DESC')]
    def load(self,sid,db=None):
        if db is None:
            with self.connect() as con:return self.load(sid,con)
        row=db.execute('SELECT * FROM scenarios WHERE id=?',(sid,)).fetchone()
        if not row:raise DomainError('Cenário não encontrado.')
        state=json.loads(row['payload']);state.setdefault('assignments',{});state['payments']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM facts')]
        return dict(id=row['id'],name=row['name'],version=row['version'],updated=row['updated']),state
    def mutate(self,sid,expected,user,action,reason,fn):
        if not reason or len(reason.strip())<3:raise DomainError('Registre um motivo para a alteração.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');meta,state=self.load(sid,db)
            if meta['version']!=expected:raise Conflict('Outra pessoa alterou este cenário. Recarregue e compare antes de salvar.')
            before=json.dumps(state,ensure_ascii=False);result=fn(state)
            # Liquidações são fatos globais, nunca exclusivas de uma cópia do cenário.
            for fact in state.get('payments',[]):db.execute('INSERT OR IGNORE INTO facts VALUES(?,?)',(fact['id'],json.dumps(fact,ensure_ascii=False)))
            persisted=dict(state,payments=[]);after=json.dumps(persisted,ensure_ascii=False);v=meta['version']+1
            db.execute('UPDATE scenarios SET name=?,version=?,payload=?,updated=? WHERE id=?',(state['name'],v,after,stamp(),sid))
            if action=='liquidation':db.execute('UPDATE scenarios SET version=version+1,updated=? WHERE id<>?',(stamp(),sid))
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?)',(uid(),sid,v,user['name'],action,reason[:2000],stamp(),before,after))
        return result
    def clone(self,sid,name,user):
        with self.connect() as db:
            meta,state=self.load(sid,db);state['name']=name.strip()[:100] or 'Nova possibilidade';state['payments']=[];new=uid()
            db.execute('INSERT INTO scenarios VALUES(?,?,?,?,?)',(new,state['name'],1,json.dumps(state,ensure_ascii=False),stamp()))
            db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?)',(uid(),new,1,user['name'],'scenario','Cópia de '+meta['name'],stamp(),'{}',json.dumps(state,ensure_ascii=False)))
        return new
    def audit(self,sid):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT id,version,actor,action,reason,created FROM audit WHERE scenario_id=? ORDER BY created DESC LIMIT 200',(sid,))]
    def payment_review_details(self,sid,reviews):
        """Explain factual payment reviews without changing any fact or allocation."""
        if not reviews:return []
        with self.connect() as db:
            facts={row['id']:json.loads(row['payload']) for row in db.execute('SELECT id,payload FROM facts')}
            scenarios=[(row['id'],row['name'],row['version'],json.loads(row['payload'])) for row in db.execute('SELECT id,name,version,payload FROM scenarios')]
        result=[]
        for review in reviews:
            pay=facts.get(review['payment_id'],{})
            key=review.get('event_key','');references=[];description=''
            for scenario_id,name,version,state in scenarios:
                matching=False
                if key.startswith('T:'):
                    title=next((t for t in state['titles'] if t['id']==key[2:]),None)
                    matching=title is not None
                    if title:description=description or title.get('description','')
                elif key.startswith('P:'):
                    parts=key.split(':');proposal=state['proposals'].get(parts[1]) if len(parts)==3 else None
                    matching=bool(proposal and parts[2].isdigit() and 1<=int(parts[2])<=proposal['n'])
                if matching and scenario_id!=sid:references.append(dict(id=scenario_id,name=name,version=version))
            result.append(dict(**review,movement={k:pay.get(k) for k in ['id','party','date','amount_cents','direction','evidence','bank']},description=description,reference_scenarios=references))
        return result

    def latest_change(self,sid):
        with self.connect() as db:
            row=db.execute("SELECT version,actor,action,reason,created,before_payload,after_payload FROM audit WHERE scenario_id=? AND action<>'scenario' ORDER BY version DESC LIMIT 1",(sid,)).fetchone()
            if not row:return None
            before=json.loads(row['before_payload']);after=json.loads(row['after_payload']);facts=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM facts')]
            before['payments']=facts;after['payments']=facts
            from planning import impact
            return dict(version=row['version'],actor=row['actor'],action=row['action'],reason=row['reason'],created=row['created'],impact=impact(before,after))
    def _workflow_save(self,db,meta,state,user,action,reason,before):
        persisted=dict(state,payments=[]);after=json.dumps(persisted,ensure_ascii=False);version=meta['version']+1
        db.execute('UPDATE scenarios SET name=?,version=?,payload=?,updated=? WHERE id=?',(state['name'],version,after,stamp(),meta['id']))
        db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?)',(uid(),meta['id'],version,user['name'],action,reason[:2000],stamp(),before,after))
        return version
    def assign(self,sid,expected,user,cid,user_id,reason):
        from workflow import clean
        from engine import creditor
        reason=clean(reason,3,'Registre um motivo para a atribuição.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');meta,state=self.load(sid,db)
            if meta['version']!=expected:raise Conflict('O cenário mudou. Recarregue antes de atribuir o fornecedor.')
            creditor(state,cid);before=json.dumps(state,ensure_ascii=False)
            if user_id:
                assigned=db.execute("SELECT id,name,role FROM users WHERE id=? AND active=1",(user_id,)).fetchone()
                if not assigned or assigned['role'] not in ['admin','editor']:raise DomainError('Escolha uma pessoa ativa com perfil de gestora ou negociadora.')
                state['assignments'][cid]=dict(user_id=assigned['id'],name=assigned['name'],assigned_by=user['id'],assigned_by_name=user['name'],assigned_at=stamp(),reason=reason)
            else:state['assignments'].pop(cid,None)
            version=self._workflow_save(db,meta,state,user,'assignment',reason,before)
        return dict(ok=True,version=version)
    def requests(self,user,sid=None):
        conditions=[];params=[]
        if user['role']!='admin':conditions.append('requested_by=?');params.append(user['id'])
        if sid:conditions.append('scenario_id=?');params.append(sid)
        query='SELECT payload FROM requests'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY rowid DESC'
        with self.connect() as db:return [json.loads(row['payload']) for row in db.execute(query,params)]
    def create_request(self,sid,expected,user,cid,proposal,evidence,reason):
        from engine import creditor
        from workflow import clean,proposal_snapshot,assess
        evidence=clean(evidence,5,'Registre a referência do retorno do fornecedor.')
        reason=clean(reason,3,'Registre o motivo da solicitação.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');meta,state=self.load(sid,db)
            if meta['version']!=expected:raise Conflict('O cenário mudou. Recarregue antes de solicitar a decisão.')
            owner=state['assignments'].get(cid,{})
            if user['role']!='admin' and owner.get('user_id')!=user['id']:raise PermissionError('Este fornecedor não está atribuído a você.')
            c=creditor(state,cid);snapshot,_,calculation=proposal_snapshot(state,cid,proposal)
            assessment=assess(state,cid,snapshot,calculation=calculation)
            now=stamp();request=dict(id=uid(),scenario_id=sid,creditor_id=cid,creditor_name=c['name'],requested_by=user['id'],requested_by_name=user['name'],status='pending',proposal=snapshot,evidence=evidence,reason=reason,created=now,updated=now,decision_note='',assessment=assessment,source_version=meta['version'])
            before=json.dumps(state,ensure_ascii=False)
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(request['id'],sid,user['id'],'pending',json.dumps(request,ensure_ascii=False)))
            request['submitted_version']=self._workflow_save(db,meta,state,user,'request',reason,before)
            db.execute('UPDATE requests SET payload=? WHERE id=?',(json.dumps(request,ensure_ascii=False),request['id']))
        return request
    def review_request(self,sid,expected,user,request_id,decision,note,acknowledge_risk):
        from workflow import clean,approve
        note=clean(note,5,'Registre a justificativa da decisão da gestora.')
        if decision not in ['approved','rejected']:raise DomainError('Decisão de revisão inválida.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');meta,state=self.load(sid,db)
            if meta['version']!=expected:raise Conflict('O cenário mudou. Recalcule a solicitação antes de decidir.')
            row=db.execute('SELECT payload FROM requests WHERE id=? AND scenario_id=?',(request_id,sid)).fetchone()
            if not row:raise DomainError('Solicitação não encontrada neste cenário.')
            request=json.loads(row['payload'])
            if request['status']!='pending':raise Conflict('Esta solicitação já recebeu uma decisão. Recarregue a caixa de revisão.')
            before=json.dumps(state,ensure_ascii=False)
            if decision=='approved':
                candidate,assessment=approve(state,request['creditor_id'],request['proposal'],request['evidence'],note,acknowledge_risk)
                state=candidate;request['submitted_assessment']=request['assessment'];request['assessment']=assessment
            request.update(status=decision,decision_note=note,reviewed_by=user['id'],reviewed_by_name=user['name'],updated=stamp(),reviewed_against_version=meta['version'],acknowledged_risk=acknowledge_risk is True)
            request['decision_version']=self._workflow_save(db,meta,state,user,'request-review',note,before)
            db.execute('UPDATE requests SET status=?,payload=? WHERE id=?',(decision,json.dumps(request,ensure_ascii=False),request_id))
        return dict(ok=True,request=request,version=request['decision_version'])
    def backup(self):
        target=self.path.parent/'backups'/('tokyo-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uid()[:6]+'.sqlite3');target.parent.mkdir(exist_ok=True)
        with self.connect() as src,contextlib.closing(sqlite3.connect(target)) as dst:src.backup(dst)
        return target.name