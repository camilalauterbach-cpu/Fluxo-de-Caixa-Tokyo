from __future__ import annotations
import argparse,copy,html,json,mimetypes,os,secrets,sys,time,traceback,uuid
from datetime import datetime,timedelta,timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parent))
from engine import DomainError,calculate,simulate,options,creditor,plan,day,money,seed,is_tax
from store import Store,Conflict,uid
from adjustments import adjust_forecast
from costs import paid_components
from workflow import assess,approve,confirm_negotiation
from identity import instance_id,build_id
from planning import apply_title,impact,periods,report,report_document,move_event
from hosting import HostingError,load_config,validate_production_database
ROOT=Path(__file__).resolve().parents[1]
BUILD_ID=build_id(ROOT)

class Forbidden(Exception):pass
class Unauthorized(Exception):pass

def text(v):return str(v or '').strip()[:2000]
def brl(v):return ('R$ '+f'{v/100:,.2f}').replace(',','x').replace('.',',').replace('x','.')

class Handler(BaseHTTPRequestHandler):
    server_version='Tokyo'
    def log_message(self,*args):pass
    @property
    def store(self):return self.server.store
    def cookie(self):
        c=SimpleCookie()
        try:c.load(self.headers.get('Cookie',''))
        except Exception:return ''
        return c['tokyo_session'].value if 'tokyo_session' in c else ''
    def session_cookie(self,token='',logout=False):
        value=f'tokyo_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={0 if logout else 43200}'
        return value+('; Secure' if getattr(self.server,'production',False) else '')
    def user(self):
        u=self.store.auth(self.cookie())
        if not u:raise Unauthorized('Entre para acessar o planejamento.')
        return u
    def role(self,u,*roles):
        if u['role'] not in roles:raise Forbidden('Seu perfil não permite esta alteração.')
    def send(self,body,status=200,kind='application/json; charset=utf-8',cookie=None):
        raw=json.dumps(body,ensure_ascii=False).encode() if isinstance(body,(dict,list)) else (body.encode() if isinstance(body,str) else body)
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store' if self.path.startswith('/api') else 'no-cache');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(raw)
    def guard(self):
        host=self.headers.get('Host','').lower()
        if len(self.headers.get_all('Host',[]))!=1 or host not in self.server.allowed_hosts:raise Forbidden('Endereço de acesso não autorizado.')
        if self.command=='POST':
            if self.headers.get('X-Tokyo-Request')!='1' or not self.headers.get('Content-Type','').startswith('application/json'):raise Forbidden('Origem da requisição não autorizada.')
            origin=self.headers.get('Origin')
            allowed=getattr(self.server,'allowed_origins',{'http://'+h for h in self.server.allowed_hosts})
            if len(self.headers.get_all('Origin',[]))>1 or origin and origin not in allowed:raise Forbidden('Origem da requisição não autorizada.')
    def do_GET(self):self.route('GET')
    def do_POST(self):self.route('POST')
    def route(self,method):
        try:
            self.guard();parsed=urlparse(self.path);path=parsed.path;q={k:v[0] for k,v in parse_qs(parsed.query).items()};data={}
            if path=='/api/setup' and getattr(self.server,'production',False):raise Forbidden('O primeiro acesso é preparado fora da interface pública.')
            if method=='POST':
                size=int(self.headers.get('Content-Length',0))
                if size<1 or size>1024*1024:raise DomainError('Requisição fora do tamanho permitido.')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise DomainError('Dados inválidos.')
            if path=='/api/health':return self.send(dict(application='Tokyo',status='ok',version=json.loads((ROOT/'package.json').read_text(encoding='utf8'))['version'],build_id=BUILD_ID,instance_id=instance_id(self.store.path)))
            if path=='/api/session':return self.send(dict(setup_required=self.store.empty(),user=self.store.auth(self.cookie())))
            if method=='POST' and path in ['/api/setup','/api/login']:
                key=self.client_address[0];now=time.time();attempts=self.server.attempts.setdefault(key,[]);attempts[:]=[a for a in attempts if now-a<60]
                if len(attempts)>=10:return self.send(dict(error='Muitas tentativas. Aguarde um minuto.'),429)
                attempts.append(now)
                if path.endswith('setup'):
                    if self.client_address[0] not in ['127.0.0.1','::1']:raise Forbidden('Crie o primeiro acesso no computador do servidor.')
                    user=self.store.setup(text(data.get('name')),text(data.get('email')),data.get('password',''))
                else:user=self.store.login(text(data.get('email')),data.get('password',''))
                token=self.store.session(user);attempts.clear();return self.send(dict(user=user),cookie=self.session_cookie(token))
            if not path.startswith('/api/'):
                if method!='GET':raise DomainError('Rota inválida.')
                rel='index.html' if path=='/' else path.lstrip('/');target=(ROOT/'dist'/rel).resolve();dist=(ROOT/'dist').resolve()
                if dist not in target.parents or not target.is_file():return self.send('Arquivo não encontrado.',404,'text/plain; charset=utf-8')
                return self.send(target.read_bytes(),kind=(mimetypes.guess_type(target.name)[0] or 'application/octet-stream')+('; charset=utf-8' if target.suffix in ['.js','.css','.html'] else ''))
            user=self.user()
            if path=='/api/logout':self.store.logout(self.cookie());return self.send(dict(ok=True),cookie=self.session_cookie(logout=True))
            if method=='GET':
                if path=='/api/scenarios':return self.send(self.store.scenarios())
                if path=='/api/users':self.role(user,'admin');return self.send(self.store.users())
                if path=='/api/audit':return self.send(self.store.audit(q.get('id','base')))
                if path=='/api/requests':
                    self.role(user,'admin','editor');return self.send(self.store.requests(user,q.get('id')))
                if path=='/api/state':
                    meta,state=self.store.load(q.get('id','base'));calc=calculate(state)
                    cache=getattr(self.server,'workflow_assessments',{});key=(meta['id'],meta['version'],calc['calculation_hash'])
                    if key not in cache:
                        assessments={}
                        for c in state['creditors']:
                            try:assessments[c['id']]=assess(state,c['id'],calculation=calc)
                            except DomainError as error:assessments[c['id']]=dict(minimum_payments=None,requested_payments=state['proposals'][c['id']]['n'],fits=False,shortfall_cents=max(0,-calc['minimum']['margin_cents']),first_shortfall=calc['first_shortfall'],min_bank_balance_cents=min(row['balance_cents'] for row in calc['daily']),needs_review=True,needs_fiscal_ack=bool(state.get('fiscal_review')),flags=[str(error)],rule_note='Condição protegida ou realizada exige revisão antes de comparar alternativas. Cobertura após janeiro incompleta.')
                        cache={key:assessments};self.server.workflow_assessments=cache
                    return self.send(dict(scenario=meta,state=state,calculation=calc,payment_review_details=self.store.payment_review_details(meta['id'],calc['payment_reviews']),assessments=cache[key],planning=dict(weeks=periods(calc),months=periods(calc,'month')),last_change=self.store.latest_change(meta['id'])))
                if path=='/api/report':
                    meta,state=self.store.load(q.get('id','base'));r=report(state,meta,q);fmt=q.get('format','json')
                    if fmt not in ['json','csv','html']:raise DomainError('Formato inválido.')
                    return self.send(r if fmt=='json' else report_document(r,fmt),kind='application/json; charset=utf-8' if fmt=='json' else 'text/csv; charset=utf-8' if fmt=='csv' else 'text/html; charset=utf-8')
                if path=='/api/export':return self.export(q)
                return self.send(dict(error='Rota não encontrada.'),404)
            if path=='/api/preview-move':
                meta,state=self.store.load(data.get('id','base'))
                if meta['version']!=data.get('expectedVersion'):raise Conflict('O cenário mudou. Recarregue antes de mover.')
                candidate,event=move_event(state,data['eventKey'],data['date'])
                return self.send(dict(impact=impact(state,candidate),previous_date=event['date'],acceptance_reset=event['stage']=='decided' and bool(event.get('creditor_id')),tax_review=event['kind']=='tax'))
            if path=='/api/preview-adjustment':
                meta,state=self.store.load(data.get('id','base'))
                if meta['version']!=data.get('expectedVersion'):raise Conflict('O cenário mudou. Recarregue antes de corrigir.')
                candidate=adjust_forecast(state,data)
                return self.send(dict(impact=impact(state,candidate)))
            if path=='/api/preview-title':
                meta,state=self.store.load(data.get('id','base'))
                if meta['version']!=data.get('expectedVersion'):raise Conflict('O cenário mudou. Recarregue antes de simular.')
                candidate=copy.deepcopy(state);apply_title(candidate,data)
                return self.send(dict(impact=impact(state,candidate),decision_reset=candidate.get('planning',{}).get('T:'+data['titleId'],{}).get('needs_reconfirmation',False)))
            if path=='/api/simulate':
                meta,state=self.store.load(data.get('id','base'))
                if meta['version']!=data.get('expectedVersion'):raise Conflict('O cenário mudou. Recarregue antes de simular.')
                cid=data['creditorId'];change=data.get('proposal',{});candidate,calc=simulate(state,cid,change);choices=options(state,cid,change)
                return self.send(dict(calculation=calc,options=choices,assessment=assess(state,cid,change,calculation=calc,choices=choices),before=calculate(state)['minimum'],impact=impact(state,candidate)))
            if path=='/api/requests':
                self.role(user,'admin','editor')
                return self.send(self.store.create_request(data.get('id','base'),data.get('expectedVersion'),user,data['creditorId'],data.get('proposal',{}),data.get('evidence'),data.get('reason')))
            if path=='/api/assignment':
                self.role(user,'admin')
                return self.send(self.store.assign(data.get('id','base'),data.get('expectedVersion'),user,data['creditorId'],text(data.get('userId')),data.get('reason')))
            if path=='/api/request-review':
                self.role(user,'admin')
                return self.send(self.store.review_request(data.get('id','base'),data.get('expectedVersion'),user,data['requestId'],data.get('decision'),data.get('decisionNote'),data.get('acknowledgeRisk',False)))
            if path=='/api/negotiation-confirm':
                self.role(user,'admin','editor')
                def confirm(s):
                    candidate,assessment=confirm_negotiation(s,user,data['creditorId'],data.get('proposal',{}),data.get('evidence'),data.get('reason'),data.get('acknowledgeRisk',False))
                    s.update(candidate);return assessment
                assessment=self.store.mutate(data.get('id','base'),data.get('expectedVersion'),user,'negotiation-confirm',text(data.get('reason')),confirm)
                return self.send(dict(ok=True,assessment=assessment))
            self.role(user,*(['admin','editor'] if path in ['/api/response','/api/creditor-contact'] else ['admin']))
            if path=='/api/scenarios':
                new=self.store.clone(data.get('id','base'),text(data.get('name')),user);return self.send(dict(id=new))
            if path=='/api/users':self.role(user,'admin');self.store.add_user(data);return self.send(dict(ok=True))
            if path=='/api/backup':self.role(user,'admin');return self.send(dict(filename=self.store.backup()))
            sid=data.get('id','base');expected=data.get('expectedVersion');reason=text(data.get('reason'));action=path.rsplit('/',1)[-1]
            def update(s):
                if path=='/api/proposal':
                    cid=data['creditorId']
                    if any(p.get('creditor_id')==cid for p in s['payments']):raise DomainError('Há pagamentos realizados neste plano. Preserve o acordo e cadastre uma nova obrigação para ajustes documentados.')
                    candidate,calc=simulate(s,cid,data['proposal']);s.update(candidate)
                elif path=='/api/response':
                    cid=data['creditorId'];status=data['status']
                    if status not in ['accepted','rejected','negotiating']:raise DomainError('Retorno inválido.')
                    if user['role']=='editor' and (status=='accepted' or s.get('assignments',{}).get(cid,{}).get('user_id')!=user['id']):raise Forbidden('Use a confirmação da negociação atribuída ou solicite análise da gestora.')
                    evidence=text(data.get('evidence'))
                    if len(evidence)<5:raise DomainError('Registre a referência do retorno do fornecedor.')
                    if status=='accepted':
                        candidate,_=approve(s,cid,{},evidence,reason,data.get('acknowledgeRisk',False));s.update(candidate)
                    else:s['proposals'][cid].update(status=status,evidence=evidence)
                elif path=='/api/creditor-contact':
                    cid=data['creditorId'];c=creditor(s,cid)
                    if user['role']=='editor' and s.get('assignments',{}).get(cid,{}).get('user_id')!=user['id']:raise Forbidden('Este fornecedor não está atribuído a você.')
                    contacts={}
                    for key,maximum,label in [('trade_name',150,'Nome fantasia'),('negotiation_contact',150,'Contato da negociação'),('negotiation_phone',50,'Telefone')]:
                        if key not in data:continue
                        value=data[key]
                        if not isinstance(value,str) or len(value)>maximum:raise DomainError(f'{label} deve ser um texto de até {maximum} caracteres.')
                        contacts[key]=value
                    c.update(contacts)
                elif path=='/api/constraint':
                    self.role(user,'admin');cid=data['creditorId'];n=data['max_payments'];evidence=text(data.get('evidence'))
                    if not isinstance(n,int) or isinstance(n,bool) or n<1 or n>36 or len(evidence)<5:raise DomainError('Informe o limite e a referência da nova negociação.')
                    c=creditor(s,cid);c.update(max_payments=n,constraint_source=evidence,risk=text(data.get('risk',c['risk'])) or 'Não informado',priority=text(data.get('priority',c['priority'])) or 'Normal')
                    s['proposals'][cid]['status']='draft'
                elif path=='/api/move':
                    candidate,event=move_event(s,data['eventKey'],data['date']);s.update(candidate)
                elif path=='/api/adjustment':s.update(adjust_forecast(s,data))
                elif path=='/api/title':apply_title(s,data)
                elif path=='/api/planning':
                    key=data.get('planningKey');flex=data.get('flexibility');stage=data.get('stage','preplanned');evidence=text(data.get('evidence'))
                    event=next((e for e in calculate(s)['events']+calculate(s)['undated'] if e.get('planning_key')==key),None)
                    if not event or not key:raise DomainError('Compromisso não encontrado.')
                    if flex not in ['fixed','negotiable','unclassified'] or stage not in ['preplanned','programmed','decided']:raise DomainError('Classificação inválida.')
                    if key.startswith('C:') and stage=='decided':raise DomainError('Use o retorno do fornecedor para registrar aceite.')
                    if stage=='decided':self.role(user,'admin')
                    if len(evidence)<5:raise DomainError('Registre a referência ou motivo da classificação.')
                    s.setdefault('planning',{})[key]=dict(flexibility=flex,stage=stage,evidence=evidence,actor=user['name'])
                elif path=='/api/group':
                    tids=data.get('titleIds',[])
                    selected=[t for t in s['titles'] if t['id'] in tids]
                    if not selected or len(selected)!=len(set(tids)):raise DomainError('Selecione obrigações existentes.')
                    if any(s['overrides'].get(t['id'],{}).get('cancelled') for t in selected):raise DomainError('Reative a previsão antes de incluí-la em um plano.')
                    if any(s.get('planning',{}).get('T:'+t['id'],{}).get('flexibility')=='fixed' for t in selected):raise DomainError('Revise a condição protegida antes de transformar a obrigação em outro plano.')
                    if any(t['remaining_signed_cents']>=0 or t.get('proposed_agreement_id') or is_tax(t) for t in selected):raise DomainError('Selecione apenas dívidas comerciais ainda fora de um plano.')
                    if len({t['party'] for t in selected})!=1:raise DomainError('Confirme a identidade antes de agrupar pessoas diferentes.')
                    if any(p['event_key'] in ['T:'+t['id'] for t in selected] for p in s['payments']):raise DomainError('Há liquidações vinculadas. Preserve a referência e negocie apenas novas obrigações.')
                    cid='C-'+uid()[:8];selected_events=[e for e in calculate(s)['events']+calculate(s)['undated'] if e.get('title_id') in tids];amount=sum(e['amount_cents'] for e in selected_events)
                    grouped_components={key:sum(e.get(key,0) for e in selected_events) for key in ['base_cents','interest_cents','penalty_cents','discount_cents']}
                    s['creditors'].append(dict(id=cid,name=selected[0]['party'],debt_cents=amount,max_payments=8,risk='Não informado',priority='Normal',constraint_source='Plano criado pela equipe',source_rows=', '.join(t['id'] for t in selected),historical_total_payments=None))
                    s['proposals'][cid]=dict(n=1,entry_cents=amount,entry_date=s['start'],first_date=s['start'],interval='monthly',include_reimbursements=False,status='draft',notes=reason,evidence='',**grouped_components)
                    for t in selected:t['proposed_agreement_id']=cid
                elif path=='/api/entry':
                    party=text(data.get('party'));kind=data.get('kind');amount=money(data.get('amount_cents'));d=day(data['date']) if data.get('date') else None
                    if not party or amount<=0 or kind not in ['receipt','obligation','creditor']:raise DomainError('Informe pessoa, tipo e valor positivo.')
                    if d and d<s['start']:raise DomainError('Use uma data posterior ao corte.')
                    tid='NOVO-'+uid()[:12];cid=None
                    if kind=='creditor':
                        cid='C-'+uid()[:8];s['creditors'].append(dict(id=cid,name=party,debt_cents=amount,max_payments=8,risk='Não informado',priority='Normal',constraint_source='Cadastro gerencial',source_rows='Novo cadastro',historical_total_payments=None))
                        s['proposals'][cid]=dict(n=1,entry_cents=amount,entry_date=d or s['start'],first_date=d or s['start'],interval='monthly',include_reimbursements=False,status='draft',notes=reason,evidence='')
                    s['titles'].append(dict(id=tid,type='Receber' if kind=='receipt' else 'Pagar',party=party,original_due_date=d,original_signed_cents=amount if kind=='receipt' else -amount,remaining_signed_cents=amount if kind=='receipt' else -amount,settled_signed_cents=0,description=text(data.get('description')),document=text(data.get('document')),account_plan='Cadastro manual',origin='Cadastro manual',cash_date=d,scenario_date=d,proposed_agreement_id=cid,original_source='Cadastro por '+user['name']))
                    if kind=='receipt':s['fiscal_review'].append(tid)
                elif path=='/api/policies':
                    self.role(user,'admin')
                    for key,value in data['policies'].items():
                        if key not in s['policies']:raise DomainError('Parâmetro inválido.')
                        if key.endswith('_cents'):
                            money(value)
                            if value<0:raise DomainError('Reserva e piso não podem ser negativos.')
                        else:day(value)
                        s['policies'][key]=value
                elif path=='/api/issues':
                    issue=next((i for i in s['issues'] if i['id']==data['issueId']),None)
                    if not issue:raise DomainError('Pendência não encontrada.')
                    statuses=['Aberta','Em análise','Resolvida documentalmente','Resolvida financeiramente','Estimativa autorizada','Descartada com motivo']
                    if data['status'] not in statuses:raise DomainError('Situação inválida.')
                    issue.update(status=data['status'],resolution_note=reason)
                elif path=='/api/reconciliation':
                    self.role(user,'admin');t=next(t for t in s['titles'] if t['id']=='TIT-002')
                    if data.get('decision') not in ['confirmed','rejected']:raise DomainError('Decisão inválida.')
                    if len(text(data.get('evidence')))<5:raise DomainError('Informe a referência documental da conciliação.')
                    t.update(remaining_signed_cents=0 if data['decision']=='confirmed' else -1500000,reconciliation_decision=data['decision'],reconciliation_evidence=text(data['evidence']))
                elif path=='/api/liquidation':
                    calc=calculate(s);key=data['eventKey'];event=next((e for e in calc['events']+calc['undated'] if e['key']==key and e['status']!='actual'),None)
                    if not event:raise DomainError('Compromisso não encontrado ou já liquidado.')
                    amount=money(data['amount_cents']);d=day(data['date']);evidence=text(data.get('evidence'))
                    bank=data.get('bank')
                    if bank not in (None,'','Itaú','Cora'):raise DomainError('Escolha Itaú ou Cora para o banco do comprovante.')
                    if d>datetime.now(timezone(timedelta(hours=-3))).date().isoformat():raise DomainError('Um movimento realizado não pode ter data futura. Use planejamento para datas futuras.')
                    if amount<=0 or amount>event['amount_cents'] or d<s['start'] or len(evidence)<5:raise DomainError('Confira valor, data posterior ao corte e referência do comprovante.')
                    if event.get('creditor_id') and s['proposals'][event['creditor_id']]['status']!='accepted':raise DomainError('Registre o aceite antes de liquidar uma parcela do plano.')
                    confirmed=data.get('composition_confirmed') is True
                    composition=paid_components(event,amount) if confirmed else {}
                    s['payments'].append(dict(**({'bank':bank} if bank else {}),components_known=confirmed,**{k:v for k,v in composition.items() if k!='amount_cents'},id=uid(),event_key=key,creditor_id=event.get('creditor_id'),party=event['party'],direction=event['direction'],source_title_ids=[t['id'] for t in s['titles'] if t.get('proposed_agreement_id')==event.get('creditor_id') and event.get('creditor_id')],amount_cents=amount,date=d,evidence=evidence,actor=user['name']))
                else:raise DomainError('Ação não encontrada.')
                calculate(s)
            self.store.mutate(sid,expected,user,action,reason,update);return self.send(dict(ok=True))
        except Unauthorized as e:self.send(dict(error=str(e)),401)
        except (Forbidden,PermissionError) as e:self.send(dict(error=str(e)),403)
        except Conflict as e:self.send(dict(error=str(e),conflict=True),409)
        except (DomainError,KeyError,ValueError,TypeError) as e:self.send(dict(error=str(e) if isinstance(e,DomainError) else 'Dados incompletos ou inválidos. Confira os campos.'),422)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except Exception:
            traceback.print_exc();self.send(dict(error='Não foi possível concluir. Nenhuma confirmação deve ser presumida; recarregue e confira o histórico.'),500)
    def export(self,q):
        _,s=self.store.load(q.get('id','base'));cid=q['creditorId'];c=creditor(s,cid);p=s['proposals'][cid];rows=plan(s,cid,p);esc=html.escape
        from engine import proposal_components
        composition=proposal_components(s,cid,p)
        status={'draft':'Rascunho — não aceito','accepted':'Aceite informado pela equipe','rejected':'Proposta recusada','negotiating':'Em negociação'}.get(p['status'],'Proposta')
        body=''.join('<tr><td>'+esc(e['label'])+'</td><td>'+esc(e['date'])+'</td><td>'+brl(e['amount_cents'])+'</td></tr>' for e in rows)
        cost_line='<p>Base registrada: '+brl(composition['base_cents'])+'; juros discriminados: '+brl(composition['interest_cents'])+'; multa: '+brl(composition['penalty_cents'])+'; desconto: '+brl(composition['discount_cents'])+'.</p>'
        doc='<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Proposta Tokyo</title><style>body{font:16px Arial;color:#182524;max-width:780px;margin:48px auto;padding:24px}h1{font-size:26px}table{width:100%;border-collapse:collapse;margin:24px 0}td,th{padding:14px;text-align:left;border-bottom:1px solid #ddd}p{line-height:1.6}@media print{body{margin:0}}</style><h1>Tokyo — proposta de pagamento</h1><h2>'+esc(c['name'])+'</h2><p>'+esc(status)+'</p><p>Total da proposta: <strong>'+brl(sum(e['amount_cents'] for e in rows))+'</strong>. '+str(len(rows))+' pagamentos totais, incluindo a entrada.</p>'+cost_line+'<table><thead><tr><th>Pagamento</th><th>Data</th><th>Valor</th></tr></thead><tbody>'+body+'</tbody></table><p>'+esc(p.get('notes',''))+'</p><p>Condições sujeitas à confirmação das partes. Este documento não executa pagamentos nem comprova quitação.</p></html>'
        return self.send(doc,kind='text/html; charset=utf-8')

