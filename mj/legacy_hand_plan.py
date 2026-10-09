"""Round-scoped soft hand routes; no action locks and no BigHandIntent switch."""
from dataclasses import dataclass


@dataclass
class HandPlan:
    round_key: object = None
    route: str = "ordinary"
    value: float = 0.0

    def update(self, round_key, route_values, margin=.25):
        if round_key != self.round_key:
            self.round_key, self.route, self.value = round_key, "ordinary", 0.0
        best = max(route_values, key=lambda key: (route_values[key], key == self.route, key))
        previous = route_values.get(self.route, float("-inf"))
        if route_values[best] > previous + margin:
            self.route = best
        self.value = route_values.get(self.route, 0.0)
        return {"route": self.route, "soft_value": self.value, "round_key": str(round_key)}


def public_route_values(context):
    hand = context.hand
    pairs = sum(n // 2 for n in hand)
    luxury = sum(n == 4 for n in hand)
    if context.locked:
        return {"ordinary": 0.0}
    return {"ordinary": 0.0, "chiitoi": (pairs - 5) * .25,
            "luxury": (pairs - 5) * .25 + luxury * .10 - .25}
