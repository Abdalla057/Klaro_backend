from rest_framework.throttling import ScopedRateThrottle


class RenvoiCodeThrottle(ScopedRateThrottle):
    """Limite les renvois de code, avec une période exprimée en minutes."""

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        nombre, periode = rate.split("/")
        if periode.endswith("min"):
            return int(nombre), int(periode[:-3]) * 60
        if periode.endswith("m"):
            return int(nombre), int(periode[:-1]) * 60
        return super().parse_rate(rate)
