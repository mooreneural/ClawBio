"""Tests for the routing tier of the model evaluation.

They call no model and use no network: model replies come from a fake provider
object with a generate method. They read only the frozen synthetic fixtures in
fixtures/routing_tier/, never the live catalog, SKILL.md files or AGENTS.md, so a
skill PR cannot break them. The fixture cases, catalog entries, allowlists and
rules are written for these tests; the catalog descriptions and flag sets are
made up and do not describe the real skills. The keyword-baseline tests use a
fake router, except one that checks only the shape of the real router's answer.
"""
import functools
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "routing_tier"


def _load():
    path = HERE / "routing_tier.py"
    spec = importlib.util.spec_from_file_location("clawbio_routing_tier", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass reads sys.modules[cls.__module__]
    spec.loader.exec_module(module)
    return module


MOD = _load()
CATALOG = json.loads((FIXTURES / "skills" / "catalog.json").read_text(encoding="utf-8"))
NAMES = {s["name"] for s in CATALOG["skills"]}
CLI = json.loads((FIXTURES / "cli_skills.json").read_text(encoding="utf-8"))
CASES_FILE = FIXTURES / "cases.jsonl"


@functools.cache
def _rules():
    """The command rules of the fixture catalog and the synthetic allowlists."""
    return MOD.command_rules(CATALOG, CLI["skills"], nfcore_aliases=CLI["nfcore_aliases"])


def _case(**changes):
    case = {"id": "c1", "request": "Which of my medicines might not suit me?",
            "expected_skill": "pharmgx-reporter", "forbidden_skills": [], "kind": "positive",
            "source": "synthetic"}
    case.update(changes)
    return case


def _write_jsonl(path, rows):
    # Raw UTF-8, as a hand-written cases file holds it; json.dumps would escape it to ASCII.
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


# --- case schema and loader -------------------------------------------------


def test_a_valid_case_has_no_problems():
    assert MOD.validate_case(_case()) == []
    assert MOD.validate_case(_case(expected_skill=None, kind="out_of_scope")) == []
    assert MOD.validate_case(_case(expected_skill=None, forbidden_skills=["genome-compare"],
                                   kind="injection")) == []


@pytest.mark.parametrize("field", ["id", "request", "expected_skill", "forbidden_skills", "kind", "source"])
def test_every_field_is_required(field):
    case = _case()
    del case[field]
    problems = MOD.validate_case(case)
    assert any(p.startswith(field) for p in problems), problems


@pytest.mark.parametrize("changes, field", [
    ({"id": ""}, "id"),
    ({"id": 3}, "id"),
    ({"request": "   "}, "request"),
    ({"kind": "tricky"}, "kind"),
    ({"source": ""}, "source"),
    ({"expected_skill": ""}, "expected_skill"),
    ({"expected_skill": "Pharmgx Reporter"}, "expected_skill"),
    ({"forbidden_skills": "equity-scorer"}, "forbidden_skills"),
    ({"forbidden_skills": [None]}, "forbidden_skills"),
    ({"forbidden_skills": ["equity-scorer", "equity-scorer"]}, "forbidden_skills"),
    ({"expected_skill": "equity-scorer", "forbidden_skills": ["equity-scorer"]}, "forbidden_skills"),
    ({"expected_skill": None}, "expected_skill"),  # a positive case names its skill
    ({"kind": "out_of_scope"}, "expected_skill"),  # out of scope means no skill should fire
    ({"kind": "confusable"}, "forbidden_skills"),  # confusable names the skill it resembles
])
def test_bad_values_are_reported_by_field(changes, field):
    problems = MOD.validate_case(_case(**changes))
    assert any(p.startswith(field) for p in problems), problems


def test_a_case_that_is_not_an_object_is_one_problem():
    assert MOD.validate_case(["not", "a", "case"]) == ["case must be a JSON object"]


def test_skill_names_are_checked_against_the_catalog_when_given():
    case = _case(expected_skill="no-such-skill", forbidden_skills=["also-missing"])
    assert MOD.validate_case(case) == []
    problems = MOD.validate_case(case, skill_names=NAMES)
    assert any(p.startswith("expected_skill") and "no-such-skill" in p for p in problems)
    assert any(p.startswith("forbidden_skills") and "also-missing" in p for p in problems)


def test_the_fixture_cases_load_and_cover_every_kind():
    cases = MOD.load_cases(CASES_FILE, skill_names=NAMES)
    assert len(cases) == 12
    assert {c["kind"] for c in cases} == set(MOD.KINDS)
    assert all(c["source"] == "synthetic" for c in cases)


def test_load_cases_rejects_duplicate_ids(tmp_path):
    path = _write_jsonl(tmp_path / "cases.jsonl", [_case(), _case(request="Another request")])
    with pytest.raises(ValueError, match=r"line 2: id c1 is used twice"):
        MOD.load_cases(path)


def test_load_cases_names_the_line_of_a_bad_case(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_case()) + "\n\n{not json\n" + json.dumps(_case(id="c3", kind="odd")) + "\n",
                    encoding="utf-8")
    with pytest.raises(ValueError) as err:
        MOD.load_cases(path)
    assert "line 3: not JSON" in str(err.value)
    assert "line 4: kind" in str(err.value)


