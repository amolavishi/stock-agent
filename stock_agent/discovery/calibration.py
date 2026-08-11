from __future__ import annotations


def precision_at(records: list[dict], n: int) -> float:
    selected = records[:max(0, n)]
    if not selected:
        return 0.0
    return sum(bool(record.get("hit")) for record in selected) / len(selected)


def sensitivity_report(run_fn, base_rules: dict, perturbations: tuple[float, ...] = (0.9, 1.0, 1.1)) -> list[dict]:
    rows = []
    for factor in perturbations:
        rules = {key: value * factor if isinstance(value, (int, float)) else value
                 for key, value in base_rules.items()}
        result = run_fn(rules)
        rows.append({"factor": factor, "result": result})
    return rows
