"""Exact monetary composition for forecasts and recorded settlements.

All values are integer centavos. ``base_cents`` is the registered original
balance, which can contain older charges that have not been discriminated.
These helpers do not infer a factual composition from a historical total.
"""

from collections.abc import Mapping


_FIELDS = ("base_cents", "interest_cents", "penalty_cents", "discount_cents", "other_cost_cents")
_NET_FIELDS = ("base_cents", "interest_cents", "penalty_cents", "other_cost_cents")


def _money(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} deve ser um número inteiro de centavos, maior ou igual a zero.")
    return value


def _compose(base, interest=0, penalty=0, discount=0, other=0, *, allow_zero=False):
    values = (base, interest, penalty, discount, other)
    result = {field: _money(value, field) for field, value in zip(_FIELDS, values)}
    if discount > base:
        raise ValueError("O desconto não pode superar o saldo original registrado.")
    total = base + interest + penalty + other - discount
    if total == 0 and not allow_zero:
        raise ValueError("O valor final deve ser maior que zero.")
    result["amount_cents"] = total
    return result


def components(base, interest=0, penalty=0, discount=0, other=0):
    """Build a validated composition with a strictly positive final amount."""
    return _compose(base, interest, penalty, discount, other)


def _read(event):
    if not isinstance(event, Mapping):
        raise ValueError("A composição deve ser um objeto com valores em centavos.")
    if event.get("components_known") is False:
        raise ValueError("A composição histórica não é conhecida; ela não equivale a encargos zero.")
    if "base_cents" not in event:
        raise ValueError("Informe o saldo original registrado para discriminar a composição.")
    result = _compose(*(event.get(field, 0) for field in _FIELDS), allow_zero=True)
    if "amount_cents" in event and _money(event["amount_cents"], "amount_cents") != result["amount_cents"]:
        raise ValueError("O total informado não corresponde à soma dos componentes.")
    return result


def _allocate(total, capacities):
    """Allocate one exact total into nonnegative capacities, residue from last.

    Floors keep the proportional split deterministic. Each residual cent is
    assigned to the last slot with available capacity. No floating point is
    used, and the work depends on the number of slots, not the money amount.
    """
    available = sum(capacities)
    if total > available:
        raise ValueError("A alocação supera o valor disponível.")
    if not available:
        return [0] * len(capacities)
    result = [total * capacity // available for capacity in capacities]
    residue = total - sum(result)
    for index in range(len(result) - 1, -1, -1):
        if not residue:
            break
        if result[index] < capacities[index]:
            result[index] += 1
            residue -= 1
    if residue:
        raise ValueError("Não foi possível conservar os centavos na distribuição.")
    return result


def split_components(component_dict, weights):
    """Return component dictionaries for the given installment totals.

    ``weights`` are the monetary installment amounts, including any entry.
    They must add up exactly to the composed amount. Zero-value slots are
    accepted for stable indexing. Each row and column is conserved: all
    component totals match the original, and every installment equals its
    supplied total. Discount and its associated gross base are allocated
    together, so no installment has a negative net base.
    """
    original = _read(component_dict)
    try:
        amounts = [_money(weight, "Valor da parcela") for weight in weights]
    except TypeError as error:
        raise ValueError("Informe uma sequência de valores das parcelas.") from error
    if not amounts:
        raise ValueError("Informe ao menos uma parcela.")
    if sum(amounts) != original["amount_cents"]:
        raise ValueError("A soma das parcelas deve corresponder ao valor final.")
    capacity = amounts[:]
    rows = []
    net_values = [
        original["base_cents"] - original["discount_cents"],
        original["interest_cents"],
        original["penalty_cents"],
        original["other_cost_cents"],
    ]
    for value in net_values:
        allocated = _allocate(value, capacity)
        rows.append(allocated)
        capacity = [remaining - used for remaining, used in zip(capacity, allocated)]
    # Discount is not a cash payment. Its gross counterpart is added only after
    # distributing the four positive net components within each cash amount.
    if original["amount_cents"]:
        discounts = _allocate_unbounded(original["discount_cents"], amounts)
    else:
        discounts = [0] * len(amounts)
        discounts[-1] = original["discount_cents"]
    return [
        _compose(rows[0][index] + discounts[index], rows[1][index], rows[2][index],
                 discounts[index], rows[3][index], allow_zero=True)
        for index in range(len(amounts))
    ]


def _allocate_unbounded(total, weights):
    """Proportionally split a noncash amount without capping it by cash values."""
    denominator = sum(weights)
    result = [total * weight // denominator for weight in weights]
    residue = total - sum(result)
    # Quotient floors leave fewer than len(weights) residual cents.
    for index in range(len(weights) - 1, -1, -1):
        if residue and weights[index]:
            result[index] += 1
            residue -= 1
    return result


def paid_components(event, amount):
    """Allocate an amount actually paid to net base, interest, penalty and costs.

    This is an explicit proportional allocation, not an inference about
    undocumented historical interest. The returned discount is always zero.
    For multiple payments, pass the remaining composition, or allocate the
    cumulative amount once; do not independently round repeated partials
    against the unchanged original event.
    """
    original = _read(event)
    amount = _money(amount, "Valor pago")
    if amount > original["amount_cents"]:
        raise ValueError("O valor pago não pode superar o saldo do lançamento.")
    net = _compose(original["base_cents"] - original["discount_cents"],
                   original["interest_cents"], original["penalty_cents"], 0,
                   original["other_cost_cents"], allow_zero=True)
    return split_components(net, [amount, original["amount_cents"] - amount])[0]


def remaining_components(event, paid):
    """Return the remaining forecast after a cumulative amount or payment dict.

    A payment dictionary must contain the already allocated composition, with
    discount zero. Keeping the original discount alongside its corresponding
    unpaid gross base preserves the original information even when the cash
    balance is zero. Fully paid events therefore return amount_cents=0.
    """
    original = _read(event)
    allocated = paid_components(original, paid) if type(paid) is int else _read(paid)
    if allocated["discount_cents"]:
        raise ValueError("Um pagamento realizado não pode conter desconto como valor pago.")
    net_original = dict(original)
    net_original["base_cents"] -= original["discount_cents"]
    if any(allocated[field] > net_original[field] for field in _NET_FIELDS):
        raise ValueError("A composição paga supera algum componente disponível no lançamento.")
    return _compose(original["base_cents"] - allocated["base_cents"],
                    original["interest_cents"] - allocated["interest_cents"],
                    original["penalty_cents"] - allocated["penalty_cents"],
                    original["discount_cents"],
                    original["other_cost_cents"] - allocated["other_cost_cents"],
                    allow_zero=True)