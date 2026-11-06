from food_agents.common.eta import apply_discount, compute_eta_min, listed_price


def price_quote(platform: dict, listing: dict, quote: dict) -> dict:
    """Turn a restaurant's base-price quote into this platform's priced option.

    platform: the platform's own settings (rider speed, assign delay)
    listing:  this platform's deal with this restaurant (markup, fee, discount)
    quote:    what the restaurant agent returned over A2A (base prices, prep time, distance)
    """
    lines = []
    for line in quote["lines"]:
        qty = int(line["qty"])  # numbers cross A2A as floats
        unit_price = listed_price(line["unit_price"], listing["price_multiplier"])
        lines.append(
            {"item": line["item"], "qty": qty, "unit_price": unit_price, "line_total": unit_price * qty}
        )

    items_subtotal = sum(l["line_total"] for l in lines)
    after_discount = apply_discount(items_subtotal, listing["discount_pct"])

    return {
        "platform_id": platform["id"],
        "restaurant_id": quote["restaurant_id"],
        "restaurant_name": quote["restaurant_name"],
        "rating": quote["rating"],
        "lines": lines,
        "items_subtotal": items_subtotal,
        "discount_pct": listing["discount_pct"],
        "discount": items_subtotal - after_discount,
        "delivery_fee": listing["delivery_fee"],
        "total": after_discount + listing["delivery_fee"],
        "eta_min": compute_eta_min(
            quote["prep_time_min"], platform["rider_assign_min"], quote["distance_km"], platform["min_per_km"]
        ),
        "eta_breakdown": {
            "prep_min": quote["prep_time_min"],
            "rider_assign_min": platform["rider_assign_min"],
            "travel_min": round(quote["distance_km"] * platform["min_per_km"], 1),
        },
    }