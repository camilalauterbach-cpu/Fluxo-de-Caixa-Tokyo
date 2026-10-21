"""Correções e anulação de previsões no cenário; fontes e fatos permanecem intactos."""
import copy
from engine import calculate,money,DomainError
FIELDS=('base_cents','interest_cents','penalty_cents','discount_cents')
def adjust_forecast(s,data):
 tid=data['titleId'];title=next((t for t in s['titles'] if t['id']==tid),None)
 if not title or title.get('proposed_agreement_id'):raise DomainError('Abra o plano do fornecedor para corrigir o valor deste acordo.')
 existing=calculate(s)
 if not any(e.get('title_id')==tid for e in existing['events']+existing['undated']+existing['voided']):raise DomainError('Não há previsão separada em aberto. Preserve as realizações e revise o plano ao qual o título está vinculado.')
 candidate=copy.deepcopy(s);current=s['overrides'].get(tid,{})
 if s.get('planning',{}).get('T:'+tid,{}).get('flexibility')=='fixed':raise DomainError('Revise a condição protegida antes de corrigir ou anular esta previsão.')
 cancelled=data.get('cancelled',current.get('cancelled',False))
 if not isinstance(cancelled,bool):raise DomainError('A situação da previsão é inválida.')
 values={k:data.get(k,current.get(k,abs(title['remaining_signed_cents']) if k=='base_cents' else 0)) for k in FIELDS}
 for v in values.values():
  money(v)
  if v<0:raise DomainError('Base, juros, multa e desconto não podem ser negativos.')
 if values['discount_cents']>values['base_cents'] or values['base_cents']+values['interest_cents']+values['penalty_cents']-values['discount_cents']<=0:raise DomainError('Confira a base e o desconto. Para retirar todo o valor, use Anular previsão.')
 o=candidate['overrides'].setdefault(tid,{});o.update(values,cancelled=cancelled,financial_note=str(data.get('financial_note',''))[:2000])
 changed=cancelled!=current.get('cancelled',False) or any(values[k]!=current.get(k,abs(title['remaining_signed_cents']) if k=='base_cents' else 0) for k in FIELDS)
 if changed:
  decision=candidate.get('planning',{}).get('T:'+tid,{})
  if decision.get('stage')=='decided':
   decision['previous_decision']=dict(evidence=decision.get('evidence',''),actor=decision.get('actor',''))
   decision.update(stage='preplanned',evidence='',needs_reconfirmation=True)
  if title['type']=='Receber' and tid not in candidate['fiscal_review']:candidate['fiscal_review'].append(tid)
 result=calculate(candidate)
 target='T:'+tid
 before_reviews={(x.get('code'),x.get('payment_id'),x.get('event_key')):x for x in existing.get('payment_reviews',[]) if x.get('event_key')==target or target in x.get('related_keys',[])}
 for review in result.get('payment_reviews',[]):
  if review.get('event_key')!=target and target not in review.get('related_keys',[]):continue
  if review.get('code') not in ['payment_components_incompatible','payment_exceeds_forecast','payment_exceeds_event']:continue
  previous=before_reviews.get((review.get('code'),review.get('payment_id'),review.get('event_key')))
  if not previous or review.get('amount_cents',0)>previous.get('amount_cents',0):raise DomainError('A correção conflita com valor ou composição já realizada. Preserve os fatos e revise apenas o remanescente compatível.')
 return candidate
