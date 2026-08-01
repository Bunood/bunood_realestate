# Copyright (c) 2026, Bunood and contributors
# For license information, please see license.txt
"""Pure apportionment primitives — no DB, no invoice logic. Shared by the rent generator
(tasks.py) and the CAM apportioner (cam.py). Lives here (not in the invoice-generation
module) so a caller imports a pure helper, never a billing engine."""

from frappe.utils import flt


def split_amount(base, weights):
	"""Split ``base`` across ``weights``; the LAST line absorbs rounding so the shares sum
	EXACTLY to ``base`` (2dp). Pure & testable. Falls back to base/n when all weights are 0."""
	base = flt(base)
	total = sum(flt(w) for w in weights)
	n = len(weights)
	shares = []
	running = 0.0
	for i, w in enumerate(weights):
		if i == n - 1:
			shares.append(flt(base - running, 2))
		else:
			s = flt(base * flt(w) / total, 2) if total else flt(base / n, 2)
			shares.append(s)
			running += s
	return shares
