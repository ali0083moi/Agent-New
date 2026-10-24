"""Where the LLM money goes: calls, tokens and cost by purpose and model.

    python -m app.usage_report [--days 30]

Prints numbers only (no message text), so it is safe to run from CI logs (.github/workflows/ops.yml).
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import func

from . import db, prefs, timeutil
from .config import get_settings
from .db import LlmUsage, Message, session


def report(days: int) -> str:
    db.init_engine()
    since = timeutil.today() - timedelta(days=days - 1)
    out: list[str] = []
    with session() as s:
        rows = (
            s.query(LlmUsage.purpose, LlmUsage.model, func.count(), func.sum(LlmUsage.prompt_tokens),
                    func.sum(LlmUsage.completion_tokens), func.sum(LlmUsage.cost_usd))
            .filter(LlmUsage.day >= since)
            .group_by(LlmUsage.purpose, LlmUsage.model)
            .all()
        )
        total = sum(r[5] or 0 for r in rows) or 1e-9
        out.append(f"LLM usage since {since} ({days} days)")
        out.append(f"{'purpose':10} {'model':26} {'calls':>6} {'in tok':>10} {'avg in':>7} {'out tok':>8} {'cost $':>8} {'share':>6}")
        for purpose, model, n, pt, ct, cost in sorted(rows, key=lambda r: -(r[5] or 0)):
            out.append(f"{purpose:10} {model[:26]:26} {n:6d} {pt or 0:10d} {(pt or 0) // max(n, 1):7d} {ct or 0:8d} "
                       f"{cost or 0:8.3f} {100 * (cost or 0) / total:5.0f}%")
        out.append(f"{'TOTAL':10} {'':26} {sum(r[2] for r in rows):6d} {sum(r[3] or 0 for r in rows):10d} {'':7} "
                   f"{sum(r[4] or 0 for r in rows):8d} {sum(r[5] or 0 for r in rows):8.3f}")
        out.append("\nPer day:")
        for day, n, cost in (s.query(LlmUsage.day, func.count(), func.sum(LlmUsage.cost_usd))
                             .filter(LlmUsage.day >= since).group_by(LlmUsage.day).order_by(LlmUsage.day)):
            out.append(f"  {day}  {n:5d} calls  ${cost or 0:.3f}")
        start = timeutil.at(since, "00:00")
        out.append("\nMessages:")
        for role, kind, n in (s.query(Message.role, Message.kind, func.count()).filter(Message.created_at >= start)
                              .group_by(Message.role, Message.kind)):
            out.append(f"  {role:10} {kind:14} {n}")
        st = get_settings()
        out.append("\nModels in use:")
        for kind in ("chat", "fast", "vision", "ielts"):
            override = prefs.get(f"llm_{kind}_model", s=s) or ""
            default = getattr(st, f"{kind}_model", "")
            out.append(f"  {kind:7} {override or default}{'  (set in Settings)' if override else ''}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    print(report(ap.parse_args().days))


if __name__ == "__main__":
    main()
