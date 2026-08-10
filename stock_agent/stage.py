class StageDetector:
    def detect(self, current: float, ma20: float, ma50: float, return_20d_pct: float, atr_pct: float) -> str:
        if min(current, ma20, ma50) <= 0:
            return "UNKNOWN"
        extension = (current / ma20 - 1) * 100
        if current < ma50 and ma20 <= ma50:
            return "STAGE_1"
        if current >= ma20 >= ma50 and extension <= max(12, atr_pct * 2):
            return "STAGE_2"
        if current > ma20 > ma50 and (extension > max(12, atr_pct * 2) or return_20d_pct > 25):
            return "STAGE_3"
        return "UNKNOWN"
