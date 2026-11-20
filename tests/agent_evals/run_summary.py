"""Run summaries for the model evaluations, and the result tables made from them.

Each run of one model over the task set is recorded as one summary, saved as
bench_runs/models/<date>/<name>.summary.json. A summary holds counts and
settings, never a transcript: raw logs stay outside the repository, as
agent_trace.run_agent requires.

AGENTS.md says never to publish a benchmark figure without its date and scope,
so render_table refuses any summary that fails validate_summary and prints each
run's date, ClawBio commit and scope with the table.

The checks are plain Python, since jsonschema is in neither the core nor the dev
dependencies.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path

# date.fromisoformat alone also accepts 20261119 and 2026-W47-4.
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
COMMIT = re.compile(r"[0-9a-f]{7,40}")  # lowercase, as git prints it
SHA256 = re.compile(r"[0-9a-f]{64}")  # lowercase, as sha256sum and hashlib print it
HARDWARE = ("gpu", "cpu", "memory_gb")
# A gpu or cpu value holding one of these may be a path, a network share or
# user@host, so it is refused. Intel CPU names put the clock after an at sign;
# record the model only. Problems name the mark, never the value.
PRIVATE_MARKS = {"/": "a slash", "\\": "a backslash", "@": "an at sign"}


def _iso_date(value) -> bool:
    if not (isinstance(value, str) and ISO_DATE.fullmatch(value)):
        return False
    try:
        date.fromisoformat(value)  # rejects 2026-02-30
    except ValueError:
        return False
    return True


def _text(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _matches(pattern):
    return lambda value: isinstance(value, str) and pattern.fullmatch(value) is not None


def _count(minimum: int):
    # bool is an int in Python, and JSON true must not count as 1.
    return lambda value: isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _number(value) -> bool:
    """A number a float can hold: not a bool, NaN or infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def _optional_text(value) -> bool:
    return value is None or _text(value)


# Every field and what it must hold. A field inside an object is checked only when
# the object is there, so a missing object is one problem, not one per field.
FIELDS = {
    "date": (_iso_date, "an ISO date, YYYY-MM-DD"),
    "clawbio_commit": (_matches(COMMIT), "7 to 40 lowercase hex characters"),
    "harness_version": (_text, "a non-empty string"),
    "task_set.version": (_text, "a non-empty string"),
    "task_set.sha256": (_matches(SHA256), "64 lowercase hex characters"),
    "scope.tiers": (lambda v: isinstance(v, list) and v != [] and all(map(_text, v)),
                    "a non-empty list of non-empty strings"),
    "scope.n_cases": (_count(1), "an integer above 0"),
    "scope.n_repeats": (_count(1), "an integer above 0"),
    "model.provider": (_text, "a non-empty string"),
    "model.requested": (_text, "a non-empty string"),
    "model.returned": (_text, "a non-empty string"),
    "model.open_weights": (lambda v: isinstance(v, bool), "true or false"),
    "settings": (lambda v: isinstance(v, dict), "an object"),
    "context_window": (lambda v: v is None or _count(1)(v), "an integer above 0, or null"),
    "hardware.gpu": (_optional_text, "a non-empty string, or null"),
    "hardware.cpu": (_optional_text, "a non-empty string, or null"),
    "hardware.memory_gb": (lambda v: v is None or (_number(v) and v > 0), "a number above 0, or null"),
    "tokens.input": (_count(0), "an integer of 0 or more"),
    "tokens.output": (_count(0), "an integer of 0 or more"),
    "cost.usd": (lambda v: _number(v) and v >= 0, "a number of 0 or more"),
    "cost.price_source": (_text, "a non-empty string"),
    "cost.price_date": (_iso_date, "an ISO date, YYYY-MM-DD"),
    "wall_clock_seconds": (lambda v: _number(v) and v >= 0, "a number of 0 or more"),
}
OPTIONAL = {
    "model.quantization": (_optional_text, "a non-empty string, or null"),
    "model.digest": (_optional_text, "a non-empty string, or null"),
}
OBJECTS = ("task_set", "scope", "model", "hardware", "tokens")


def validate_summary(summary) -> list[str]:
    """Every problem with one run summary, each starting with the field it is about.

    An empty list means the summary may be published.
    """
    if not isinstance(summary, dict):
        return ["summary must be a JSON object"]
    problems = []
    for name in OBJECTS:
        if name not in summary:
            problems.append(f"{name} is missing")
        elif not isinstance(summary[name], dict):
            problems.append(f"{name} must be an object")
    problems += _cost_problems(summary)
    for path, (ok, rule) in (FIELDS | OPTIONAL).items():
        parent, _, key = path.rpartition(".")
        node = summary.get(parent) if parent else summary
        if not isinstance(node, dict):
            continue  # its object is reported above, or cost is null
        if key not in node:
            if path not in OPTIONAL:
                problems.append(f"{path} is missing")
        elif not ok(node[key]):
            problems.append(f"{path} must be {rule}")
    if isinstance(summary.get("hardware"), dict):
        problems += _hardware_problems(summary["hardware"])
    return problems + _json_problems(summary)


