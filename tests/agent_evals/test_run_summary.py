"""Tests for the model-run summaries and the result tables made from them.

AGENTS.md: never publish a benchmark figure without its date and scope. These
tests need no model and no network, and write only under pytest's temporary
folders.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st


def _load():
    path = Path(__file__).resolve().parent / "run_summary.py"
    spec = importlib.util.spec_from_file_location("clawbio_run_summary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()
REPO = Path(__file__).resolve().parents[2]
SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"

# A local run of an open-weights model, so there is no API cost.
LOCAL = {
    "date": "2027-01-29",
    "clawbio_commit": "9a35f8e",
    "harness_version": "0.1.0",
    "task_set": {"version": "1.0", "sha256": SHA},
    "scope": {"tiers": ["routing"], "n_cases": 160, "n_repeats": 5},
    "model": {"provider": "ollama", "requested": "open-model:8b", "returned": "open-model:8b",
              "open_weights": True, "quantization": "Q4_K_M", "digest": "0123456789ab"},
    "settings": {"sampling": "provider defaults"},
    "context_window": 32768,
    "hardware": {"gpu": "NVIDIA GeForce RTX 4080", "cpu": "AMD Ryzen 9 7950X", "memory_gb": 64},
    "tokens": {"input": 14_000_000, "output": 600_000},
    "cost": None,
    "wall_clock_seconds": 14400.4,
}
# A hosted proprietary model, priced from a table passed in at run time.
HOSTED = {
    "date": "2027-01-30",
    "clawbio_commit": "9a35f8e72afaae4f47a89571836bbdb0065ea479",
    "harness_version": "0.1.0",
    "task_set": {"version": "1.0", "sha256": SHA},
    "scope": {"tiers": ["routing", "agentic"], "n_cases": 184, "n_repeats": 1},
    "model": {"provider": "openai", "requested": "hosted-model-2026-12-01",
              "returned": "hosted-model-2026-12-01", "open_weights": False},
    "settings": {"sampling": "provider defaults"},
    "context_window": None,
    "hardware": {"gpu": None, "cpu": "Apple M3 Pro", "memory_gb": 36},
    "tokens": {"input": 2_800_000, "output": 120_000},
    "cost": {"usd": 9.1, "price_source": "vendor price page", "price_date": "2027-01-28"},
    "wall_clock_seconds": 5400,
}


def _with(summary, path, value):
    """A copy of summary with a dotted field set; a missing or null parent becomes an object."""
    summary = deepcopy(summary)
    *parents, key = path.split(".")
    node = summary
    for name in parents:
        if not isinstance(node.get(name), dict):
            node[name] = {}
        node = node[name]
    node[key] = value
    return summary


def _without(summary, path):
    summary = deepcopy(summary)
    *parents, key = path.split(".")
    node = summary
    for name in parents:
        node = node[name]
    del node[key]
    return summary


def _names(problems, path):
    """Some problem is about this field."""
    return any(p.startswith(f"{path} ") for p in problems)


def _save(path, summary):
    path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")


# --- validate_summary: examples for each rule -----------------------------------

def test_a_complete_local_run_and_a_complete_hosted_run_have_no_problems():
    assert MOD.validate_summary(deepcopy(LOCAL)) == []
    assert MOD.validate_summary(deepcopy(HOSTED)) == []


REQUIRED = [
    "date", "clawbio_commit", "harness_version",
    "task_set", "task_set.version", "task_set.sha256",
    "scope", "scope.tiers", "scope.n_cases", "scope.n_repeats",
    "model", "model.provider", "model.requested", "model.returned", "model.open_weights",
    "settings", "context_window",
    "hardware", "hardware.gpu", "hardware.cpu", "hardware.memory_gb",
    "tokens", "tokens.input", "tokens.output",
    "cost", "cost.usd", "cost.price_source", "cost.price_date",
    "wall_clock_seconds",
]


@pytest.mark.parametrize("path", REQUIRED)
def test_a_missing_field_is_a_problem_that_names_it(path):
    # HOSTED, so that cost is an object with fields of its own.
    assert _names(MOD.validate_summary(_without(HOSTED, path)), path)


BAD = [
    ("date", "19-11-2026"),
    ("date", "2026-02-30"),  # no such day
    ("date", "20261119"),  # date.fromisoformat accepts this basic format
    ("date", "2026-W47-4"),  # and ISO week dates
    ("date", "2026-11-19T10:00"),
    ("date", "2026-11-19\n"),
    ("date", 20261119),
    ("date", None),
    ("clawbio_commit", "9a35f8"),  # 6 characters
    ("clawbio_commit", "9" * 41),
    ("clawbio_commit", "9a35f8g"),
    ("clawbio_commit", "9A35F8E"),  # git prints lowercase
    ("clawbio_commit", "HEAD"),
    ("harness_version", ""),
    ("harness_version", " "),
    ("harness_version", 1),
    ("task_set", "1.0"),
    ("task_set.version", ""),
    ("task_set.sha256", "0" * 63),
    ("task_set.sha256", "g" * 64),
    ("scope", None),
    ("scope.tiers", []),
    ("scope.tiers", "routing"),
    ("scope.tiers", ["routing", ""]),
    ("scope.tiers", ["routing", 3]),
    ("scope.n_cases", 0),
    ("scope.n_cases", 160.0),
    ("scope.n_cases", True),  # JSON true is not a count
    ("scope.n_cases", "160"),
    ("scope.n_repeats", -1),
    ("model", ["open-model:8b"]),
    ("model.provider", ""),
    ("model.requested", None),
    ("model.returned", 3),
    ("model.open_weights", "yes"),
    ("model.open_weights", 1),
    ("model.open_weights", None),
    ("model.quantization", ""),
    ("model.digest", 7),
    ("settings", None),
    ("settings", ["temperature", 0]),
    ("context_window", "32k"),
    ("context_window", 0),
    ("context_window", 32768.0),
    ("context_window", True),
    ("hardware", "RTX 4080"),
    ("hardware", None),
    ("hardware.gpu", ""),
    ("hardware.gpu", 4080),
    ("hardware.memory_gb", 0),
    ("hardware.memory_gb", -16),
    ("hardware.memory_gb", "64"),
    ("tokens", 0),
    ("tokens.input", -1),
    ("tokens.output", 1.5),
    ("tokens.output", None),
    ("cost", "free"),
    ("cost.usd", -0.01),
    ("cost.usd", "9.10"),
    ("cost.price_source", ""),
    ("cost.price_date", "January 2027"),
    ("wall_clock_seconds", -1),
    ("wall_clock_seconds", "1h"),
    ("wall_clock_seconds", None),
]


@pytest.mark.parametrize("path, value", BAD)
def test_a_bad_value_is_a_problem_that_names_its_field(path, value):
    assert _names(MOD.validate_summary(_with(HOSTED, path, value)), path)


NUMBERS = ["scope.n_cases", "scope.n_repeats", "context_window", "hardware.memory_gb",
           "tokens.input", "tokens.output", "cost.usd", "wall_clock_seconds"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("path", NUMBERS)
def test_nan_and_infinity_are_never_numbers(path, value):
    """json.loads reads NaN and Infinity, and inf >= 0 is True."""
    assert _names(MOD.validate_summary(_with(HOSTED, path, value)), path)


@pytest.mark.parametrize("field", ["quantization", "digest"])
def test_quantization_and_digest_may_be_left_out_or_null(field):
    assert MOD.validate_summary(_without(LOCAL, f"model.{field}")) == []
    assert MOD.validate_summary(_with(LOCAL, f"model.{field}", None)) == []


def test_cost_may_be_null_only_for_an_open_weights_model():
    """A null cost means a local run, and a proprietary model does not run locally."""
    assert _names(MOD.validate_summary(_with(HOSTED, "cost", None)), "cost")


@pytest.mark.parametrize("key, value", [
    ("gpu", "/dev/nvidia0"),
    ("cpu", "jdoe@workstation"),
    ("cpu", "C:\\Users\\jdoe"),
    ("gpu", "\\\\buildbox\\share"),
    # CPU brand strings carry the clock after an at sign; record the model only.
    ("cpu", "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz"),
])
def test_hardware_that_looks_like_a_path_host_or_user_is_rejected_without_quoting_it(key, value):
    problems = MOD.validate_summary(_with(LOCAL, f"hardware.{key}", value))
    assert _names(problems, f"hardware.{key}")
    assert not any(value in p for p in problems)


@pytest.mark.parametrize("key", ["hostname", "user", "os"])
def test_hardware_holds_only_gpu_cpu_and_memory(key):
    assert _names(MOD.validate_summary(_with(LOCAL, f"hardware.{key}", "x")), "hardware")


@pytest.mark.parametrize("summary", [None, [], "2027-01-29", 3])
def test_a_summary_that_is_not_an_object_is_a_problem_not_a_crash(summary):
    assert MOD.validate_summary(summary) != []


@pytest.mark.parametrize("value", [float("nan"), {1, 2}, Path("x")])
def test_settings_must_be_plain_json(value):
    """Settings are free-form, but the saved file must be strict JSON."""
    problems = MOD.validate_summary(_with(LOCAL, "settings.extra", value))
    assert _names(problems, "summary")


# --- render_table -------------------------------------------------------------

EXPECTED = """\
| Run | Date | Model | Provider | Open weights | Tiers | Cases x repeats | Input tokens | Output tokens | Cost (USD) | Wall clock (s) |
| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 2027-01-29 | open-model:8b | ollama | yes | routing | 160 x 5 | 14,000,000 | 600,000 | local | 14,400 |
| 2 | 2027-01-30 | hosted-model-2026-12-01 | openai | no | routing, agentic | 184 x 1 | 2,800,000 | 120,000 | 9.10 | 5,400 |

