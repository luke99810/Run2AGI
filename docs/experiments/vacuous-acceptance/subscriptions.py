from dataclasses import dataclass
from typing import List

@dataclass
class Subscription:
    name: str
    monthly_cny: float
    renew_day: int

def monthly_total(subs: List[Subscription]) -> float:
    return sum(sub.monthly_cny for sub in subs)