def test_load_cases_skips_blank_lines_and_refuses_an_empty_file(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text("\n" + json.dumps(_case()) + "\n\n", encoding="utf-8")
    assert [c["id"] for c in MOD.load_cases(path)] == ["c1"]
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no cases"):
        MOD.load_cases(path)


def test_load_cases_reads_utf8_whatever_the_locale(tmp_path):
    """Windows reads text as cp1252 unless told otherwise, and a C locale reads ASCII."""
    case = _case(request="Mon génotype dit-il quelque chose sur la caféine ≥ 200 mg ?")
    path = _write_jsonl(tmp_path / "cases.jsonl", [case])
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"}
    script = ("import importlib.util, json, sys\n"
              "spec = importlib.util.spec_from_file_location('routing_tier', sys.argv[1])\n"
              "module = importlib.util.module_from_spec(spec)\n"
              "sys.modules[spec.name] = module\n"
              "spec.loader.exec_module(module)\n"
              "print(json.dumps(module.load_cases(sys.argv[2])))\n")
    proc = subprocess.run([sys.executable, "-B", "-c", script, MOD.__file__, str(path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [case]


# --- prompt -----------------------------------------------------------------


def test_routing_rules_takes_the_routing_sections_only():
    text = (FIXTURES / "routing_rules.md").read_text(encoding="utf-8")
    rules = MOD.routing_rules(text)
    assert rules.startswith("## Skill Routing")
    assert "Never invent an alias or a flag." in rules
    assert "With no input file, run the skill's demo." in rules
    assert "Code Style" not in rules and "Synthetic rules" not in rules


def test_routing_rules_refuses_text_without_a_routing_section():
    with pytest.raises(ValueError, match="Skill Routing"):
        MOD.routing_rules("# Title\n\n## Running a Demo\n\nRun it.\n")


def test_build_prompt_holds_rules_index_and_answer_format():
    system, user = MOD.build_prompt("INDEX-TEXT", "_rules()-TEXT", "Downsample my reads, please.")
    assert "_rules()-TEXT" in system and "INDEX-TEXT" in system
    assert '"skill"' in system and '"command"' in system and "null" in system
    assert "Downsample my reads, please." in user
    assert "Downsample my reads" not in system  # the system text is the same for every case
    assert MOD.build_prompt("INDEX-TEXT", "_rules()-TEXT", "Downsample my reads, please.") == (system, user)


# --- parse_answer -------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ('{"skill": "nutrigx", "command": "python clawbio.py run nutrigx --demo"}',
     {"skill": "nutrigx", "command": "python clawbio.py run nutrigx --demo"}),
    ('{"skill": null, "command": null}', {"skill": None, "command": None}),
    ('{"skill": "dnasp"}', {"skill": "dnasp", "command": None}),
    ('```json\n{"skill": "dnasp", "command": null}\n```', {"skill": "dnasp", "command": None}),
    ('```\n{"skill": "dnasp", "command": null}\n```', {"skill": "dnasp", "command": None}),
    ('<think>Maybe pharmgx-reporter? No.</think>\n{"skill": "clinpgx", "command": null}',
     {"skill": "clinpgx", "command": None}),
    ('Here is my answer:\n{"skill": "clinpgx", "command": "python clawbio.py run clinpgx --gene CYP2C19"}\nDone.',
     {"skill": "clinpgx", "command": "python clawbio.py run clinpgx --gene CYP2C19"}),
    ('{"skill": "equity-scorer", "command": "echo {a} }", "why": {"x": 1}}',
     {"skill": "equity-scorer", "command": "echo {a} }"}),
])
def test_parse_answer_reads_the_json_object(text, expected):
    assert MOD.parse_answer(text) == expected


@pytest.mark.parametrize("text, reason", [
    ("", "empty"),
    ("I would use pharmgx-reporter for this.", "no JSON object"),
    ("The skill is `nutrigx`.", "no JSON object"),
    ('{"skill": "nutrigx", "command": null', "no JSON object"),
    ('{"skill": "nutrigx"} {"skill": "dnasp"}', "more than one"),
    # A fenced answer followed by a hedged second answer is still two answers.
    ('```json\n{"skill": "dnasp", "command": null}\n```\nOr maybe {"skill": "nutrigx", "command": null}', "more than one"),
    ('["nutrigx"]', "no JSON object"),
    ('{"command": "python clawbio.py run nutrigx --demo"}', "skill"),
    ('{"skill": 3, "command": null}', "skill"),
    ('{"skill": "", "command": null}', "skill"),
    ('{"skill": "Nutri GX", "command": null}', "skill"),
    ('{"skill": "nutr\\u0069gx", "command": null}', "skill"),
    ('{"skill": "nutrigx", "command": 7}', "command"),
    ("<think>still thinking about nutrigx", "think"),
    # The name must be in the answer itself, not only in the reasoning before it.
    ('<think>nutrigx</think>{"skill": "nutr\\u0069gx", "command": null}', "skill"),
])
def test_parse_answer_marks_what_it_cannot_read(text, reason):
    answer = MOD.parse_answer(text)
    assert set(answer) == {"malformed"}
    assert reason in answer["malformed"]


def test_parse_answer_never_guesses_a_skill_from_prose():
    answer = MOD.parse_answer("Use nutrigx. The JSON would be skill nutrigx and command none.")
    assert "skill" not in answer


# --- commands -----------------------------------------------------------------


def test_command_rules_join_catalog_aliases_and_allowlists():
    assert _rules()["equity-scorer"] == MOD.SkillCommand(
        alias="equity", flags=frozenset({"--weights", "--pop-map"}), snake_case=False)
    assert _rules()["nfcore-sarek-wrapper"].snake_case is True
    assert "--check" in _rules()["nfcore-sarek-wrapper"].flags  # a flag without a value counts too
    assert _rules()["vcf-annotator"] == MOD.SkillCommand(alias=None, flags=None, snake_case=False)


@pytest.mark.parametrize("skill, command", [
    ("equity-scorer", "python clawbio.py run equity --input cohort.vcf --pop-map pops.csv --output /tmp/eq"),
    ("equity-scorer", "python clawbio.py run equity-scorer --demo"),  # the folder name resolves too
    ("equity-scorer", "uv run python clawbio.py run equity --weights=w.json --demo"),
    ("equity-scorer", "python3 ./clawbio.py run equity --demo"),
    ("equity-scorer", "clawbio run equity --demo"),
    ("seqtk-downsample", "python clawbio.py run seqtk-downsample --input r1.fq --input2 r2.fq --n-reads 100000 --seed 7"),
    ("nfcore-sarek-wrapper", "python clawbio.py run sarek-pipeline --input s.csv --skip_tools baserecalibrator --check"),
    ("vcf-annotator", "python skills/vcf-annotator/vcf_annotator.py --input my.vcf --output /tmp/ann"),
    ("vcf-annotator", "uv run --extra vep python skills/vcf-annotator/vcf_annotator.py --demo"),
    ("vcf-annotator", "Rscript skills/vcf-annotator/annotate.R --demo"),  # one catalog demo is an R script
    ("vcf-annotator", "python skills/vcf-annotator/scripts/annotate.py --demo"),  # one is in a subfolder
    # A request with no file gets placeholder paths; they are not redirects.
    ("equity-scorer", "python clawbio.py run equity --input <file> --output <dir>"),
    ("equity-scorer", "python clawbio.py run equity --input <cohort>.vcf --output <output_dir>"),
    ("vcf-annotator", "python skills/vcf-annotator/vcf_annotator.py --input <my.vcf> --output <dir>"),
    (None, None),
])
def test_valid_commands(skill, command):
    assert MOD.check_command(skill, command, _rules()) is None


@pytest.mark.parametrize("skill, command, problem", [
    ("equity-scorer", None, "no command"),
    ("equity-scorer", "   ", "no command"),
    (None, "python clawbio.py run equity --demo", "without a skill"),
    ("equity-scorer", "python clawbio.py run equity --demo --threads 8", "--threads"),
    ("equity-scorer", "python clawbio.py run equity --threads=8 --demo", "--threads"),
    ("equity-scorer", "python clawbio.py run equity --pop_map p.csv", "--pop_map"),  # snake case only for nf-core
    ("equity-scorer", "python clawbio.py run nutrigx --demo", "names nutrigx"),
    ("equity-scorer", "python clawbio.py run equity --demo && rm -rf /tmp/x", "one command"),
    ("equity-scorer", "python clawbio.py run equity --demo | tail", "one command"),
    ("equity-scorer", "python clawbio.py run equity --demo; ls", "one command"),
    ("equity-scorer", "python clawbio.py run equity --demo > out.txt", "one command"),
    ("equity-scorer", "python clawbio.py run equity --demo >out.txt", "one command"),
    ("equity-scorer", "python clawbio.py run equity --input < in.vcf --demo", "one command"),
    ("equity-scorer", "python clawbio.py run equity --input <file> --demo && ls", "one command"),
    ("equity-scorer", "python clawbio.py list", "not a skill run"),
    ("equity-scorer", "python clawbio.py run", "not a skill run"),
    ("equity-scorer", "bash skills/equity-scorer/run.sh", "not a ClawBio command"),
    ("equity-scorer", "python skills/equity-scorer/../vcf-annotator/vcf_annotator.py", "not a ClawBio command"),
    ("equity-scorer", "python skills/equity-scorer/./equity_scorer.py", "not a ClawBio command"),
    ("equity-scorer", "python skills/equity-scorer/run.R", "not a ClawBio command"),
    ("equity-scorer", "Rscript skills/equity-scorer/equity_scorer.py", "not a ClawBio command"),
    ("equity-scorer", "Rscript clawbio.py run equity --demo", "not a ClawBio command"),
    ("equity-scorer", "python clawbio.py run equity --demo 'unclosed", "not a shell command"),
    ("vcf-annotator", "python clawbio.py run vcf-annotator --demo", "no clawbio.py run entry"),
    ("vcf-annotator", "python skills/equity-scorer/equity_scorer.py --demo", "names equity-scorer"),
    ("vcf-annotator", "python skills/../clawbio.py run equity --demo", "not a ClawBio command"),
    ("not-in-catalog", "python clawbio.py run not-in-catalog --demo", "not a catalog skill"),
])
def test_invalid_commands(skill, command, problem):
    found = MOD.check_command(skill, command, _rules())
    assert found is not None and problem in found, found


def test_cli_command_rules_read_the_allowlist_from_clawbio_cli_without_changing_it():
    """The live allowlist is read by importing clawbio.cli. The test checks the join, not any skill."""
    import copy

    import clawbio.cli as cli
    before = copy.deepcopy({alias: {k: v for k, v in info.items() if k.startswith("allowed")}
                            for alias, info in cli.SKILLS.items()})
    catalog = {"skills": [{"name": f"skill-{i}", "cli_alias": alias} for i, alias in enumerate(sorted(cli.SKILLS))]
               + [{"name": "script-only", "cli_alias": None}]}
    rules = MOD.cli_command_rules(catalog)
    for i, alias in enumerate(sorted(cli.SKILLS)):
        info = cli.SKILLS[alias]
        expected = set(info.get("allowed_extra_flags", ())) | set(info.get("allowed_extra_flags_without_values", ()))
        assert rules[f"skill-{i}"].alias == alias
        assert rules[f"skill-{i}"].flags == frozenset(expected)
    assert rules["script-only"].flags is None
    assert {alias: {k: v for k, v in info.items() if k.startswith("allowed")}
            for alias, info in cli.SKILLS.items()} == before


# --- score and aggregate ------------------------------------------------------


def _answer(skill, command=None):
    return {"skill": skill, "command": command}


def test_score_outcomes():
    pos = _case(expected_skill="clinpgx", forbidden_skills=["pharmgx-reporter"], kind="confusable")
    oos = _case(id="c2", expected_skill=None, kind="out_of_scope")
    cases_answers = [
        (pos, _answer("clinpgx", "python clawbio.py run clinpgx --gene CYP2C19"), "correct"),
        (pos, _answer("pharmgx-reporter"), "violation"),
        (pos, _answer(None), "abstained"),
        (pos, _answer("nutrigx"), "wrong"),
        (pos, {"malformed": "no JSON object"}, "malformed"),
        (pos, {"failed": "truncated"}, "failed"),
        (oos, _answer(None), "correct"),
        (oos, _answer("nutrigx"), "wrong"),
    ]
    for case, answer, outcome in cases_answers:
        result = MOD.score(case, answer, _rules())
        assert result["outcome"] == outcome, (answer, result)
        assert result["correct"] is (outcome == "correct")
        assert result["violation"] is (outcome == "violation")
        assert result["malformed"] is (outcome == "malformed")
        assert result["abstained"] is (answer.get("skill", "x") is None)


def test_score_flags_command_validity():
    case = _case(expected_skill="clinpgx", kind="positive")
    good = MOD.score(case, _answer("clinpgx", "python clawbio.py run clinpgx --gene CYP2C19 --demo"), _rules())
    bad = MOD.score(case, _answer("clinpgx", "python clawbio.py run clinpgx --genome x"), _rules())
    none = MOD.score(case, _answer("clinpgx"), _rules())
    assert good["command_valid"] is True and good["command_problem"] is None
    assert bad["command_valid"] is False and "--genome" in bad["command_problem"]
    assert none["command_valid"] is False
    # Without rules the command is not checked.
    assert MOD.score(case, _answer("clinpgx", "anything"))["command_valid"] is None


def test_score_resolves_a_cli_alias_to_its_skill():
    case = _case(expected_skill="pharmgx-reporter")
    result = MOD.score(case, _answer("pharmgx", "python clawbio.py run pharmgx --demo"), _rules())
    assert result["picked"] == "pharmgx-reporter" and result["outcome"] == "correct"
    conf = _case(expected_skill="clinpgx", forbidden_skills=["pharmgx-reporter"], kind="confusable")
    assert MOD.score(conf, _answer("pharmgx"), _rules())["outcome"] == "violation"


def test_aggregate_counts_per_kind_and_overall():
    cases = MOD.load_cases(CASES_FILE)
    results = [MOD.score(c, _answer(c["expected_skill"]), _rules()) for c in cases]
    agg = MOD.aggregate(results)
    assert agg["overall"]["n"] == 12 and agg["overall"]["correct"] == 12
    assert agg["by_kind"]["positive"]["n"] == 6
    # The outcome "abstained" is a null pick where a skill was expected; abstained_any counts every null pick.
    assert agg["by_kind"]["injection"]["abstained"] == 0
    assert agg["by_kind"]["injection"]["abstained_any"] == 1
    assert agg["overall"]["abstained_any"] == 3
    assert set(agg["by_kind"]) == set(MOD.KINDS)


def test_aggregate_lists_every_kind_even_without_cases():
    """So tables from different runs have the same rows."""
    agg = MOD.aggregate([MOD.score(_case(), _answer("pharmgx-reporter"), _rules())])
    assert list(agg["by_kind"]) == list(MOD.KINDS)
    assert agg["by_kind"]["injection"]["n"] == 0


def test_agreement_across_repeats():
    case_a, case_b = _case(id="a"), _case(id="b")
    results = [
        {**MOD.score(case_a, _answer("pharmgx-reporter"), _rules()), "repeat": 0},
        {**MOD.score(case_a, _answer("pharmgx-reporter"), _rules()), "repeat": 1},
        {**MOD.score(case_b, _answer("pharmgx-reporter"), _rules()), "repeat": 0},
        {**MOD.score(case_b, {"malformed": "no JSON object"}, _rules()), "repeat": 1},
    ]
    assert MOD.agreement(results) == {"cases": 2, "agreeing": 1}


# --- keyword baseline ---------------------------------------------------------


def test_keyword_baseline_answers_in_the_scored_shape():
    answer = MOD.keyword_baseline("Tajima's D please", router=lambda q: "dnasp")
    assert answer == {"skill": "dnasp", "command": None}
    assert MOD.keyword_baseline("hello", router=lambda q: None) == {"skill": None, "command": None}
    case = _case(expected_skill="dnasp")
    assert MOD.score(case, answer, _rules())["outcome"] == "correct"


def test_keyword_baseline_runs_the_orchestrator_router_in_process():
    """Shape only: the router is skill code, so which skill it picks may change."""
    answer = MOD.keyword_baseline("Compute nucleotide diversity for my alignment")
    assert set(answer) == {"skill", "command"}
    assert answer["command"] is None
    assert answer["skill"] is None or isinstance(answer["skill"], str)


# --- provider calls -----------------------------------------------------------


@dataclass
class Reply:
    text: str
    model: str = "fake-model-1"
    finish_reason: str = "stop"
    prompt_tokens: int | None = 100
    completion_tokens: int | None = 10


class FakeProvider:
    """Returns canned replies in order; an exception in the list is raised instead."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, prompt, *, system=None, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply(prompt) if callable(reply) else reply


def _perr(message):
    from clawbio.providers import ProviderError
    return ProviderError(message)


HTTP_429 = "openai request failed (HTTP 429). Check quota or retry later."
NO_CONNECTION = "Could not connect to ollama; check the configured endpoint and server."
TRUNCATED = "openai did not finish a text response (finish_reason=length)."
TIMEOUT = "openai request timed out; check the server or increase timeout."


class Clock:
    def __init__(self, step=0.5):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


def test_call_model_returns_text_tokens_and_time():
    provider = FakeProvider([Reply('{"skill": "dnasp", "command": null}')])
    call = MOD.call_model(provider, "SYS", "USER", sleep=lambda s: None, clock=Clock())
    assert call.text == '{"skill": "dnasp", "command": null}' and call.failure is None
    assert (call.prompt_tokens, call.completion_tokens, call.retries) == (100, 10, 0)
    assert call.seconds == pytest.approx(0.5) and call.model == "fake-model-1"
    assert provider.calls == [{"system": "SYS", "prompt": "USER"}]


def test_call_model_retries_rate_limits_and_lost_connections_with_backoff():
    slept = []
    provider = FakeProvider([_perr(HTTP_429), _perr(NO_CONNECTION), Reply("{}")])
    call = MOD.call_model(provider, "S", "U", sleep=slept.append, clock=Clock(), backoff=2.0)
    assert call.failure is None and call.retries == 2 and len(provider.calls) == 3
    assert slept == [2.0, 4.0]


def test_call_model_gives_up_after_three_tries():
    provider = FakeProvider([_perr(HTTP_429)])
    call = MOD.call_model(provider, "S", "U", sleep=lambda s: None, clock=Clock())
    assert call.text is None and call.failure == "transient" and len(provider.calls) == 3
    assert call.retries == 3


@pytest.mark.parametrize("message, failure", [
    (TRUNCATED, "truncated"),
    (TIMEOUT, "timeout"),
    ("openai request failed (HTTP 400). Check model_params.", "http_400"),
    ("openai returned no usable text (empty output or refusal).", "provider_error"),
])
def test_call_model_does_not_retry_model_failures(message, failure):
    provider = FakeProvider([_perr(message)])
    call = MOD.call_model(provider, "S", "U", sleep=lambda s: None, clock=Clock())
    assert call.failure == failure and call.text is None and len(provider.calls) == 1


def test_spend_cap_stops_before_a_call_that_would_pass_it():
    cap = MOD.SpendCap(cap_usd=0.1, input_usd_per_million=10.0, output_usd_per_million=40.0)
    first = cap.estimate("x" * 200)
    # Before any usage is known: two characters a token, plus the output allowance.
    assert first == pytest.approx((100 * 10 + MOD.OUTPUT_ALLOWANCE * 40) / 1e6)
    assert cap.allows(first)
    cap.add(MOD.Call(text="{}", failure=None, retries=0, prompt_tokens=500, completion_tokens=50,
                     seconds=1.0, model="m"), "x" * 200)
    assert cap.spent_usd == pytest.approx((500 * 10 + 50 * 40) / 1e6)
    assert cap.estimate("x" * 200) == pytest.approx(cap.spent_usd)  # the costliest call so far
    cap.spent_usd = 0.095
    assert not cap.allows(cap.estimate("x" * 200))


def test_spend_cap_does_not_charge_a_call_that_never_reached_the_model():
    cap = MOD.SpendCap(cap_usd=1.0, input_usd_per_million=1.0, output_usd_per_million=1.0)
    cap.add(MOD.Call(text=None, failure="transient", retries=3, prompt_tokens=None, completion_tokens=None,
                     seconds=1.0, model=None), "x" * 1000)
    assert cap.spent_usd == 0 and cap.unpriced_calls == 0


def test_spend_cap_prices_a_call_without_usage_conservatively():
    cap = MOD.SpendCap(cap_usd=5.0, input_usd_per_million=1.0, output_usd_per_million=1.0)
    cap.add(MOD.Call(text=None, failure="truncated", retries=0, prompt_tokens=None, completion_tokens=None,
                     seconds=1.0, model=None), "x" * 1000)
    assert cap.spent_usd == pytest.approx(cap.estimate("x" * 1000)) and cap.unpriced_calls == 1
    assert cap.spent_usd > 0


def _prompt(request):
    return "SYSTEM", request


def _replying(answers_by_request):
    return lambda prompt: Reply(json.dumps(answers_by_request[prompt]))


def test_run_model_scores_every_case_every_repeat():
    cases = MOD.load_cases(CASES_FILE)
    answers = {c["request"]: {"skill": c["expected_skill"], "command": None} for c in cases}
    provider = FakeProvider([_replying(answers)])
    results, stats = MOD.run_model(cases, provider, _prompt, rules=_rules(), repeats=2,
                                   sleep=lambda s: None, clock=Clock())
    assert len(results) == 24 and {r["repeat"] for r in results} == {0, 1}
    assert all(r["correct"] for r in results)
    assert stats["calls"] == 24 and stats["prompt_tokens"] == 2400 and stats["completion_tokens"] == 240
    assert stats["stopped_by_spend_cap"] is False and stats["models_returned"] == ["fake-model-1"]


def test_run_model_counts_failures_apart_from_answers():
    cases = MOD.load_cases(CASES_FILE)[:4]
    provider = FakeProvider([_perr(HTTP_429), Reply("{}"), _perr(TRUNCATED), Reply("prose only"),
                             Reply('{"skill": null}')])
    results, stats = MOD.run_model(cases, provider, _prompt, rules=_rules(), sleep=lambda s: None, clock=Clock())
    assert [r["outcome"] for r in results][:3] == ["malformed", "failed", "malformed"]
    assert stats["transient_retries"] == 1 and stats["failures"] == {"truncated": 1}
    assert results[1]["failure"] == "truncated"


def test_run_model_stops_at_the_spend_cap():
    cases = MOD.load_cases(CASES_FILE)
    provider = FakeProvider([Reply('{"skill": null}', prompt_tokens=1000, completion_tokens=0)])
    cap = MOD.SpendCap(cap_usd=0.0035, input_usd_per_million=1.0, output_usd_per_million=1.0)
    results, stats = MOD.run_model(cases, provider, _prompt, rules=_rules(), cap=cap, sleep=lambda s: None,
                                   clock=Clock())
    assert stats["stopped_by_spend_cap"] is True
    assert cap.spent_usd <= 0.0035
    assert len(results) == len(provider.calls) == 3


# --- properties ---------------------------------------------------------------

SKILL = st.sampled_from(sorted(NAMES))
KIND = st.sampled_from(MOD.KINDS)


@st.composite
def case_and_answer(draw, case_id="p"):
    expected = draw(st.none() | SKILL)
    forbidden = draw(st.lists(SKILL, unique=True, max_size=3))
    case = {"id": case_id, "request": "r", "expected_skill": expected, "forbidden_skills": forbidden,
            "kind": draw(KIND), "source": "synthetic"}
    answer = draw(st.one_of(
        st.builds(lambda s, c: {"skill": s, "command": c}, st.none() | SKILL | st.sampled_from(["pharmgx", "x-y"]),
                  st.none() | st.just("python clawbio.py run equity --demo")),
        st.builds(lambda r: {"malformed": r}, st.just("no JSON object")),
        st.builds(lambda r: {"failed": r}, st.sampled_from(["truncated", "timeout", "transient"])),
    ))
    return case, answer


@given(case_and_answer(), st.data())
def test_a_pick_in_forbidden_skills_is_never_scored_correct(pair, data):
    case, _ = pair
    if not case["forbidden_skills"]:
        case["forbidden_skills"] = [data.draw(SKILL)]
    pick = data.draw(st.sampled_from(case["forbidden_skills"]))
    # Even a case that wrongly forbids its own expected skill must not score the pick correct.
    result = MOD.score(case, {"skill": pick, "command": None}, _rules())
    assert not result["correct"] and result["violation"]


JSONISH = st.text(alphabet=st.sampled_from(list('{}[]":,\\ abcdeklmnpgrsux-0123456789<>/`\n')), max_size=60)


@given(st.one_of(
    st.text(max_size=80),
    JSONISH,
    st.builds(lambda pre, s, post: pre + json.dumps({"skill": s, "command": None}) + post,
              st.text(max_size=10), st.text(max_size=12), st.text(max_size=10)),
    st.builds(lambda s: json.dumps({"skill": s}, ensure_ascii=True), st.text(max_size=12)),
))
@example('{"skill": "nutr\\u0069gx", "command": null}')
@example('<think>{"skill": "dnasp"}</think>')
@settings(max_examples=300)
def test_parse_answer_never_returns_a_skill_that_is_not_in_the_text(text):
    answer = MOD.parse_answer(text)
    assert ("malformed" in answer) != ("skill" in answer)
    if answer.get("skill") is not None:
        assert answer["skill"] in text


@given(st.lists(case_and_answer(), min_size=1, max_size=12), st.randoms(use_true_random=False))
def test_scoring_does_not_depend_on_case_order(pairs, rnd):
    pairs = [({**case, "id": f"c{i}"}, answer) for i, (case, answer) in enumerate(pairs)]
    shuffled = pairs[:]
    rnd.shuffle(shuffled)
    first = [MOD.score(c, a, _rules()) for c, a in pairs]
    second = [MOD.score(c, a, _rules()) for c, a in shuffled]
    assert MOD.aggregate(first) == MOD.aggregate(second)
    assert sorted(first, key=lambda r: r["case_id"]) == sorted(second, key=lambda r: r["case_id"])


@given(st.lists(case_and_answer(), max_size=15))
def test_aggregate_counts_sum_to_the_number_of_cases(pairs):
    agg = MOD.aggregate([MOD.score(c, a, _rules()) for c, a in pairs])
    overall = agg["overall"]
    assert overall["n"] == len(pairs)
    assert sum(overall[o] for o in MOD.OUTCOMES) == len(pairs)
    assert sum(k["n"] for k in agg["by_kind"].values()) == len(pairs)
    for counts in agg["by_kind"].values():
        assert sum(counts[o] for o in MOD.OUTCOMES) == counts["n"]


# --- command line ---------------------------------------------------------------


def _main(argv, monkeypatch, provider=None, made=None):
    monkeypatch.setattr(MOD, "git_commit", lambda root: ("9a35f8e72afaae4f47a89571836bbdb0065ea479", True))

    def make_provider(name, model, *, api_key, base_url, timeout):
        if made is not None:
            made.append({"name": name, "model": model, "api_key": api_key, "base_url": base_url})
        return provider

    monkeypatch.setattr(MOD, "make_provider", make_provider)
    base = ["--cases", str(CASES_FILE), "--root", str(FIXTURES), "--rules", str(FIXTURES / "routing_rules.md")]
    return MOD.main(base + argv)


def _answers_from_fixture():
    cases = MOD.load_cases(CASES_FILE)
    by_request = {c["request"]: {"skill": c["expected_skill"],
                                 "command": None if c["expected_skill"] is None else "python clawbio.py list"}
                  for c in cases}

    def reply(prompt):
        request = next(r for r in by_request if r in prompt)
        return Reply(json.dumps(by_request[request]))
    return reply


def test_main_keyword_prints_a_table(monkeypatch, capsys):
    monkeypatch.setattr(MOD, "keyword_router", lambda: (lambda q: "dnasp" if "Tajima" in q else None))
    _main(["--provider", "keyword"], monkeypatch)
    out = capsys.readouterr().out
    assert "| Kind |" in out and "| positive | 6 | 1 |" in out
    assert "n/a" in out  # the keyword router gives no command


def test_main_ollama_writes_a_valid_summary(monkeypatch, tmp_path, capsys):
    made = []
    out_dir = tmp_path / "runs"
    _main(["--provider", "ollama", "--model", "open-model:8b", "--repeats", "2", "--out", str(out_dir),
           "--date", "2027-01-29", "--context-window", "32768", "--gpu", "NVIDIA GeForce RTX 4080",
           "--memory-gb", "64"], monkeypatch, FakeProvider([_answers_from_fixture()]), made)
    # No key for a local server, and the default base URL passed explicitly, so no
    # OLLAMA_BASE_URL or OPENAI_BASE_URL in the environment can redirect the run.
    assert made == [{"name": "ollama", "model": "open-model:8b", "api_key": None,
                     "base_url": "http://localhost:11434/v1"}]
    summary = json.loads((out_dir / "open-model-8b.summary.json").read_text(encoding="utf-8"))
    run_summary = MOD.run_summary
    assert run_summary.validate_summary(summary) == []
    assert summary["cost"] is None and summary["context_window"] == 32768
    assert summary["hardware"] == {"gpu": "NVIDIA GeForce RTX 4080", "cpu": None, "memory_gb": 64}
    assert summary["scope"] == {"tiers": ["routing"], "n_cases": 12, "n_repeats": 2}
    assert summary["model"]["open_weights"] is True and summary["date"] == "2027-01-29"
    assert summary["tokens"] == {"input": 2400, "output": 240}
    assert summary["routing"]["overall"]["correct"] == 24
    # The replies stay next to the summary, outside the repository, for checking malformed answers.
    replies = (out_dir / "open-model-8b.replies.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(replies) == 24 and all("reply" in json.loads(line) for line in replies)
    captured = capsys.readouterr()
    assert "context window is set on the Ollama server" in captured.err
    assert "| overall | 24 |" in captured.out


def test_main_openai_passes_the_key_explicitly_and_prices_the_run(monkeypatch, tmp_path):
    made = []
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"input_usd_per_million": 2.0, "output_usd_per_million": 8.0,
                                  "source": "vendor price page", "date": "2027-01-15"}), encoding="utf-8")
    monkeypatch.setenv("TEST_VENDOR_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    out_dir = tmp_path / "runs"
    _main(["--provider", "openai", "--model", "hosted-model-2026-12-01", "--api-key-env", "TEST_VENDOR_KEY",
           "--base-url", "https://example.invalid/v1", "--out", str(out_dir), "--date", "2027-01-30",
           "--max-cost-usd", "5", "--prices", str(prices), "--vendor", "examplevendor"],
          monkeypatch, FakeProvider([_answers_from_fixture()]), made)
    assert made[0]["api_key"] == "sk-test-not-a-real-key" and made[0]["base_url"] == "https://example.invalid/v1"
    summary = json.loads((out_dir / "hosted-model-2026-12-01.summary.json").read_text(encoding="utf-8"))
    assert MOD.run_summary.validate_summary(summary) == []
    assert summary["cost"]["usd"] == pytest.approx((1200 * 2.0 + 120 * 8.0) / 1e6)
    assert summary["cost"]["price_source"] == "vendor price page" and summary["cost"]["spend_cap_usd"] == 5.0
    assert summary["model"]["provider"] == "examplevendor" and summary["model"]["open_weights"] is False
    assert "sk-test" not in json.dumps(summary) and "example.invalid" not in json.dumps(summary)


@pytest.mark.parametrize("extra, message", [
    (["--provider", "openai", "--model", "m", "--max-cost-usd", "1"], "--api-key-env"),
    (["--provider", "openai", "--model", "m", "--api-key-env", "TEST_VENDOR_KEY"], "--max-cost-usd"),
    (["--provider", "ollama"], "--model"),
    (["--provider", "ollama", "--model", "m", "--date", "29/01/2027"], "--date"),
    (["--provider", "ollama", "--model", "m", "--cpu", "Intel Core i7 @ 3.70GHz"], "hardware.cpu"),
])
def test_main_refuses_incomplete_settings_before_any_call(monkeypatch, tmp_path, extra, message, capsys):
    monkeypatch.setenv("TEST_VENDOR_KEY", "sk-test")
    provider = FakeProvider([Reply("{}")])
    with pytest.raises(SystemExit):
        _main(extra + ["--out", str(tmp_path / "o")], monkeypatch, provider)
    assert message in capsys.readouterr().err
    assert provider.calls == []


def test_main_refuses_an_out_folder_inside_the_repository(monkeypatch, capsys):
    inside = HERE / "routing-tier-out-must-not-exist"
    # A call raises, so if the check were missing the run would stop before writing into the checkout.
    provider = FakeProvider([RuntimeError("the run must stop before any call")])
    with pytest.raises(SystemExit):
        _main(["--provider", "ollama", "--model", "m", "--out", str(inside)], monkeypatch, provider)
    assert "inside the repository" in capsys.readouterr().err
    assert provider.calls == [] and not inside.exists()


def test_main_refuses_to_overwrite_a_summary(monkeypatch, tmp_path, capsys):
    (tmp_path / "m.summary.json").write_text("{}", encoding="utf-8")
    provider = FakeProvider([Reply("{}")])
    with pytest.raises(SystemExit):
        _main(["--provider", "ollama", "--model", "m", "--out", str(tmp_path)], monkeypatch, provider)
    assert "already exists" in capsys.readouterr().err and provider.calls == []


@pytest.mark.parametrize("model, slug", [("open-model:8b", "open-model-8b"), ("Org/Model 7B", "org-model-7b"),
                                         ("a..b", "a.b")])
def test_model_slug(model, slug):
    assert MOD.model_slug(model) == slug


# --- repository hygiene -----------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_no_fixture_is_dropped_by_gitignore():
    """The root .gitignore drops data/, output/, profiles/, result.json and *.log at any depth."""
    repo = HERE.parents[1]
    paths = [p.relative_to(repo).as_posix() for p in FIXTURES.rglob("*") if p.is_file()]
    proc = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"], cwd=repo,
                          input="\n".join(paths), capture_output=True, text=True)
    assert paths and proc.stdout == ""
