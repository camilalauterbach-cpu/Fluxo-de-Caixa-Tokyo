"""Visão do planejamento: agregações exatas, classificação explícita e comparação."""
import copy,csv,html,io
from datetime import date,timedelta,datetime,timezone
from engine import calculate,day,money,is_tax,DomainError
STAGES={'decided':'Decidido','programmed':'Programado internamente','preplanned':'Em estudo','rejected':'Recusado — hipótese mantida','actual':'Realizado','cancelled':'Previsão anulada'}
FLEX={'fixed':'Sem margem de negociação','negotiable':'Pode negociar','unclassified':'Não classificado'}

def event_meta(s,e):
 key=('C:'+e['creditor_id']) if e.get('creditor_id') else ('T:'+e['title_id']) if e.get('title_id') else None
 if e['status']=='actual':return dict(planning_key=None,stage='actual',flexibility='fixed',planning_evidence='Realização registrada')
 m=s.get('planning',{}).get(key,{})
 stage='cancelled' if e['status']=='cancelled' else 'decided' if e['status']=='accepted' else 'rejected' if e['status']=='rejected' else m.get('stage','preplanned')
 return dict(planning_key=key,stage=stage,flexibility=m.get('flexibility','unclassified'),planning_evidence=s['proposals'][e['creditor_id']].get('evidence','') if e['status']=='accepted' and e.get('creditor_id') else m.get('evidence',''),needs_reconfirmation=m.get('needs_reconfirmation',False))

def periods(c,unit='week'):
 groups={}
 for row in c['daily']:
  d=date.fromisoformat(row['date'])
  key=row['date'][:7] if unit=='month' else (d-timedelta(days=d.weekday())).isoformat() if unit=='week' else row['date']
  g=groups.setdefault(key,dict(key=key,start=row['date'],end=row['date'],in_cents=0,out_cents=0,minimum_margin_cents=row['margin_cents'],minimum_date=row['date']))
  g['end']=row['date'];g['in_cents']+=row['in_cents'];g['out_cents']+=row['out_cents'];g['balance_cents']=row['balance_cents'];g['margin_cents']=row['margin_cents']
  if row['margin_cents']<g['minimum_margin_cents']:g.update(minimum_margin_cents=row['margin_cents'],minimum_date=row['date'])
 return list(groups.values())

def impact(before,after):
 a=calculate(before);b=calculate(after);rows=[]
 for unit in ['week','month']:
  old={r['key']:r for r in periods(a,unit)}
  for r in periods(b,unit):
   prev=old[r['key']];item=dict(r,unit=unit)
   for field in ['in_cents','out_cents','balance_cents','margin_cents','minimum_margin_cents']:item['delta_'+field]=r[field]-prev[field];item['before_'+field]=prev[field]
   if any(item['delta_'+k] for k in ['in_cents','out_cents','balance_cents','margin_cents','minimum_margin_cents']):rows.append(item)
 return dict(before_minimum=a['minimum'],after_minimum=b['minimum'],before_first_shortfall=a['first_shortfall'],after_first_shortfall=b['first_shortfall'],end_delta_cents=b['end_balance_cents']-a['end_balance_cents'],tail_delta_cents=b['tail_out_cents']-a['tail_out_cents'],tail_in_delta_cents=b['tail_in_cents']-a['tail_in_cents'],fiscal_review_added=len(set(after['fiscal_review'])-set(before['fiscal_review'])),periods=rows)

