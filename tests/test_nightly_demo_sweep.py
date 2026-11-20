import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

# Running a demo (argv handling, --output, failure modes) is tested in
# clawbio/common/tests/test_demo_runner.py; this file covers what the sweep adds.

SWEEP_PATH = Path(__file__).resolve().parents[1] / "scripts" / "nightly_demo_sweep.py"


def _load_sweep_module():
    spec = importlib.util.spec_from_file_location("nightly_demo_sweep", SWEEP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _capture_demo_runs(monkeypatch, sweep):
    seen = []

    def fake_run_demo(name, output_dir, *, timeout):
        assert Path(output_dir).is_dir()
        seen.append((name, Path(output_dir), timeout))
        return {"name": name, "passed": True}

    monkeypatch.setattr(sweep.demo_runner, "run_demo", fake_run_demo)
    return seen


def test_run_demo_gives_each_skill_its_own_dir_under_output(monkeypatch, tmp_path):
    sweep = _load_sweep_module()
    seen = _capture_demo_runs(monkeypatch, sweep)

    sweep.run_demo({"name": "vcf-annotator"}, timeout=30, output_dir=tmp_path)

    assert seen == [("vcf-annotator", tmp_path / "vcf-annotator", 30)]


def test_run_demo_without_output_dir_uses_a_temp_dir_and_removes_it(monkeypatch):
    sweep = _load_sweep_module()
    seen = _capture_demo_runs(monkeypatch, sweep)

    sweep.run_demo({"name": "x"}, timeout=30, output_dir=None)

    assert not seen[0][1].exists()


def _write_bundle(out: Path, files=("commands.sh", "environment.yml", "checksums.sha256")):
    (out / "reproducibility").mkdir(parents=True)
    for name in files:
        (out / "reproducibility" / name).write_text("x\n")


def test_run_demo_records_which_reproducibility_files_the_demo_left_out(monkeypatch, tmp_path):
    sweep = _load_sweep_module()

    def fake_run_demo(name, output_dir, *, timeout):
        _write_bundle(output_dir, files=("commands.sh",))
        return {"name": name, "passed": True}

    monkeypatch.setattr(sweep.demo_runner, "run_demo", fake_run_demo)

    result = sweep.run_demo({"name": "x"}, timeout=30, output_dir=tmp_path)

    assert result["contract_gaps"] == {
        "repro_files": "reproducibility/ is missing environment.yml, checksums.sha256"}


def test_run_demo_checks_the_contract_before_removing_a_temp_output_dir(monkeypatch):
    sweep = _load_sweep_module()

    def fake_run_demo(name, output_dir, *, timeout):
        _write_bundle(output_dir)
        return {"name": name, "passed": True}

    monkeypatch.setattr(sweep.demo_runner, "run_demo", fake_run_demo)

    result = sweep.run_demo({"name": "x"}, timeout=30, output_dir=None)

    assert result["contract_gaps"] == {}


def _result(name, gaps, passed=True):
    return {"name": name, "passed": passed, "contract_gaps": gaps}


def test_a_gap_not_listed_for_the_skill_fails():
    sweep = _load_sweep_module()

    problems = sweep.contract_regressions(
        [_result("new-skill", {"repro_files": "reproducibility/ is missing checksums.sha256"})], known={})

    assert problems == ["new-skill: reproducibility/ is missing checksums.sha256"]


def test_a_listed_gap_the_skill_now_passes_must_leave_the_table():
    sweep = _load_sweep_module()

    problems = sweep.contract_regressions([_result("fixed-skill", {})], known={"fixed-skill": {"repro_files"}})

    assert problems == ["fixed-skill: now passes repro_files; remove it from CONTRACT_KNOWN_GAPS"]


def test_listed_gaps_and_failed_demos_are_not_reported():
    sweep = _load_sweep_module()
    results = [_result("known-gap", {"repro_files": "missing"}), _result("crashed", {}, passed=False),
               _result("good", {})]

    assert sweep.contract_regressions(results, known={"known-gap": {"repro_files"}}) == []



def test_gaps_for_checks_the_sweep_does_not_run_are_left_to_their_own_tests():
    sweep = _load_sweep_module()

    problems = sweep.contract_regressions([_result("skill", {})], known={"skill": {"demo_flag"}})

    assert problems == []

def test_the_sweep_exits_nonzero_on_a_contract_gap_even_when_every_demo_passed(monkeypatch, tmp_path):
    sweep = _load_sweep_module()
    monkeypatch.setattr(sweep, "load_demo_skills", lambda: [{"name": "new-skill", "demo_command": "python x.py --demo"}])
    monkeypatch.setattr(sweep, "run_demo", lambda skill, timeout, output_dir: {
        "name": "new-skill", "command": "", "exit_code": 0, "elapsed": 0.0, "stderr_tail": "", "passed": True,
        "contract_gaps": {"repro_files": "reproducibility/ is missing commands.sh"}})
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["nightly_demo_sweep.py", "--output", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_info:
        sweep.main()

    assert exit_info.value.code == 1
    assert "new-skill: reproducibility/ is missing commands.sh" in (tmp_path / "sweep_summary.md").read_text()


# --- reading and writing files whatever the locale -----------------------------
#
# generate_catalog.py writes skills/catalog.json as UTF-8 and keeps non-ASCII
# text, such as xena-tcga-gene-query's Chinese trigger keywords. In UTF-8, 预后 is
# E9 A2 84 E5 90 8E: an ASCII locale cannot decode any of those bytes, and cp1252
# cannot decode 0x90.


@pytest.fixture
def c_locale(monkeypatch):
    """Run child Pythons in the C locale with locale coercion and UTF-8 mode off.

    That gives Python an ASCII locale encoding on Linux. Windows ignores LC_ALL
    and keeps its ANSI code page, such as cp1252.
    """
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PYTHONCOERCECLOCALE", "0")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)


# Loads the sweep from the path in argv[1] as `sweep`.
_LOAD_SWEEP = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("nightly_demo_sweep", sys.argv[1])
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)
"""


def _run_with_sweep(code: str, arg: Path) -> subprocess.CompletedProcess:
    """Run `code` in a child Python that has loaded the sweep as `sweep`, with `arg` as sys.argv[2]."""
    return subprocess.run(
        [sys.executable, "-c", _LOAD_SWEEP + code, str(SWEEP_PATH), str(arg)],
        cwd=SWEEP_PATH.parents[1],  # the child imports this checkout's clawbio
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_load_demo_skills_reads_the_catalog_as_utf8_whatever_the_locale(tmp_path, c_locale):
    catalog = tmp_path / "catalog.json"
    skill = {"name": "tcga-query", "trigger_keywords": ["生存分析", "预后"], "has_demo": True,
             "demo_command": "python -c pass"}
    catalog.write_text(json.dumps({"skills": [skill]}, ensure_ascii=False), encoding="utf-8")

    proc = _run_with_sweep("sweep.CATALOG = Path(sys.argv[2])\nprint(json.dumps(sweep.load_demo_skills()))", catalog)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [skill]


_any_text = st.text(max_size=8)
_catalog_skill = st.fixed_dictionaries({
    "name": _any_text,
    "description": _any_text,
    "trigger_keywords": st.lists(_any_text, max_size=3),
    "has_demo": st.booleans(),
    "demo_command": st.none() | _any_text,
})


@pytest.fixture
def sweep():
    """The sweep, loaded once for all the examples of a property."""
    return _load_sweep_module()


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(skills=st.lists(_catalog_skill, max_size=4))
# Found by Hypothesis under cp1252: U+0081 is C2 81 in UTF-8, and cp1252 cannot
# decode 0x81.
@example(skills=[{"name": "", "description": "\u0081", "trigger_keywords": [], "has_demo": False,
                  "demo_command": None}])
# cp1252 reads an em dash (E2 80 94) as "â€”", with no error.
@example(skills=[{"name": "x", "description": "\u2014", "trigger_keywords": [], "has_demo": True,
                  "demo_command": "python x.py --demo"}])
def test_load_demo_skills_returns_each_demo_skill_as_the_catalog_wrote_it(skills, sweep, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    # As generate_catalog.py writes it.
    catalog.write_text(json.dumps({"skills": skills}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(sweep, "CATALOG", catalog)

    assert sweep.load_demo_skills() == [s for s in skills if s["has_demo"] and s["demo_command"]]


def test_collect_output_genes_reads_skill_json_as_utf8_whatever_the_locale(tmp_path, c_locale):
    out = tmp_path / "out"
    (out / "tcga-query").mkdir(parents=True)
    (out / "tcga-query" / "result.json").write_text(
        json.dumps({"genes": ["TP53"], "note": "预后"}, ensure_ascii=False), encoding="utf-8")

    proc = _run_with_sweep("print(json.dumps(sweep.collect_output_genes(Path(sys.argv[2]))))", out)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"tcga-query": ["TP53"]}


def test_collect_output_genes_skips_a_json_file_that_is_not_utf8(tmp_path):
    sweep = _load_sweep_module()
    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "result.json").write_text(json.dumps({"genes": ["TP53"]}), encoding="utf-8")
    # Written in the Windows code page, so é is a lone 0xE9 byte, which is not UTF-8.
    (tmp_path / "skill" / "notes.json").write_bytes('{"genes": ["EGFR"], "note": "café"}'.encode("cp1252"))

    assert sweep.collect_output_genes(tmp_path) == {"skill": ["TP53"]}


# main() with one failed demo whose stderr tail holds 预后, written as escapes so
# that the code handed to the child stays ASCII.
_MAIN_WITH_A_FAILED_DEMO = """
sweep.load_demo_skills = lambda: [{"name": "tcga-query"}]
sweep.run_demo = lambda skill, timeout, output_dir: {
    "name": "tcga-query", "command": "", "exit_code": 1, "elapsed": 0.0,
    "stderr_tail": "KeyError: \\u9884\\u540e", "passed": False, "contract_gaps": {}}
sys.argv = ["nightly_demo_sweep.py", "--output", sys.argv[2]]
sweep.main()
"""


def test_the_summaries_are_written_as_utf8_whatever_the_locale(tmp_path, c_locale, monkeypatch):
    # Stdout is UTF-8 here so that main()'s prints succeed; this test is about the files.
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    step_summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))

    proc = _run_with_sweep(_MAIN_WITH_A_FAILED_DEMO, tmp_path / "out")

    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == 1  # the demo failed
    for summary in (tmp_path / "out" / "sweep_summary.md", step_summary):
        assert "KeyError: 预后" in summary.read_text(encoding="utf-8")