Date, ClawBio commit and scope of each run:

- Run 1: 2027-01-29; ClawBio commit 9a35f8e; task set 1.0 (sha256 0f1e2d3c4b5a); tiers: routing; 160 cases x 5 repeats.
- Run 2: 2027-01-30; ClawBio commit 9a35f8e72afaae4f47a89571836bbdb0065ea479; task set 1.0 (sha256 0f1e2d3c4b5a); tiers: routing, agentic; 184 cases x 1 repeat.
"""


def test_the_table_states_each_runs_date_commit_and_scope():
    runs = [deepcopy(HOSTED), deepcopy(LOCAL)]
    assert MOD.render_table(runs) == EXPECTED
    assert runs == [HOSTED, LOCAL]  # the caller's list keeps its order


def test_render_table_refuses_an_invalid_summary_and_says_which():
    with pytest.raises(ValueError, match=r"summaries\[1\]: date is missing"):
        MOD.render_table([HOSTED, _without(LOCAL, "date")])


def test_render_table_refuses_an_empty_list():
    with pytest.raises(ValueError, match="no run summaries"):
        MOD.render_table([])


@pytest.mark.parametrize("usd, shown", [(0, "0.00"), (0.004, "<0.01"), (0.006, "0.01"), (1234.5, "1,234.50")])
def test_a_cost_below_a_cent_is_not_shown_as_zero(usd, shown):
    row = MOD.render_table([_with(HOSTED, "cost.usd", usd)]).splitlines()[2]
    assert f"| {shown} |" in row


def _cells(line):
    return re.split(r"(?<!\\)\|", line)


def test_a_pipe_or_newline_in_a_model_name_cannot_break_the_table():
    header, _, row = MOD.render_table([_with(LOCAL, "model.returned", "open|model\n:8b")]).splitlines()[:3]
    assert len(_cells(row)) == len(_cells(header))


# --- load_summaries -------------------------------------------------------------

def test_load_summaries_reads_only_summary_files_in_file_name_order(tmp_path):
    for name in ("b.summary.json", "a.summary.json", "result.json", "a.json",
                 "c.summary.json.bak", "D.SUMMARY.JSON"):
        _save(tmp_path / name, {"file": name})
    (tmp_path / "sub").mkdir()
    _save(tmp_path / "sub" / "c.summary.json", {"file": "sub/c.summary.json"})
    (tmp_path / "folder.summary.json").mkdir()
    assert [s["file"] for s in MOD.load_summaries(tmp_path)] == ["a.summary.json", "b.summary.json"]


def test_load_summaries_reads_utf8_whatever_the_locale(tmp_path):
    """Windows reads text as cp1252 unless told otherwise, and a C locale reads ASCII.

    The child Python runs with UTF-8 mode off, so this fails on Windows and on
    Linux alike if load_summaries stops passing encoding="utf-8".
    """
    run = _with(HOSTED, "cost.price_source", "liste de prix r\u00e9vis\u00e9e")
    _save(tmp_path / "run.summary.json", run)
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"}
    script = ("import importlib.util, json, sys\n"
              "spec = importlib.util.spec_from_file_location('run_summary', sys.argv[1])\n"
              "module = importlib.util.module_from_spec(spec)\n"
              "spec.loader.exec_module(module)\n"
              "print(json.dumps(module.load_summaries(sys.argv[2])))\n")
    # -B: no __pycache__ next to the module under test.
    proc = subprocess.run([sys.executable, "-B", "-c", script, MOD.__file__, str(tmp_path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [run]


@pytest.mark.parametrize("content", [b"{", b"[]", b"\xff{}"])
def test_load_summaries_names_a_file_that_is_not_one_json_object(tmp_path, content):
    (tmp_path / "broken.summary.json").write_bytes(content)
    with pytest.raises(ValueError, match=r"broken\.summary\.json"):
        MOD.load_summaries(tmp_path)


def test_load_summaries_refuses_a_folder_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        MOD.load_summaries(tmp_path / "2027-01-29")


def test_loaded_summaries_render_the_same_table(tmp_path):
    _save(tmp_path / "hosted.summary.json", HOSTED)
    _save(tmp_path / "local.summary.json", LOCAL)
    assert MOD.render_table(MOD.load_summaries(tmp_path)) == EXPECTED


def test_summaries_under_bench_runs_models_are_not_gitignored():
    """Summaries go in bench_runs/models/<date>/; an ignore rule there would drop them silently."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("needs git")
    proc = subprocess.run([git, "-C", str(REPO), "check-ignore", "--no-index", "-q",
                           "bench_runs/models/2027-01-29/open-model.summary.json"], capture_output=True)
    if proc.returncode == 128:
        pytest.skip("not a git checkout")
    assert proc.returncode == 1  # 0 means ignored