def create_server(port=None,db=None,*,config=None,host=None,public_url=None,production=None):
    settings=config if config is not None else load_config(port=port,db=db,host=host,public_url=public_url,production=production)
    if settings.production:validate_production_database(settings.db_path)
    server=ThreadingHTTPServer((settings.host,settings.port),Handler)
    try:
        server.store=Store(ROOT,settings.db_path);server.production=settings.production
        actual_port=server.server_address[1];server.allowed_hosts=settings.allowed_hosts(actual_port)
        server.allowed_origins=settings.allowed_origins(actual_port);server.attempts={};server.hosting_config=settings
        return server
    except BaseException:
        server.server_close();raise

def serve(port=None,db=None,*,config=None,host=None,public_url=None,production=None):
    server=create_server(port,db,config=config,host=host,public_url=public_url,production=production)
    address=server.hosting_config.public_origin or f'http://{server.hosting_config.host}:{server.server_address[1]}'
    print(f'Tokyo disponível em {address}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--port',type=int);p.add_argument('--db');p.add_argument('--host');p.add_argument('--public-url')
    p.add_argument('--production',action='store_const',const=True,default=None)
    args=p.parse_args(argv)
    try:serve(args.port,args.db,host=args.host,public_url=args.public_url,production=args.production)
    except HostingError as error:p.error(str(error))

if __name__=='__main__':main()
