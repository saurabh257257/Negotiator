"""Cost drivers and component templates (starter library for battery / EV manufacturing)."""

# yahoo: Yahoo Finance symbol where a free series exists; None means a benchmark the user uploads (CSV).
DRIVERS = {
    "lith":   {"name": "Lithium carbonate",    "type": "Commodity", "yahoo": None},
    "cobalt": {"name": "Cobalt",               "type": "Commodity", "yahoo": None},
    "nickel": {"name": "Nickel",               "type": "Commodity", "yahoo": None},
    "copper": {"name": "Copper",               "type": "Commodity", "yahoo": "HG=F"},
    "alu":    {"name": "Aluminum",             "type": "Commodity", "yahoo": "ALI=F"},
    "steel":  {"name": "Steel (HRC)",          "type": "Commodity", "yahoo": None},
    "resin":  {"name": "Polymer resin",        "type": "Commodity", "yahoo": None},
    "bpa":    {"name": "Epoxy raw (BPA/ECH)",  "type": "Commodity", "yahoo": None},
    "elec":   {"name": "Electronics index",    "type": "Commodity", "yahoo": None},
    "crude":  {"name": "Crude oil",            "type": "Transport", "yahoo": "BZ=F"},
    "usdinr": {"name": "USD/INR",              "type": "FX",        "yahoo": "USDINR=X"},
    "cnyinr": {"name": "CNY/INR",              "type": "FX",        "yahoo": "CNYINR=X"},
    "freight": {"name": "Container freight",   "type": "Transport", "yahoo": None},
}

# w: share of the component's vendor price explained by each driver.
# kw: keywords used by the rule-based classifier. Order matters: more specific templates come first.
TEMPLATES = [
    {"id": "bms",    "name": "BMS",                       "imp": True,  "w": {"elec": .35, "copper": .10, "resin": .05, "crude": .03},
     "kw": ["bms", "battery management", "balancer"]},
    # busbar/nickel strip is checked before holder: "Nickel Strip ... with holder" is a strip, not a holder
    {"id": "busbar", "name": "Busbar & nickel strip",     "imp": False, "w": {"copper": .60, "nickel": .10},
     "kw": ["busbar", "bus bar", "nickel strip", "series link"]},
    {"id": "holder", "name": "Cell holder (plastic)",    "imp": False, "w": {"resin": .55, "crude": .06, "usdinr": .08},
     "kw": ["holder", "spacer", "bracket"]},
    {"id": "sep",    "name": "Electrolyte & separator",   "imp": True,  "w": {"lith": .25, "resin": .20, "crude": .05},
     "kw": ["separator", "electrolyte"]},
    {"id": "epoxy",  "name": "Epoxy / potting",           "imp": True,  "w": {"bpa": .45, "crude": .15},
     "kw": ["epoxy", "potting", "hardener", "adhesive", "resin"]},
    {"id": "wire",  "name": "Wires & connectors",        "imp": False, "w": {"copper": .45, "resin": .20},
     "kw": ["wire", "cable", "connector", "harness", "fuse", "lead"]},
    {"id": "box",    "name": "Battery box (sheet metal)", "imp": False, "w": {"steel": .45, "alu": .15, "crude": .05},
     "kw": ["box", "enclosure", "housing", "tray", "casing", "case", "rack"]},
    {"id": "pkg",    "name": "Packaging & foam",          "imp": False, "w": {"resin": .50, "crude": .08},
     "kw": ["foam", "carton", "packaging", "bag", "insert", "label", "protector"]},
    {"id": "cell",   "name": "Li-ion cell",               "imp": True,  "w": {"lith": .32, "cobalt": .08, "nickel": .08, "copper": .06, "alu": .04, "crude": .03},
     "kw": ["cell", "li-ion", "lithium", "lfp", "nmc", "18650", "21700", "32700", "prismatic", "pouch"]},
]
OTHER = {"id": "other", "name": "Other (no cost-driver template)", "imp": False, "w": {}, "kw": []}
BY_ID = {t["id"]: t for t in TEMPLATES + [OTHER]}


def classify(name: str) -> str:
    """Rule-based template for a component name; 'other' when nothing matches."""
    n = f" {name.lower()} "
    for t in TEMPLATES:
        if any(k in n for k in t["kw"]):
            return t["id"]
    return "other"
