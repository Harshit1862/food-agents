"""The life of an order after the restaurant accepts it, as a list of stages.

Delivery time is the sum of three phases (the same numbers the ETA was built from):
    preparing  -> prep_min          (the restaurant cooks)
    rider_assigned -> rider_assign_min  (a rider is found and reaches the restaurant)
    out_for_delivery -> travel_min  (the ride to the customer)
The simulation sleeps SIM_SECONDS_PER_MIN real seconds per delivery minute.
"""

from math import ceil

STAGE_TEXT = {
    "confirmed": "{restaurant} accepted your order.",
    "preparing": "{restaurant} is preparing your food.",
    "rider_assigned": "A rider has been assigned and is heading to {restaurant}.",
    "out_for_delivery": "Your order is out for delivery.",
    "delivered": "Delivered. Enjoy your meal!",
}


def stage_plan(eta_breakdown: dict) -> list[tuple[str, float]]:
    """[(stage, minutes spent in that stage), ...] in order."""
    return [
        ("confirmed", 0.0),
        ("preparing", float(eta_breakdown["prep_min"])),
        ("rider_assigned", float(eta_breakdown["rider_assign_min"])),
        ("out_for_delivery", float(eta_breakdown["travel_min"])),
        ("delivered", 0.0),
    ]


def minutes_left(plan: list[tuple[str, float]], index: int) -> int:
    """Whole minutes still to go when stage number `index` begins."""
    return ceil(round(sum(minutes for _, minutes in plan[index:]), 6))