# --- properties -----------------------------------------------------------------

HEX = "0123456789abcdef"
DATES = st.dates(date(2026, 1, 1), date(2030, 12, 31)).map(date.isoformat)
MODELS = st.sampled_from(["open-model:8b", "open-model:32b", "hosted-model-2026-12-01", "Org/Model-7B"])
CHIPS = st.sampled_from(["NVIDIA GeForce RTX 4080", "NVIDIA A100-SXM4-80GB", "Apple M3 Pro",
                         "AMD Ryzen 9 7950X", "Intel(R) Core(TM) i7-13700H"])


@st.composite
def summaries(draw):
    """Valid summaries. Dates and models come from small sets, so runs often tie on them."""
    open_weights = draw(st.booleans())
    model = {"provider": draw(st.sampled_from(["ollama", "openai", "google"])),
             "requested": draw(MODELS), "returned": draw(MODELS), "open_weights": open_weights}
    for optional in ("quantization", "digest"):
        if draw(st.booleans()):
            model[optional] = draw(st.none() | st.sampled_from(["Q4_K_M", "0123456789ab"]))
    local = open_weights and draw(st.booleans())
    return {
        "date": draw(st.sampled_from(["2027-01-29", "2027-01-30"]) | DATES),
        "clawbio_commit": draw(st.text(HEX, min_size=7, max_size=40)),
        "harness_version": draw(st.sampled_from(["0.1.0", "0.2.0"])),
        "task_set": {"version": draw(st.sampled_from(["1.0", "1.1"])),
                     "sha256": draw(st.text(HEX, min_size=64, max_size=64))},
        "scope": {"tiers": draw(st.lists(st.from_regex(r"[a-z][a-z0-9_-]{0,11}", fullmatch=True),
                                         min_size=1, max_size=3)),
                  "n_cases": draw(st.integers(1, 400)), "n_repeats": draw(st.integers(1, 5))},
        "model": model,
        "settings": draw(st.dictionaries(st.sampled_from(["sampling", "tools", "temperature"]),
                                         st.none() | st.booleans() | st.floats(0, 2) | st.text(max_size=5),
                                         max_size=2)),
        "context_window": draw(st.none() | st.integers(1, 2**20)),
        "hardware": {"gpu": draw(st.none() | CHIPS), "cpu": draw(st.none() | CHIPS),
                     "memory_gb": draw(st.none() | st.integers(1, 1024) | st.floats(0.5, 1024))},
        "tokens": {"input": draw(st.integers(0, 10**9)), "output": draw(st.integers(0, 10**8))},
        "cost": None if local else {"usd": draw(st.floats(0, 1000)),
                                    "price_source": draw(st.sampled_from(["vendor price page", "prices.csv"])),
                                    "price_date": draw(DATES)},
        "wall_clock_seconds": draw(st.integers(0, 10**6) | st.floats(0, 10**6)),
    }


