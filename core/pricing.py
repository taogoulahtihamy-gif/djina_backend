"""
core/pricing.py
Calcul de distance et de prix pour les courses.
"""
from decimal import Decimal
from math import radians, sin, cos, asin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine_distance_km(lat1, lon1, lat2, lon2) -> Decimal:
    """Distance à vol d'oiseau entre deux points GPS, en kilomètres."""
    lat1, lon1, lat2, lon2 = (float(lat1), float(lon1), float(lat2), float(lon2))
    lat1_r, lon1_r, lat2_r, lon2_r = map(radians, (lat1, lon1, lat2, lon2))

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    distance = 2 * EARTH_RADIUS_KM * asin(sqrt(a))

    return Decimal(str(round(distance, 3)))
