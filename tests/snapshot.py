"""Comparison helper for the demo-data golden snapshot (tests/data/demo_snapshot.json)."""


def differences(expected, actual, path="deliveries") -> list[str]:
    """Human-readable list of where `actual` deviates from `expected`."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for key in sorted(expected.keys() | actual.keys()):
            if key not in actual:
                out.append(f"{path}.{key}: missing (expected {expected[key]!r})")
            elif key not in expected:
                out.append(f"{path}.{key}: unexpected {actual[key]!r}")
            else:
                out.extend(differences(expected[key], actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        out = [f"{path}: {len(actual)} entries, expected {len(expected)}"] if len(expected) != len(actual) else []
        for i, (e, a) in enumerate(zip(expected, actual)):
            out.extend(differences(e, a, f"{path}[{i}]"))
        return out
    return [] if expected == actual else [f"{path}: {actual!r}, expected {expected!r}"]
