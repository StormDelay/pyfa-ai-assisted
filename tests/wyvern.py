"""The Wyvern from the fit search design brief, with real item names.

The brief's absolute EHP differs from these (~6% lower here, ratios equal),
so tests compare against evaluate_fit on these fits, not the brief's numbers.
"""
EXTENDER = "Dread Guristas Capital Shield Extender"
HARDENER = "Estamel's Modified Multispectrum Shield Hardener"
DC = "Cormack's Modified Damage Control"
PLATE = "CONCORD 25000mm Steel Plates"
PDS = "Chelm's Modified Power Diagnostic System"
RIG = "Capital Core Defense Field Extender II"
POD = ["High-grade Nirvana Alpha", "High-grade Nirvana Beta", "High-grade Nirvana Gamma",
       "High-grade Nirvana Delta", "High-grade Nirvana Epsilon", "High-grade Nirvana Omega",
       "Zainou 'Gnome' Shield Management SM-706",
       "Inherent Implants 'Noble' Mechanic MC-806",
       "Inherent Implants 'Noble' Hull Upgrades HG-1008"]
BOOST = ("[Nighthawk, Shield links]\n\n\n"
         "Shield Command Burst II, Shield Harmonizing Charge\n"
         "Shield Command Burst II, Active Shielding Charge\n"
         "Shield Command Burst II, Shield Extension Charge\n\n\n"
         "Caldari Navy Command Mindlink\n")
PHENOMENA = "[Leviathan, Phenomena]\n\n\nCaldari Phenomena Generator\n"
CONDITIONS = {"command": [{"fit": BOOST}, {"fit": PHENOMENA}]}
HOT = {**CONDITIONS, "module_states": [{"module": HARDENER, "state": "overheated"}]}


def fit(lows: list[str]) -> str:
    return ("[Wyvern, Brief Wyvern]\n" + "\n".join(lows) + "\n\n"
            + f"{EXTENDER}\n" * 5 + f"{HARDENER}\n" * 3 + "\n\n"
            + f"{RIG}\n" * 3 + "\n" + "\n".join(POD) + "\n")


BRIEF = fit([DC] + [PLATE] * 3)          # the agent's first answer
BEST_LOWS = fit([DC] + [PDS] * 3)        # what the agent should have found
NO_DC = fit([PDS] * 4)                   # must lose to BEST_LOWS
