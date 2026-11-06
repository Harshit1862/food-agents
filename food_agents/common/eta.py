"""Delivery-time and pricing helpers shared by platform agents.

ETA = restaurant prep time + platform rider-assign delay + distance * platform minutes-per-km.
Restaurants own prep time; platforms own the delivery leg.
"""

import math


def compute_eta_min(
    prep_time_min: float,
    rider_assign_min: float,
    distance_km: float,
    min_per_km: float,
) -> int:
    """Total ETA in whole minutes, rounded up so we never under-promise."""
    total = prep_time_min + rider_assign_min + distance_km * min_per_km
    return math.ceil(round(total, 6))  # round first: float noise like 37.000000000000004 must not become 38


def listed_price(base_price: float, price_multiplier: float) -> int:
    """Price a platform shows for an item (base price x platform multiplier), whole rupees."""
    return round(base_price * price_multiplier)


def apply_discount(amount: float, discount_pct: float) -> int:
    """Discount applied to the items subtotal, whole rupees."""
    return round(amount * (1 - discount_pct / 100))