LOSSES = {
    "no date": lambda s: s.pop("date"),
    "blank date": lambda s: s.update(date=""),
    "no scope": lambda s: s.pop("scope"),
    "scope not an object": lambda s: s.update(scope="routing"),
    "no tiers": lambda s: s["scope"].pop("tiers"),
    "empty tiers": lambda s: s["scope"].update(tiers=[]),
    "no case count": lambda s: s["scope"].pop("n_cases"),
    "no repeat count": lambda s: s["scope"].pop("n_repeats"),
}


@given(runs=st.lists(summaries(), min_size=1, max_size=4), at=st.integers(0, 3),
       loss=st.sampled_from(sorted(LOSSES)))
def test_a_run_without_its_date_or_scope_is_always_rejected(runs, at, loss):
    broken = runs[at % len(runs)]
    assert MOD.validate_summary(broken) == []
    LOSSES[loss](broken)
    assert any(p.startswith(("date ", "scope")) for p in MOD.validate_summary(broken))
    with pytest.raises(ValueError):
        MOD.render_table(runs)


def _states(line, run):
    """A caption line states the run's date, commit, tiers, case count and repeat count."""
    scope = run["scope"]
    return (run["date"] in line and run["clawbio_commit"] in line and ", ".join(scope["tiers"]) in line
            and re.search(rf"\b{scope['n_cases']} cases?\b", line) is not None
            and re.search(rf"\b{scope['n_repeats']} repeats?\b", line) is not None)


