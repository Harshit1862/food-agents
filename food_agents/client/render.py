"""Turns results into the text shown to the customer. Every number here comes from the quotes."""

from food_agents.client.satisfaction import label, money


def render_question(needs_input: list[dict]) -> str:
    """One question covering every restaurant that needs a choice."""
    lines = []
    for entry in needs_input:
        place = entry["restaurant_name"]
        for issue in entry["issues"]:
            if issue["problem"] == "ambiguous":
                lines.append(f"'{issue['item']}' matches several dishes at {place}: " + ", ".join(issue["options"]))
            elif issue["problem"] == "unavailable":
                alt = ", ".join(issue["alternatives"]) or "nothing similar right now"
                lines.append(f"{issue['item']} is unavailable at {place}. Similar dishes: {alt}")
    lines.append("Which one would you like?")
    return "\n".join(lines)


def render_options(eligible: list[dict]) -> str:
    lines = ["Options (best match first):"]
    for n, e in enumerate(eligible, 1):
        o = e["option"]
        lines.append(
            f"  {n}. {label(o)}   {money(o['total'])} · {round(o['eta_min'])} min · "
            f"★{o['rating']:.1f} · score {e['score']:.0f}"
        )
    return "\n".join(lines)


def render_bill(option: dict) -> str:
    lines = [f"Bill for {label(option)}:"]
    for l in option["lines"]:
        lines.append(f"  {int(l['qty'])} × {l['item']}   {money(l['line_total'])}")
    if option["discount"]:
        lines.append(f"  Discount ({round(option['discount_pct'])}%)   −{money(option['discount'])}")
    lines.append(f"  Delivery   {money(option['delivery_fee'])}")
    lines.append(f"  Total   {money(option['total'])}   (about {round(option['eta_min'])} min)")
    return "\n".join(lines)


def render_ineligible(ineligible: list[dict], limit: int | None = None) -> str:
    shown = ineligible if limit is None else ineligible[:limit]
    lines = []
    for e in shown:
        o = e["option"]
        lines.append(
            f"  - {label(o)}   {money(o['total'])} · {round(o['eta_min'])} min — " + "; ".join(e["violations"])
        )
    return "\n".join(lines)


def fallback_summary(eligible: list[dict]) -> str:
    """Used if the LLM is unavailable: same facts, plain wording."""
    best = eligible[0]["option"]
    text = f"I'd go with {label(best)}: {money(best['total'])}, about {round(best['eta_min'])} min."
    if len(eligible) > 1:
        nxt = eligible[1]["option"]
        cost = round(nxt["total"] - best["total"])
        mins = round(nxt["eta_min"] - best["eta_min"])
        text += (
            f" The next best, {label(nxt)}, costs {money(abs(cost))} {'more' if cost >= 0 else 'less'} "
            f"and is {abs(mins)} min {'slower' if mins >= 0 else 'faster'}."
        )
    return text


def render_update(data: dict) -> str:
    """One live line per delivery stage. `data` is the stage update the platform streamed."""
    line = f"  • {data['text']}"
    if data["minutes_left"]:
        line += f"  (about {int(data['minutes_left'])} min left)"
    if data["stage"] == "confirmed":
        line += f"\n    Order {data['order_id']} · task {data['task_id']}"
    return line


def render_receipt(order: dict) -> str:
    """Final message once the order is delivered. `order` is the receipt saved by the platform."""
    lines = [f"Your order {order['order_id']} from {order['platform_id'].title()} · {order['restaurant_name']} was delivered."]
    for l in order["lines"]:
        lines.append(f"  {int(l['qty'])} × {l['item']}")
    lines.append(f"  Paid {money(order['total'])} (delivery fee {money(order['delivery_fee'])} included)")
    return "\n".join(lines)


def render_status(data: dict) -> str:
    """Where an order is right now (from the platform's task status)."""
    line = f"Order {data['order_id']} · {data['restaurant_name']}: {data['text']}"
    if data["minutes_left"]:
        line += f" About {int(data['minutes_left'])} min left."
    return line


def render_restaurants(block: dict) -> str:
    lines = [f"Restaurants on {block['platform_name']}" + (f" ({block['filter']})" if block.get("filter") else "") + ":"]
    for n, r in enumerate(block["restaurants"], 1):
        deal = f" · {round(r['discount_pct'])}% off" if r["discount_pct"] else ""
        state = "" if r["is_online"] else " · closed"
        lines.append(
            f"  {n}. {r['name']} ({r['cuisine']}) ★{r['rating']:.1f} · about {round(r['eta_min'])} min · "
            f"delivery {money(r['delivery_fee'])}{deal}{state}"
        )
    return "\n".join(lines)


def _dish_line(item: dict) -> str:
    diet = "veg" if item["is_veg"] else "non-veg"
    price = money(item["price"]) if item["available"] else "unavailable"
    return f"  - {item['name']} ({diet}) {price}"


def render_menu(block: dict) -> str:
    head = f"{block['restaurant_name']} on {block['platform_name']} · {block['cuisine']} · ★{block['rating']:.1f}"
    head += f" · about {round(block['eta_min'])} min · delivery {money(block['delivery_fee'])}"
    if block["discount_pct"]:
        head += f" · {round(block['discount_pct'])}% off"
    lines = [head + (f"  [{block['filters']}]" if block.get("filters") else "")]
    lines += [_dish_line(row["item"]) for row in block["rows"]]
    return "\n".join(lines)


def render_dishes(block: dict) -> str:
    lines = [f"Where you can get '{block['query']}':"]
    for g in block["groups"]:
        lines.append(f"  {g['restaurant_name']} on {g['platform_name']} (★{g['rating']:.1f})")
        lines += ["  " + _dish_line(row["item"]) for row in g["rows"]]
    return "\n".join(lines)


def render_cart(block: dict) -> str:
    option = block.get("option")
    if option is None:
        return f"Cart ({block['restaurant_name']} on {block['platform_id'].title()}): {block.get('problem') or 'could not be priced'}"
    return render_bill(option) + "\nSay 'place the order' to order it."