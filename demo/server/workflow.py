"""Atribuição e revisão internas, com avaliação determinística do cenário completo."""
import copy
from engine import DomainError, calculate, creditor, options, simulate

PROPOSAL_FIELDS = ('n', 'entry_cents', 'entry_date', 'first_date', 'interval',
                   'include_reimbursements', 'notes', 'payment_dates',
                   'base_cents', 'interest_cents', 'penalty_cents', 'discount_cents')


def clean(value, minimum, label):
    value = str(value or '').strip()[:2000]
    if len(value) < minimum:
        raise DomainError(label)
    return value


def proposal_snapshot(state, creditor_id, changes):
    if not isinstance(changes, dict) or set(changes) - set(PROPOSAL_FIELDS):
        raise DomainError('Campos da proposta inválidos.')
    candidate, calculation = simulate(state, creditor_id, changes)
    proposal = candidate['proposals'][creditor_id]
    defaults = dict(interval='monthly', include_reimbursements=False, notes='',
                    payment_dates={}, base_cents=creditor(state, creditor_id)['debt_cents'],
                    interest_cents=0, penalty_cents=0, discount_cents=0)
    snapshot = {key: copy.deepcopy(proposal.get(key, defaults.get(key)))
                for key in PROPOSAL_FIELDS}
    return snapshot, candidate, calculation


def assess(state, creditor_id, proposal=None, calculation=None, choices=None):
    proposal = proposal or {}
    if calculation is None:
        _, calculation = simulate(state, creditor_id, proposal)
    if choices is None:
        choices = options(state, creditor_id, proposal)
    viable = [choice['n'] for choice in choices if choice['conditional_fit']]
    current = dict(state['proposals'][creditor_id], **proposal)
    pending_costs = sum(bool(e.get('cost_pending')) for e in
                        calculation['events'] + calculation['undated'])
    fiscal_count = len(state.get('fiscal_review', []))
    warnings = []
    for violation in calculation['violations']:
        for message in violation['messages']:
            flag='Condição de '+violation['name']+': '+message
            if flag not in warnings:warnings.append(flag)
    if fiscal_count:
        warnings.append('Há recebimentos com provisão fiscal pendente de revisão.')
    if pending_costs:
        warnings.append('Há encargos ainda não quantificados; custo desconhecido não é zero.')
    if calculation.get('needs_review'):
        warnings.append('Há pagamentos que exigem revisão de vínculo ou de remanescente.')
    if calculation['undated_out_cents']:
        warnings.append('Existem obrigações sem data fora do saldo projetado.')
    warnings.append('Depois de janeiro há somente compromissos conhecidos; a cobertura futura é incompleta.')
    margin = calculation['minimum']['margin_cents']
    return dict(minimum_payments=min(viable) if viable else None,
                requested_payments=current['n'], fits=margin >= 0 and not calculation['violations'] and not calculation.get('needs_review'),
                shortfall_cents=max(0, -margin), first_shortfall=calculation['first_shortfall'],
                min_bank_balance_cents=min(row['balance_cents'] for row in calculation['daily']),
                needs_fiscal_ack=bool(fiscal_count or pending_costs),
                needs_review=bool(calculation.get('needs_review')),
                pending_cost_count=pending_costs, fiscal_review_count=fiscal_count,
                flags=warnings, coverage_until=state['end'], horizon_complete=False,
                tail_out_cents=calculation['tail_out_cents'],
                rule_note='Menor quantidade que preserva caixa, reservas e piso no conjunto do cenário, nas datas e entradas informadas. Resultado condicionado às receitas previstas até janeiro; não comprova cobertura posterior nem apuração fiscal definitiva.')


def approve(state, creditor_id, proposal, evidence, note, acknowledge_risk):
    snapshot, candidate, calculation = proposal_snapshot(state, creditor_id, proposal)
    violations = [violation for violation in calculation['violations']
                  if violation['creditor_id'] == creditor_id]
    if violations:
        raise DomainError('Revise a condição do fornecedor antes de aprovar: ' + violations[0]['messages'][0])
    if calculation.get('needs_review'):
        raise DomainError('Há pagamentos com revisão de vínculo ou remanescente pendente. Resolva a revisão antes de aprovar.')
    assessment = assess(state, creditor_id, snapshot, calculation=calculation)
    if (assessment['shortfall_cents'] or assessment['needs_fiscal_ack']) and acknowledge_risk is not True:
        raise DomainError('Reconheça expressamente a falta de caixa, as reservas ou as pendências fiscais e de encargos antes de aprovar.')
    candidate['proposals'][creditor_id].update(status='accepted', evidence=evidence,
                                             approval_note=note)
    calculate(candidate)
    return candidate, assessment


def confirm_negotiation(state, user, creditor_id, proposal, evidence, reason, acknowledge_risk=False):
    evidence=clean(evidence,5,'Registre a referência do aceite do fornecedor.')
    reason=clean(reason,3,'Registre o motivo da confirmação.')
    if user['role'] not in ['admin','editor']:
        raise PermissionError('Seu perfil não permite confirmar uma negociação.')
    if user['role']=='editor' and state.get('assignments',{}).get(creditor_id,{}).get('user_id')!=user['id']:
        raise PermissionError('Este fornecedor não está atribuído a você.')
    _,_,calculation=proposal_snapshot(state,creditor_id,proposal)
    if user['role']=='editor' and calculation['minimum']['margin_cents']<0:
        raise DomainError('Esta condição compromete o caixa, o piso ou as reservas. Solicite autorização da gestora antes de confirmar.')
    if user['role']=='editor' and calculation['violations']:
        raise DomainError('Há condições de fornecedores ainda não respeitadas no conjunto do cenário. Solicite análise da gestora antes de confirmar.')
    return approve(state,creditor_id,proposal,evidence,reason,acknowledge_risk)
