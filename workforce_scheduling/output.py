from __future__ import annotations

from typing import Dict, List, Tuple
import csv

from .data import ProblemData
from .solve import Assignment


def format_roster_text(
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int] | None = None,
) -> str:
    by_slot: Dict[tuple[int, int, str], List[str]] = {}
    employee_names = {e.employee_id: e.name for e in data.employees}

    for assignment in assignments:
        key = (assignment.day, assignment.shift, assignment.role)
        by_slot.setdefault(key, []).append(employee_names[assignment.employee_id])

    lines: List[str] = []
    for day in data.days:
        lines.append(f"Day {day}")
        for shift in range(len(data.shifts)):
            lines.append(f"  {data.shifts[shift]}")
            for role in data.roles:
                names = ", ".join(sorted(by_slot.get((day, shift, role), [])))
                if not names:
                    names = "-"
                suffix = ""
                if shortages is not None:
                    shortage = shortages.get((day, shift, role), 0)
                    suffix = f" (shortage {shortage})"
                lines.append(f"    {role}: {names}{suffix}")
    return "\n".join(lines)


def write_roster_csv(
    path: str,
    data: ProblemData,
    assignments: List[Assignment],
) -> None:
    employee_names = {e.employee_id: e.name for e in data.employees}
    employee_costs = {e.employee_id: e.hourly_cost for e in data.employees}

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "day",
                "shift",
                "role",
                "employee_id",
                "employee_name",
                "hourly_cost",
            ]
        )
        for assignment in assignments:
            writer.writerow(
                [
                    assignment.day,
                    data.shifts[assignment.shift],
                    assignment.role,
                    assignment.employee_id,
                    employee_names[assignment.employee_id],
                    employee_costs[assignment.employee_id],
                ]
            )