def _cost_problems(summary) -> list[str]:
    if "cost" not in summary:
        return ["cost is missing"]
    cost, model = summary["cost"], summary.get("model")
    if cost is None:
        # Null means a local run, and a model that is not open-weights runs only on its vendor's servers.
        if isinstance(model, dict) and model.get("open_weights") is False:
            return ["cost may be null only for a local run of an open-weights model"]
        return []
    if not isinstance(cost, dict):
        return ["cost must be an object, or null for a local run"]
    return []


def _hardware_problems(hardware: dict) -> list[str]:
    """GPU model, CPU model and memory only: nothing that names a machine, a person or a path."""
    problems = []
    if any(key not in HARDWARE for key in hardware):
        problems.append("hardware may hold only gpu, cpu and memory_gb")
    for key in ("gpu", "cpu"):
        value = hardware.get(key)
        if not isinstance(value, str):
            continue
        for mark, name in PRIVATE_MARKS.items():
            if mark in value:
                problems.append(f"hardware.{key} contains {name}, so it may hold a path, host or user name; "
                                "record the model name only")
                break
    return problems


def _json_problems(summary) -> list[str]:
    """A summary is saved as strict JSON, which has no NaN or infinity.

    sort_keys also rejects keys of mixed types, on which render_table's sort would fail.
    """
    try:
        json.dumps(summary, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError, RecursionError):
        return ["summary must be plain JSON, with no NaN, infinity or value JSON cannot hold"]
    return []


COLUMNS = ("Run", "Date", "Model", "Provider", "Open weights", "Tiers", "Cases x repeats",
           "Input tokens", "Output tokens", "Cost (USD)", "Wall clock (s)")
ALIGN = ("---:", "---", "---", "---", "---", "---", "---:", "---:", "---:", "---:", "---:")


def render_table(summaries) -> str:
    """A markdown table with one row per run, then each run's date, ClawBio commit and scope.

    Raises ValueError for an empty list or for any summary that fails
    validate_summary, so the figures cannot be printed without their date and
    scope. Rows are sorted, so the output does not depend on the input order.
    """
    runs = list(summaries)
    if not runs:
        raise ValueError("no run summaries to render")
    invalid = [f"summaries[{i}]: {'; '.join(problems)}"
               for i, run in enumerate(runs) if (problems := validate_summary(run))]
    if invalid:
        raise ValueError("cannot render a table from invalid run summaries:\n" + "\n".join(invalid))
    runs.sort(key=_order)
    lines = [_row(COLUMNS), _row(ALIGN)]
    lines += [_row(_cells(n, run)) for n, run in enumerate(runs, 1)]
    lines += ["", "Date, ClawBio commit and scope of each run:", ""]
    lines += [f"- Run {n}: {_md(_scope(run))}" for n, run in enumerate(runs, 1)]
    return "\n".join(lines) + "\n"


def _order(run):
    # By date, then model. The whole summary breaks ties, so equal keys never keep input order.
    model = run["model"]
    return run["date"], model["provider"], model["returned"], json.dumps(run, sort_keys=True)


def _cells(n: int, run) -> tuple:
    model, scope, tokens = run["model"], run["scope"], run["tokens"]
    return (n, run["date"], model["returned"], model["provider"], "yes" if model["open_weights"] else "no",
            ", ".join(scope["tiers"]), f"{scope['n_cases']} x {scope['n_repeats']}",
            f"{tokens['input']:,}", f"{tokens['output']:,}", _usd(run["cost"]),
            f"{run['wall_clock_seconds']:,.0f}")


def _scope(run) -> str:
    scope, task_set = run["scope"], run["task_set"]
    return (f"{run['date']}; ClawBio commit {run['clawbio_commit']}; "
            f"task set {task_set['version']} (sha256 {task_set['sha256'][:12]}); "
            f"tiers: {', '.join(scope['tiers'])}; "
            f"{_plural(scope['n_cases'], 'case')} x {_plural(scope['n_repeats'], 'repeat')}.")


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _usd(cost) -> str:
    if cost is None:
        return "local"
    usd = cost["usd"]
    return "<0.01" if 0 < usd < 0.005 else f"{usd:,.2f}"  # a run that cost something never shows 0.00


def _row(cells) -> str:
    return "| " + " | ".join(_md(cell) for cell in cells) + " |"


def _md(value) -> str:
    """Text on one line that cannot end a table cell early."""
    return " ".join(str(value).split()).replace("\\", "\\\\").replace("|", "\\|")


def load_summaries(directory) -> list[dict]:
    """Every *.summary.json directly in directory, sorted by file name.

    Not result.json: the root .gitignore drops that name at any depth, so a
    summary saved under it would never be committed. Files are read as UTF-8,
    which Windows does not assume. The index render_table gives a bad summary is
    its place in this order.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"no folder of run summaries at {directory}")
    # case_sensitive, so that Windows skips X.SUMMARY.JSON as Linux does.
    paths = sorted((p for p in directory.glob("*.summary.json", case_sensitive=True) if p.is_file()),
                   key=lambda p: p.name)
    summaries = []
    for path in paths:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:  # not UTF-8, or not JSON
            raise ValueError(f"{path.name} is not a readable JSON file: {e}") from e
        if not isinstance(summary, dict):
            raise ValueError(f"{path.name} must hold one JSON object")
        summaries.append(summary)
    return summaries