def apply_title(s,data):
 tid=data['titleId'];t=next((t for t in s['titles'] if t['id']==tid),None)
 if not t or t.get('proposed_agreement_id'):raise DomainError('Abra o plano do fornecedor para alterar estas parcelas.')
 d=day(data['date'])
 if d<s['start']:raise DomainError('Use uma data posterior ao corte.')
 o=s['overrides'].get(tid,{})
 previous_date=o.get('date',t.get('scenario_date') or t.get('cash_date'));previous_cost=o.get('extra_cost_cents',0)
 if s.get('planning',{}).get('T:'+tid,{}).get('flexibility')=='fixed' and (d!=o.get('date',t.get('scenario_date') or t.get('cash_date')) or data.get('extra_cost_cents') not in [None,o.get('extra_cost_cents',0)] or data.get('extra_cost_cents') is None and o.get('extra_cost_cents',0)>0):raise DomainError('Este compromisso está protegido. Revise a classificação com motivo antes de alterar data ou valor.')
 o=s['overrides'].setdefault(tid,{})
 if t['type']=='Receber' and d[:7]!=(o.get('date',t.get('scenario_date') or t.get('cash_date')) or '')[:7]:
  if tid not in s['fiscal_review']:s['fiscal_review'].append(tid)
 o['date']=d
 if is_tax(t) and d>(t.get('original_due_date') or d):
  extra=data.get('extra_cost_cents');o['cost_pending']=extra is None
  if extra is not None:
   money(extra)
   if extra<0 or not str(data.get('cost_evidence','')).strip():raise DomainError('Informe os encargos não negativos e a fonte do cálculo fiscal.')
   o.update(extra_cost_cents=extra,cost_evidence=str(data['cost_evidence'])[:2000])
  else:o.pop('extra_cost_cents',None)
 if 'allocation_id' in data:
  aid=data['allocation_id'];c=calculate(s)
  if aid!='opening' and not any(e['key']==aid and e['direction']=='in' for e in c['events']):raise DomainError('Recebimento vinculado não encontrado.')
  o['allocation_id']=aid
 if d!=previous_date or o.get('extra_cost_cents',0)!=previous_cost:
  decision=s.get('planning',{}).get('T:'+tid,{})
  if decision.get('stage')=='decided':
   decision['previous_decision']=dict(evidence=decision.get('evidence',''),actor=decision.get('actor',''),date=previous_date)
   decision.update(stage='preplanned',evidence='',needs_reconfirmation=True)
 return s

def report(s,meta,q):
 start=q.get('from') or s['start'];end=q.get('to') or s['end'];stage=q.get('stage','all');scope=q.get('scope','period')
 day(start);day(end)
 if start>end:raise DomainError('A data final deve ser posterior à inicial.')
 if stage not in ['all',*STAGES] or scope not in ['period','undated','tail','voided']:raise DomainError('Recorte de relatório inválido.')
 c=calculate(s)
 source=c['voided'] if scope=='voided' else c['undated'] if scope=='undated' else c['tail_events'] if scope=='tail' else [e for e in c['events'] if start<=e['date']<=end]
 rows=[e for e in source if stage=='all' or e['stage']==stage]
 totals={direction:sum(e['amount_cents'] for e in rows if e['direction']==direction) for direction in ['in','out']}
 cost_totals=dict(forecast_interest_cents=sum((e.get('interest_cents') or 0) for e in rows if e['direction']=='out' and e['stage'] not in ['actual','cancelled']),paid_interest_cents=sum((e.get('interest_cents') or 0) for e in rows if e['direction']=='out' and e['stage']=='actual' and e.get('components_known')),paid_penalty_cents=sum((e.get('penalty_cents') or 0) for e in rows if e['direction']=='out' and e['stage']=='actual' and e.get('components_known')),unclassified_actuals=sum(e['direction']=='out' and e['stage']=='actual' and not e.get('components_known') for e in rows))
 return dict(cost_totals=cost_totals,scenario=meta,generated_at=datetime.now(timezone.utc).isoformat(),cutoff=s['cutoff'],from_date=start,to_date=end,scope=scope,stage=stage,rows=rows,totals=totals,calculation_hash=c['calculation_hash'],minimum=c['minimum'],end_balance_cents=c['end_balance_cents'],undated_in_cents=c['undated_in_cents'],undated_out_cents=c['undated_out_cents'],tail_out_cents=c['tail_out_cents'],fiscal_review_count=len(s['fiscal_review']),pending_cost_count=sum(bool(e.get('cost_pending')) for e in c['events']+c['undated']),stages={key:sum(e['amount_cents'] for e in rows if e['direction']=='out' and e['stage']==key) for key in STAGES})

