"""Shift planning for paper samples under a limited number of prepress minutes.

The planner answers one question: which paper samples can this shift actually
print, so that the prepress crew discovers as many overflows as possible while
still covering every layout / reference-language combination the model has never
seen? The rules, in priority order:

1. Planned minutes never exceed the shift budget (concurrent orders share it).
2. Every required (unseen) combination gets at least one sample when the budget
   allows; otherwise as few combinations as possible are left out and the missing
   ones are named.
3. Remaining minutes are filled by a 0/1 knapsack that maximises the expected
   number of overflows (the summed model probability), so inserting a new order
   keeps the crew busy instead of leaving idle minutes.
4. Samples already started keep their booked minutes when a shift is re-planned,
   even if that pushes the shift past its budget (never stop a running sample).
"""
from dataclasses import dataclass, field
import math


@dataclass(frozen=True)
class PlanItem:
    sample_id: str
    minutes: float
    score: float
    group_key: str | None = None


@dataclass
class PlanResult:
    selected: set[str] = field(default_factory=set)
    ordered: list[str] = field(default_factory=list)
    covered_groups: set[str] = field(default_factory=set)
    missing_groups: list[str] = field(default_factory=list)
    scheduled_minutes: float = 0.0
    expected_overflows: float = 0.0
    over_budget: bool = False
    idle_minutes: float = 0.0


def minute_cost(minutes):
    """Whole minutes used for planning; round up so the shift is never overbooked."""
    return max(1, int(math.ceil(float(minutes) - 1e-9)))


def plan(items, budget_minutes, required_groups=()):
    """Plan a fresh shift from a set of candidate samples."""
    usable = [item for item in items if item.minutes is not None and float(item.minutes) > 0]
    budget = max(0, int(math.floor(float(budget_minutes) + 1e-9)))
    return _plan(usable, budget, required_groups, fixed=[])


def reschedule(items, budget_minutes, required_groups=(), fixed_ids=(), required_ids=()):
    """Re-plan a shift after an order is inserted.

    Samples in ``fixed_ids`` (already started) keep their booked minutes even if
    that pushes the shift over budget. Samples in ``required_ids`` are each
    order's foothold, so inserting a new order cannot starve it of any sample.
    Everything else is re-planned against what is left, filling the pool with the
    highest expected overflows.
    """
    usable = [item for item in items if item.minutes is not None and float(item.minutes) > 0]
    fixed_set = set(fixed_ids)
    fixed = [item for item in usable if item.sample_id in fixed_set]
    budget = max(0, int(math.floor(float(budget_minutes) + 1e-9)))
    return _plan(usable, budget, required_groups, fixed=fixed, required_ids=required_ids)


def _plan(usable, budget, required_groups, fixed, required_ids=()):
    required = [group for group in dict.fromkeys(required_groups) if group]
    if not usable:
        return PlanResult(missing_groups=required)

    cost = {item.sample_id: minute_cost(item.minutes) for item in usable}
    by_id = {item.sample_id: item for item in usable}

    chosen = {item.sample_id: item for item in fixed}
    spend = sum(cost[item.sample_id] for item in fixed)
    over_budget = spend > budget
    covered = {item.group_key for item in fixed if item.group_key in required}

    if not over_budget:
        cheapest = {}
        for item in usable:
            if item.sample_id in chosen or not item.group_key:
                continue
            if item.group_key not in required or item.group_key in covered:
                continue
            current = cheapest.get(item.group_key)
            if current is None or _rank(item, cost) < _rank(current, cost):
                cheapest[item.group_key] = item
        for group in sorted(cheapest, key=lambda key: _rank(cheapest[key], cost)):
            item = cheapest[group]
            price = cost[item.sample_id]
            if spend + price <= budget:
                chosen[item.sample_id] = item
                spend += price
                covered.add(group)

        for sample_id in dict.fromkeys(required_ids):
            item = by_id.get(sample_id)
            if item is None or item.sample_id in chosen:
                continue
            price = cost[item.sample_id]
            if spend + price <= budget:
                chosen[item.sample_id] = item
                spend += price

        leftover = [item for item in usable if item.sample_id not in chosen]
        for item in _knapsack(leftover, budget - spend, cost):
            chosen[item.sample_id] = item
            spend += cost[item.sample_id]

    selected = set(chosen)
    fixed_order = [item.sample_id for item in usable if item.sample_id in {f.sample_id for f in fixed}]
    planned_order = [item.sample_id for item in usable if item.sample_id in selected]
    ordered = fixed_order + [sample for sample in planned_order if sample not in fixed_order]
    total_minutes = round(sum(float(by_id[sample].minutes) for sample in selected), 2)
    return PlanResult(
        selected=selected,
        ordered=ordered,
        covered_groups=covered,
        missing_groups=[group for group in required if group not in covered],
        scheduled_minutes=total_minutes,
        expected_overflows=round(sum(float(by_id[sample].score) for sample in selected), 6),
        over_budget=over_budget,
        idle_minutes=float(max(0, budget - spend)),
    )


def _rank(item, cost):
    return (cost[item.sample_id], -item.score, item.sample_id)


def _knapsack(items, budget, cost):
    """0/1 knapsack on whole minutes, maximising the summed score."""
    if budget <= 0 or not items:
        return []
    negative = float("-inf")
    values = [negative] * (budget + 1)
    values[0] = 0.0
    takes = [bytearray(budget + 1) for _ in items]
    for index, item in enumerate(items):
        price = cost[item.sample_id]
        if price > budget:
            continue
        row = takes[index]
        for spent in range(budget, price - 1, -1):
            previous = values[spent - price]
            if previous == negative:
                continue
            candidate = previous + item.score
            if candidate > values[spent] + 1e-12:
                values[spent] = candidate
                row[spent] = 1
    best_spent = 0
    best_value = 0.0
    for spent in range(budget + 1):
        value = values[spent]
        if value == negative:
            continue
        if value > best_value + 1e-12:
            best_value = value
            best_spent = spent
    chosen = []
    spent = best_spent
    for index in range(len(items) - 1, -1, -1):
        if takes[index][spent]:
            item = items[index]
            chosen.append(item)
            spent -= cost[item.sample_id]
    return chosen
