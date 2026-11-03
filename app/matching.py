"""Match a component name to a product that appears on the user's Zoho bills."""
import re

STOP = {"and", "the", "of", "for", "with", "a", "an", "to", "in", "pcs", "nos", "no"}
THRESHOLD = 0.6


# Spelling variants that should count as the same word.
SYNONYMS = [(r"bus[\s-]?bars?", "busbar"), (r"(lithium|li)[\s-]?ion", "liion"), (r"\bbms\b|battery management system", "bms"),
            (r"\bcells\b", "cell"), (r"\bwires\b", "wire"), (r"\bcables\b", "cable")]


def tokens(text: str) -> set:
    t = (text or "").lower()
    for pattern, repl in SYNONYMS:
        t = re.sub(pattern, repl, t)
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP}


def score(a: str, b: str) -> float:
    """Token overlap: Jaccard, or 0.9 x containment when one name is contained in the other."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return max(inter / len(ta | tb), 0.9 * inter / min(len(ta), len(tb)))


def best_match(name: str, products: dict):
    """products: key -> {"name": ...}. Returns (key, score) or (None, 0). Ties go to the shorter name."""
    best = (None, 0.0, 10**9)
    for key, p in products.items():
        s = score(name, p["name"])
        if s > best[1] or (s == best[1] and s > 0 and len(p["name"]) < best[2]):
            best = (key, s, len(p["name"]))
    return (best[0], best[1]) if best[1] >= THRESHOLD else (None, 0.0)
