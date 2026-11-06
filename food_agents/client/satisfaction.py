"""How well does each quoted option satisfy what the customer asked for?

Pure code, no LLM: a budget or a deadline is a hard rule, never "mostly respected".

1. Hard constraints (max_budget, max_eta_min) split options into eligible / ineligible.
2. Eligible options get a 0-100 score = weighted mix of three ratios, each 1.0 for the
   best option in the set: cheapness (best_total / total), speed (best_eta / eta),
   rating (rating / best_rating). The weights depend on what the customer stressed.
3. If nothing is eligible, the least-violating options are shown instead.
"""

WEIGHTS = {
    "balanced": {"price": 0.40, "eta": 0.40, "rating": 0.20},
    "cheapest": {"price": 0.70, "eta": 0.15, "rating": 0.15},
    "fastest": {"price": 0.15, "eta": 0.70, "rating": 0.15},
    "best_rated": {"price": 0.15, "eta": 0.15, "rating": 0.70},
}


def money(amount: float) -> str:
    return f"₹{int(round(amount))}"


def label(option: dict) -> str:
    return f"{option['platform_id'].title()} · {option['restaurant_name']}"


def evaluate(options: list[dict], max_budget: float | None, max_eta_min: float | None, priority: str = "balanced") -> dict:
    eligible, ineligible = [], []

    for option in options:
        violations, overage = [], 0.0
        if max_budget is not None and option["total"] > max_budget:
            violations.append(f"{money(option['total'] - max_budget)} over your {money(max_budget)} budget")
            overage += (option["total"] - max_budget) / max_budget
        if max_eta_min is not None and option["eta_min"] > max_eta_min:
            violations.append(
                f"{round(option['eta_min'] - max_eta_min)} min over your {round(max_eta_min)}-min limit"
            )
            overage += (option["eta_min"] - max_eta_min) / max_eta_min
        entry = {"option": option, "violations": violations, "overage": overage}
        (ineligible if violations else eligible).append(entry)

    if eligible:
        w = WEIGHTS[priority]
        best_total = min(e["option"]["total"] for e in eligible)
        best_eta = min(e["option"]["eta_min"] for e in eligible)
        best_rating = max(e["option"]["rating"] for e in eligible)
        for e in eligible:
            o = e["option"]
            ratio = (
                w["price"] * best_total / o["total"]
                + w["eta"] * best_eta / o["eta_min"]
                + w["rating"] * o["rating"] / best_rating
            )
            e["score"] = round(100 * ratio, 1)
        eligible.sort(key=lambda e: (-e["score"], e["option"]["total"], e["option"]["eta_min"]))

    ineligible.sort(key=lambda e: e["overage"])
    return {"eligible": eligible, "ineligible": ineligible}


def _brief(option: dict) -> dict:
    """A compact, number-checked summary of one option, used as the LLM's only source of facts."""
    return {
        "platform": option["platform_id"].title(),
        "restaurant": option["restaurant_name"],
        "items": [f"{int(l['qty'])} x {l['item']}" for l in option["lines"]],
        "total_rupees": int(round(option["total"])),
        "delivery_minutes": int(round(option["eta_min"])),
        "rating": option["rating"],
        "discount_rupees": int(round(option["discount"])),
        "delivery_fee_rupees": int(round(option["delivery_fee"])),
    }


def recommendation_facts(evaluation: dict, max_budget, max_eta_min, priority: str) -> dict:
    best = evaluation["eligible"][0]["option"]
    alternatives = []
    for e in evaluation["eligible"][1:3]:
        o = e["option"]
        alternatives.append(
            {
                **_brief(o),
                "costs_more_rupees": int(round(o["total"] - best["total"])),
                "extra_minutes": int(round(o["eta_min"] - best["eta_min"])),
            }
        )
    return {
        "customer_limits": {"max_budget_rupees": max_budget, "max_delivery_minutes": max_eta_min},
        "customer_priority": priority,
        "recommended": _brief(best),
        "next_best_alternatives": alternatives,
    }