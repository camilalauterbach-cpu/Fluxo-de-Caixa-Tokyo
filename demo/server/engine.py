from __future__ import annotations
import calendar,copy,hashlib,json
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from costs import components,split_components,paid_components,remaining_components
START='2026-10-20';END='2027-01-31';CUTOFF='2026-10-19'
class DomainError(ValueError): pass

def money(v):
    if isinstance(v,bool) or not isinstance(v,int) or abs(v)>10**14: raise DomainError('Valor monetário inválido.')
    return v

def day(v):
    try: return date.fromisoformat(v).isoformat()
    except (ValueError,TypeError): raise DomainError('Data inválida.')

def rounded(v): return int(Decimal(str(v)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def month(d,n):
    d=date.fromisoformat(d);m=d.month-1+n;y=d.year+m//12;m=m%12+1
    return date(y,m,min(d.day,calendar.monthrange(y,m)[1])).isoformat()

def split(total,n,entry):
    money(total);money(entry)
    if isinstance(n,bool) or not isinstance(n,int) or n<1 or n>36: raise DomainError('Escolha entre 1 e 36 pagamentos totais.')
    if total<=0 or entry<0 or entry>total: raise DomainError('A entrada deve estar entre zero e o saldo.')
    if n==1:return [total]
    if entry in (0,total):raise DomainError('Para vários pagamentos, informe uma entrada maior que zero e menor que o saldo.')
    p=rounded(Decimal(total-entry)/(n-1));a=[entry]+[p]*(n-2)+[total-entry-p*(n-2)]
    if min(a)<=0:raise DomainError('Reduza a quantidade de pagamentos para evitar parcelas zeradas.')
    return a

def seed(root):
    def load(n):return json.loads((root/'data'/n).read_text(encoding='utf-8'))
    creditors=load('credores_snapshot_com_restricoes.json');proposals={}
    for c in creditors:
        c.update(max_payments=c['current_max_payments'],risk='Não informado',priority='Normal')
        proposals[c['id']]=dict(n=min(c['historical_total_payments'],c['max_payments']),entry_cents=c['historical_down_payment_cents'],entry_date=c['historical_down_payment_date'],first_date=c['historical_first_balance_date'] or c['historical_down_payment_date'],interval='monthly',status='draft',include_reimbursements=False,notes='',evidence='')
    return dict(schema=1,name='Demonstração — dados fictícios',creditors=creditors,titles=load('titulos_snapshot.json'),proposals=proposals,issues=load('pendencias_snapshot.json'),overrides={},payments=[],policies=dict(floor_cents=100000,tax_reserve_cents=0,tax_reserve_from='2026-11-05',operating_reserve_cents=0,operating_reserve_from='2027-01-01'),opening_cash_cents=5000000,cutoff=CUTOFF,start=START,end=END,fiscal_review=[])

def is_tax(t):
    s=' '.join(str(t.get(k,'')) for k in ['party','description','account_plan','origin']).upper()
    return any(x in s for x in ['IRPJ','CSLL','COFINS','PIS ','PIS/','DARF','FGTS','RECEITA FEDERAL','ISS '])

def reimbursements(s):
    return [t for t in s['titles'] if t['type']=='Pagar' and t['remaining_signed_cents']<0 and ('EDER' in t['party'].upper() or 'J.E MONTAGEM' in t['party'].upper()) and not t.get('proposed_agreement_id') and abs(t['remaining_signed_cents']) in [62686,8436]]

def creditor(s,cid):
    c=next((c for c in s['creditors'] if c['id']==cid),None)
    if not c:raise DomainError('Fornecedor não encontrado.')
    return c

def proposal_components(s,cid,p):
    extra=sum(-t['remaining_signed_cents'] for t in reimbursements(s)) if cid=='C04' and p.get('include_reimbursements') else 0
    try:return components(p.get('base_cents',creditor(s,cid)['debt_cents'])+extra,p.get('interest_cents',0),p.get('penalty_cents',0),p.get('discount_cents',0))
    except ValueError as exc:raise DomainError(str(exc))
def total(s,cid,p):return proposal_components(s,cid,p)['amount_cents']

def plan(s,cid,p):
    vals=split(total(s,cid,p),p['n'],p['entry_cents']);d0=day(p['entry_date']);d1=day(p['first_date'])
    if d0<s['start'] or d1<s['start']:raise DomainError('Use datas posteriores ao corte bancário.')
    if len(vals)>1 and d1<=d0:raise DomainError('O saldo deve começar depois da entrada.')
    interval=p.get('interval','monthly')
    if interval not in ['monthly','weekly','fortnightly']:raise DomainError('Periodicidade inválida.')
    dates=[d0]+[month(d1,i) if interval=='monthly' else (date.fromisoformat(d1)+timedelta(days=(7 if interval=='weekly' else 14)*i)).isoformat() for i in range(len(vals)-1)]
    manual=p.get('payment_dates',{})
    if not isinstance(manual,dict) or any(str(k) not in {str(i+1) for i in range(len(vals))} for k in manual):raise DomainError('Datas específicas inválidas para este cronograma.')
    dates=[day(manual.get(str(i+1),d)) for i,d in enumerate(dates)]
    if any(d<s['start'] for d in dates) or any(a>=b for a,b in zip(dates,dates[1:])):raise DomainError('Mantenha as parcelas em ordem, com datas diferentes e posteriores ao corte.')
    c=creditor(s,cid)
    composition=split_components(proposal_components(s,cid,p),vals)
    return [dict(key=f'P:{cid}:{i+1}',creditor_id=cid,party=c['name'],date=d,amount_cents=v,direction='out',kind='proposal',label='Pagamento único' if len(vals)==1 else ('Entrada' if i==0 else f'Parcela {i}'),sequence=i+1,status=p['status'],legal_due_date=None,components_known=True,**{k:v for k,v in composition[i].items() if k!='amount_cents'}) for i,(v,d) in enumerate(zip(vals,dates))]

def _proposal_has_payments(s,cid):
    keys={e['key'] for e in plan(s,cid,s['proposals'][cid])}
    if cid=='C04' and s['proposals'][cid].get('include_reimbursements'):
        keys.update('T:'+t['id'] for t in reimbursements(s))
    return any(pay.get('creditor_id')==cid or pay.get('event_key') in keys or str(pay.get('event_key','')).startswith('P:'+cid+':') for pay in s.get('payments',[]))


def _apply_payments(s,events,undated,voided,merged):
    """Apply immutable facts by reference before any alternate-scenario fallback.

    Unknown compositions are allocated sequentially only for the forecast.
    Their actual records remain unknown. Global facts can exceed an older
    scenario: retain the complete actual and report the incompatibility rather
    than making an old scenario unreadable or producing a negative forecast.
    """
    by_key={e['key']:e for e in events+undated+voided}
    actuals=[];reviews=[]
    for event in by_key.values():event['paid_cents']=0

    def warn(pay,code,message,cid,targets,amount=0):
        item=dict(code=code,payment_id=pay['id'],event_key=pay['event_key'],creditor_id=cid,message=message,amount_cents=amount,related_keys=[e['key'] for e in targets])
        reviews.append(item)
        for event in targets:
            event['needs_review']=True
            messages=event.setdefault('payment_review_messages',[])
            if message not in messages:messages.append(message)

    for pay in s.get('payments',[]):
        key=pay['event_key'];cid=pay.get('creditor_id')
        if not cid and key in {'T:'+tid for tid in merged}:cid='C04'
        exact=by_key.get(key)
        schedule=sorted([e for e in events if cid and e.get('creditor_id')==cid],key=lambda e:e['sequence'])
        sources=[by_key['T:'+tid] for tid in pay.get('source_title_ids',[]) if 'T:'+tid in by_key]
        reimburse=[by_key['T:'+t['id']] for t in reimbursements(s) if 'T:'+t['id'] in by_key] if cid=='C04' else []
        if exact:
            targets=[exact]
            # Historical C04 facts can describe one aggregate remittance for
            # the plan plus reimbursements. Preserve this existing perimeter,
            # while explicitly flagging its incompatible installment key.
            if cid=='C04' and pay['amount_cents']>exact['amount_cents']:
                targets += [e for e in schedule+reimburse if e is not exact]
            if pay['amount_cents']>exact['amount_cents']:
                warn(pay,'payment_exceeds_event','O fato realizado supera o saldo desta parcela no cenário. Confira sua referência e o perímetro liquidado.',cid,targets,pay['amount_cents']-exact['amount_cents'])
        else:
            targets=schedule+reimburse if schedule else sources
            # Global title payments from an alternate unmerged C04 scenario.
            if not targets and cid=='C04':targets=reimburse
            if targets:warn(pay,'payment_reassigned','A referência original não existe neste cenário; a baixa foi alocada ao mesmo credor ou aos títulos de origem. Confira a compatibilidade.',cid,targets,pay['amount_cents'])
        left=pay['amount_cents'];known=None
        if pay.get('components_known'):
            try:known=remaining_components(pay,0)
            except ValueError:
                warn(pay,'payment_components_incompatible','A composição do fato não é compatível com os campos monetários registrados. O fato integral foi preservado para revisão.',cid,targets,pay['amount_cents'])
        # Keep persisted registration order, including unknown/known partials.
        # Re-rounding the cumulative paid amount would rewrite known parts.
        for event in targets:
            if not left:break
            amount=min(left,event['amount_cents'])
            if not amount:continue
            part=paid_components(known,amount) if known is not None else amount
            try:remaining=remaining_components(event,part)
            except ValueError:
                warn(pay,'payment_components_incompatible','Os componentes pagos não cabem na composição deste cenário. O realizado foi preservado e o saldo financeiro foi limitado sem alterar o fato.',cid,[event],amount)
                remaining=remaining_components(event,amount)
            event.update(remaining);event['paid_cents']+=amount
            left-=amount
            if known is not None:known=remaining_components(known,part)
        if left:
            warn(pay,'payment_exceeds_forecast','Há valor realizado sem saldo compatível neste cenário. O fato permanece integral; revise o plano e a vinculação.',cid,targets,left)
        pay_reviews=[item for item in reviews if item['payment_id']==pay['id']]
        actuals.append(dict(key='A:'+pay['id'],creditor_id=pay.get('creditor_id'),party=pay['party'],date=pay['date'],amount_cents=pay['amount_cents'],direction=pay['direction'],status='actual',kind='actual',label='Movimento realizado com referência documental',legal_due_date=None,original_key=key,components_known=pay.get('components_known',False),needs_review=bool(pay_reviews),payment_review_messages=[item['message'] for item in pay_reviews],**{k:pay.get(k) for k in ['base_cents','interest_cents','penalty_cents','discount_cents','other_cost_cents']}))
    return actuals,reviews


def calculate(s):
    events=[];undated=[];voided=[];violations=[]
    merged={t['id'] for t in reimbursements(s)} if s['proposals'].get('C04',{}).get('include_reimbursements') else set()
    for t in s['titles']:
        v=t['remaining_signed_cents']
        if not v or t['id'] in merged or t.get('proposed_agreement_id') in s['proposals']:continue
        o=s['overrides'].get(t['id'],{});d=o.get('date',t.get('scenario_date') or t.get('cash_date'))
        e=dict(key='T:'+t['id'],title_id=t['id'],party=t['party'],date=d,**components(o.get('base_cents',abs(v)),o.get('interest_cents',0),o.get('penalty_cents',0),o.get('discount_cents',0),o.get('extra_cost_cents',0)),components_known=True,direction='in' if v>0 else 'out',kind='receipt' if v>0 else ('tax' if is_tax(t) else 'obligation'),label=t.get('description',''),status='forecast',legal_due_date=t.get('original_due_date'),source=t.get('original_source',''),document=t.get('document',''),cost_pending=bool(o.get('cost_pending',is_tax(t) and abs(v) in [9374068,3451725])),allocation_id=o.get('allocation_id'))
        if o.get('cancelled'):
            e.update(status='cancelled');voided.append(e);continue
        if not d or d<s['start']:e.update(original_cash_date=d,date=None);undated.append(e)
        else:events.append(e)
    summaries=[]
    for c in s['creditors']:
        p=s['proposals'][c['id']];schedule=plan(s,c['id'],p);flags=[]
        if p['n']>c['max_payments']:flags.append(f"Limite vigente de {c['max_payments']} pagamentos, incluindo entrada.")
        if c['id']=='C04':
            extra_dates={s['overrides'].get(t['id'],{}).get('date',t.get('scenario_date') or t.get('cash_date')) for t in reimbursements(s)}-{None}
            if p.get('include_reimbursements'):
                if extra_dates and schedule[0]['date']>min(extra_dates):flags.append('A primeira remessa adia os reembolsos. Registre nova condição antes do aceite.')
            elif len({e['date'] for e in schedule}|extra_dates)>c['max_payments']:flags.append('Os R$ 711,22 de reembolsos criam uma terceira data. Inclua e coordene as remessas antes do aceite.')
        if flags:violations.append(dict(creditor_id=c['id'],name=c['name'],messages=flags))
        events.extend(schedule);summaries.append(dict(**c,proposal=p,schedule=schedule,flags=flags,total_cents=total(s,c['id'],p),reimbursements_cents=sum(-t['remaining_signed_cents'] for t in reimbursements(s)) if c['id']=='C04' else 0))
    actuals,payment_reviews=_apply_payments(s,events,undated,voided,merged)
    for summary in summaries:
        schedule_paid=sum(e['paid_cents'] for e in summary['schedule'])
        related=[r for r in payment_reviews if r['creditor_id']==summary['id']]
        summary.update(paid_cents=schedule_paid,remaining_cents=max(0,summary['total_cents']-schedule_paid),fully_paid=summary['total_cents']==schedule_paid,needs_review=bool(related),payment_review_messages=list(dict.fromkeys(r['message'] for r in related)))
        summary['flags'].extend(message for message in summary['payment_review_messages'] if message not in summary['flags'])
    events=[e for e in events if e['amount_cents']>0]+actuals;undated=[e for e in undated if e['amount_cents']>0];voided=[e for e in voided if e['amount_cents']>0]
    events.sort(key=lambda e:(e['date'],0 if e['direction']=='in' else 1,e['key']))
    receipts=[e for e in events if e['direction']=='in']
    for e in events:
        if e['direction']=='out':
            previous=[r for r in receipts if r['date']<=e['date']];e['suggested_receipt_id']=previous[-1]['key'] if previous else 'opening'
    daily=[];monthly={};balance=s['opening_cash_cents'];p=s['policies'];cursor=date.fromisoformat(s['start']);end=date.fromisoformat(s['end'])
    if end<cursor or (end-cursor).days>730:raise DomainError('Horizonte inválido; use até dois anos.')
    groups={}
    for e in events:groups.setdefault(e['date'],[]).append(e)
    while cursor<=end:
        d=cursor.isoformat();items=groups.get(d,[]);inc=sum(e['amount_cents'] for e in items if e['direction']=='in');out=sum(e['amount_cents'] for e in items if e['direction']=='out');before=balance;balance+=inc-out
        reserve=(p['tax_reserve_cents'] if d>=p['tax_reserve_from'] else 0)+(p['operating_reserve_cents'] if d>=p['operating_reserve_from'] else 0)
        row=dict(date=d,in_cents=inc,out_cents=out,balance_cents=balance,reserves_cents=reserve,margin_cents=balance-reserve-p['floor_cents'],before_cents=before,same_day_dependency=bool(inc and out>before));daily.append(row)
        m=monthly.setdefault(d[:7],dict(month=d[:7],in_cents=0,out_cents=0));m['in_cents']+=inc;m['out_cents']+=out;m.update(balance_cents=balance,reserves_cents=reserve,margin_cents=row['margin_cents']);cursor+=timedelta(days=1)
    minimum=min(daily,key=lambda r:r['margin_cents']);first=next((r for r in daily if r['margin_cents']<0),None);period=[e for e in events if s['start']<=e['date']<=s['end']];after=[e for e in events if e['date']>s['end']]
    from planning import event_meta
    for event in events+undated+voided:event.update(event_meta(s,event))
    return dict(payment_reviews=payment_reviews,needs_review=bool(payment_reviews),unallocated_payment_cents=sum(r['amount_cents'] for r in payment_reviews if r['code']=='payment_exceeds_forecast'),creditors=summaries,events=events,undated=undated,voided=voided,daily=daily,monthly=list(monthly.values()),violations=violations,minimum=minimum,first_shortfall=first,opening_cash_cents=s['opening_cash_cents'],end_balance_cents=balance,receipts_cents=sum(e['amount_cents'] for e in period if e['direction']=='in'),payments_cents=sum(e['amount_cents'] for e in period if e['direction']=='out'),undated_in_cents=sum(e['amount_cents'] for e in undated if e['direction']=='in'),undated_out_cents=sum(e['amount_cents'] for e in undated if e['direction']=='out'),tail_out_cents=sum(e['amount_cents'] for e in after if e['direction']=='out'),tail_in_cents=sum(e['amount_cents'] for e in after if e['direction']=='in'),tail_events=after,horizon_complete=False,calculation_hash=hashlib.sha256(json.dumps(s,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16])

def simulate(s,cid,change):
    if cid not in s['proposals']:raise DomainError('Plano não encontrado.')
    if set(change)-{'n','entry_cents','entry_date','first_date','interval','include_reimbursements','notes','payment_dates','base_cents','interest_cents','penalty_cents','discount_cents'}:raise DomainError('Campo de proposta inválido.')
    candidate=copy.deepcopy(s);candidate['proposals'][cid].update(change)
    original=s['proposals'][cid];proposed=candidate['proposals'][cid]
    perimeter_changed=bool(proposed.get('include_reimbursements'))!=bool(original.get('include_reimbursements'))
    if cid=='C04' and perimeter_changed:
        reimbursement_ids={t['id'] for t in reimbursements(s)}
        if any(s.get('planning',{}).get('T:'+tid,{}).get('flexibility')=='fixed' for tid in reimbursement_ids):
            raise DomainError('Há reembolso protegido. Revise a classificação com motivo antes de alterar sua inclusão no plano.')
        if proposed.get('include_reimbursements') and any(s['overrides'].get(tid,{}).get('cancelled') or any(k in s['overrides'].get(tid,{}) for k in ['base_cents','interest_cents','penalty_cents','discount_cents']) for tid in reimbursement_ids):
            raise DomainError('Há reembolso corrigido ou anulado no cenário. Preserve a composição e mantenha-o separado do plano.')
        if proposed.get('include_reimbursements') and any(not pay.get('creditor_id') and pay['event_key'] in {'T:'+tid for tid in reimbursement_ids} for pay in s.get('payments',[])):
            raise DomainError('Há reembolso com liquidação individual registrada. Preserve essa referência e mantenha o reembolso separado do plano.')
    old=[(e['date'],e['amount_cents']) for e in plan(s,cid,original)]
    new=[(e['date'],e['amount_cents']) for e in plan(candidate,cid,proposed)]
    composition_changed=proposal_components(s,cid,original)!=proposal_components(candidate,cid,proposed)
    if _proposal_has_payments(s,cid) and (old!=new or perimeter_changed or composition_changed):
        raise DomainError('Este plano já tem liquidações. Preserve as datas, valores e a composição paga; registre uma revisão documentada do remanescente.')
    if s.get('planning',{}).get('C:'+cid,{}).get('flexibility')=='fixed' and (old!=new or perimeter_changed or composition_changed):
        raise DomainError('Este plano está protegido. Revise a classificação com motivo antes de renegociar.')
    if old!=new or perimeter_changed or composition_changed or original.get('notes','')!=proposed.get('notes',''):
        proposed['status']='draft'
    return candidate,calculate(candidate)

def options(s,cid,change):
    choices=[]
    counts=[s['proposals'][cid]['n']] if _proposal_has_payments(s,cid) or s.get('planning',{}).get('C:'+cid,{}).get('flexibility')=='fixed' else range(1,creditor(s,cid)['max_payments']+1)
    for n in counts:
        trial=dict(change,n=n);base=dict(s['proposals'][cid],**change);amount=total(s,cid,base)
        if n>1 and (base['entry_cents']<=0 or base['entry_cents']>=amount):trial['entry_cents']=max(1,rounded(Decimal(amount)*Decimal('.05')))
        if n>1 and base['first_date']<=base['entry_date']:trial['first_date']=month(base['entry_date'],1)
        if n!=s['proposals'][cid]['n']:trial['payment_dates']={}
        _,r=simulate(s,cid,trial);c=next(c for c in r['creditors'] if c['id']==cid)
        choices.append(dict(n=n,minimum_margin_cents=r['minimum']['margin_cents'],minimum_date=r['minimum']['date'],first_shortfall=r['first_shortfall'],schedule=c['schedule'],flags=c['flags'],conditional_fit=r['minimum']['margin_cents']>=0 and not r['violations'] and not r.get('needs_review'),tail_out_cents=r['tail_out_cents']))
    return choices