def brl(v):return ('R$ '+f'{v/100:,.2f}').replace(',','x').replace('.',',').replace('x','.')
def report_document(r,fmt='html'):
 headers=['Data planejada','Vencimento original','Direção','Pessoa','Compromisso','Situação','Flexibilidade','Valor (R$)','Base registrada (R$)','Juros discriminados (R$)','Multa (R$)','Desconto (R$)','Outros acréscimos (R$)','Referência da decisão']
 def values(e):return [e.get('date') or 'Sem data',e.get('legal_due_date') or '', 'Entrada' if e['direction']=='in' else 'Saída',e['party'],e['label'],STAGES[e['stage']],FLEX[e['flexibility']],f"{e['amount_cents']//100},{e['amount_cents']%100:02d}",*[f"{(e.get(k) or 0)/100:.2f}".replace('.',',') if e.get('components_known') else 'Não informado' for k in ['base_cents','interest_cents','penalty_cents','discount_cents','other_cost_cents']],e.get('planning_evidence','')]
 if fmt=='csv':
  buf=io.StringIO();writer=csv.writer(buf,delimiter=';')
  def safe(v):
   v=str(v);return "'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v
  for row in [['Tokyo — relatório interno confidencial'],['Cenário',r['scenario']['name'],'Versão',r['scenario']['version']],['Corte',r['cutoff'],'Gerado em UTC',r['generated_at']],['Recorte',r['scope'],r['from_date'],r['to_date'],'Situação',r['stage']],['Entradas do recorte (centavos)',r['totals']['in'],'Saídas do recorte (centavos)',r['totals']['out']],['O recorte não representa saldo disponível. Previsões não comprovam recebimentos. Após janeiro a cobertura é incompleta.'],headers]+[values(e) for e in r['rows']]:writer.writerow([safe(v) for v in row])
  return '\ufeff'+buf.getvalue()
 esc=lambda v:html.escape(str(v));rows=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in values(e))+'</tr>' for e in r['rows'])
 return '<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>Tokyo — relatório interno</title><style>body{font:14px Segoe UI,Arial;color:#182524;max-width:1250px;margin:35px auto;padding:20px}h1{font-size:26px}p{line-height:1.6}table{border-collapse:collapse;width:100%;font-size:11px}th,td{padding:9px;text-align:left;border-bottom:1px solid #ddd;overflow-wrap:anywhere}.notice{background:#fff4e6;padding:12px}@media print{body{margin:0;padding:0}thead{display:table-header-group}tr{break-inside:avoid}@page{size:landscape;margin:12mm}}</style><h1>Tokyo — relatório interno confidencial</h1><p><b>'+esc(r['scenario']['name'])+'</b> · versão '+str(r['scenario']['version'])+' · corte '+esc(r['cutoff'])+'<br>Gerado em '+esc(r['generated_at'])+' · cálculo '+esc(r['calculation_hash'])+'<br>Recorte: '+esc(r['scope'])+' · '+esc(r['from_date'])+' a '+esc(r['to_date'])+' · situação: '+esc(STAGES.get(r['stage'],'Todas'))+'</p><p>Entradas no recorte: <b>'+brl(r['totals']['in'])+'</b> · Saídas no recorte: <b>'+brl(r['totals']['out'])+'</b></p><p class="notice">Este documento é interno e inclui outros credores. O recorte não representa saldo disponível. Previsão não comprova recebimento; aceite não comprova pagamento. Depois de janeiro há apenas compromissos conhecidos, sem cobertura completa. Encargos pendentes: '+str(r['pending_cost_count'])+'; revisões fiscais: '+str(r['fiscal_review_count'])+'.</p><p>Fora da programação: a pagar sem data '+brl(r['undated_out_cents'])+'; a receber sem data '+brl(r['undated_in_cents'])+'; saídas após o horizonte '+brl(r['tail_out_cents'])+'.</p><p>Juros discriminados previstos: '+brl(r['cost_totals']['forecast_interest_cents'])+'; juros pagos confirmados: '+brl(r['cost_totals']['paid_interest_cents'])+'; multas pagas confirmadas: '+brl(r['cost_totals']['paid_penalty_cents'])+'; pagamentos sem composição: '+str(r['cost_totals']['unclassified_actuals'])+'.</p><table><thead><tr>'+''.join('<th>'+esc(v)+'</th>' for v in headers)+'</tr></thead><tbody>'+rows+'</tbody></table><p>Use a impressão do navegador para salvar em PDF. Para enviar a um fornecedor, utilize a proposta individual.</p></html>'

def move_event(s,key,new_date):
 day(new_date);candidate=copy.deepcopy(s);c=calculate(s);event=next((e for e in c['events']+c['undated'] if e['key']==key),None)
 if not event or event['stage'] in ['actual','cancelled']:raise DomainError('Escolha um compromisso ainda não realizado.')
 if event['flexibility']=='fixed':raise DomainError('Compromisso protegido: revise a condição antes de mover.')
 cid=event.get('creditor_id')
 if cid:
  if any(p.get('creditor_id')==cid for p in s['payments']):raise DomainError('Este plano já tem liquidações. Preserve o cronograma e revise o ajuste documentado.')
  dates=dict(candidate['proposals'][cid].get('payment_dates',{}));dates[str(event['sequence'])]=new_date
  from engine import simulate
  candidate,_=simulate(candidate,cid,dict(payment_dates=dates))
 else:apply_title(candidate,dict(titleId=event['title_id'],date=new_date))
 calculate(candidate)
 return candidate,event