@given(st.lists(summaries(), min_size=1, max_size=5))
def test_the_table_always_states_every_runs_date_commit_and_scope(runs):
    lines = MOD.render_table(runs).splitlines()
    rows = [line for line in lines if line.startswith("|")][2:]
    caption = [line for line in lines if line.startswith("- Run ")]
    assert len(rows) == len(caption) == len(runs)
    for row, line in zip(rows, caption):
        number, day = [cell.strip() for cell in row.strip("|").split("|")][:2]
        assert line.startswith(f"- Run {number}: {day};")  # each row has the caption line of its run
    for run in runs:
        assert any(_states(line, run) for line in caption), run


TWIN = _with(LOCAL, "tokens.input", LOCAL["tokens"]["input"] + 1)  # same date and model, other figures


@given(st.lists(summaries(), min_size=1, max_size=5).flatmap(
    lambda runs: st.tuples(st.just(runs), st.permutations(runs))))
@example(([LOCAL, TWIN], [TWIN, LOCAL]))
def test_the_table_does_not_depend_on_input_order(runs_and_shuffled):
    runs, shuffled = runs_and_shuffled
    assert MOD.render_table(runs) == MOD.render_table(shuffled)


@given(summary=summaries(), key=st.sampled_from(["gpu", "cpu", "memory_gb"]), before=st.text(max_size=8),
       mark=st.sampled_from(["/", "\\", "@"]), after=st.text(max_size=8))
@example(summary=LOCAL, key="cpu", before="Intel(R) Core(TM) i7-8700K CPU ", mark="@", after=" 3.70GHz")
@example(summary=LOCAL, key="gpu", before="", mark="/", after="")
def test_hardware_values_with_path_host_or_user_marks_are_always_rejected(summary, key, before, mark, after):
    value = before + mark + after
    problems = MOD.validate_summary(_with(summary, f"hardware.{key}", value))
    assert _names(problems, f"hardware.{key}")
    assert not any(value in p for p in problems)  # the problem must not repeat it


OTHER_KEYS = st.text(min_size=1, max_size=12).filter(lambda key: key not in ("gpu", "cpu", "memory_gb"))


@given(summary=summaries(), key=OTHER_KEYS, value=st.none() | st.text(max_size=8) | st.integers())
def test_hardware_with_any_other_key_is_always_rejected(summary, key, value):
    summary = deepcopy(summary)
    summary["hardware"][key] = value
    assert _names(MOD.validate_summary(summary), "hardware")


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
    max_leaves=6,
)


@given(summary=summaries(), path=st.sampled_from(REQUIRED + ["model.quantization", "model.digest"]), value=JSON)
@example(summary=LOCAL, path="wall_clock_seconds", value=10**400)  # too large for a float
def test_validate_summary_returns_problems_instead_of_raising(summary, path, value):
    problems = MOD.validate_summary(_with(summary, path, value))
    assert isinstance(problems, list) and all(isinstance(p, str) for p in problems)


@given(JSON)
def test_a_value_that_is_not_a_summary_is_rejected_without_raising(value):
    assert MOD.validate_summary(value) != []


@settings(deadline=None)  # file writes can be slow on Windows
@given(names=st.lists(st.from_regex(r"[a-z0-9_-]{1,8}", fullmatch=True), unique=True, max_size=5))
def test_load_summaries_returns_files_in_name_order_whatever_order_they_were_written(tmp_path_factory, names):
    folder = tmp_path_factory.mktemp("summaries")
    for name in names:
        _save(folder / f"{name}.summary.json", {"name": name})
    expected = sorted(names, key=lambda name: f"{name}.summary.json")
    assert [s["name"] for s in MOD.load_summaries(folder)] == expected
