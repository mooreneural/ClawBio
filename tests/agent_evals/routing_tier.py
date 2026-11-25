"""The routing tier of the model evaluation: one model call per case, scored offline.

A case is one plain-language request with the skill that should handle it (or none),
the skills that must not fire, a kind and a source. The prompt holds the AGENTS.md
routing rules and the compact index from routing_index.render_index, and asks for one
JSON object, {"skill": name or null, "command": string or null}. parse_answer reads
that object and never guesses a skill from prose. score compares the pick with the
case and checks the command: it must name the skill or its CLI alias, and a
`clawbio.py run` command may use only --input, --output, --demo and the flags in
clawbio.cli.SKILLS allowed_extra_flags, because the CLI drops other flags without a
message. A command that runs a script under skills/<name>/ (with python, or Rscript for
an R script) is checked for the skill folder only; its flags are not checked here.

keyword_baseline runs the bio-orchestrator keyword router in-process and answers in
the same shape, so the same scorer reads it. It gives no command.

Running this file runs one provider over a cases file and prints a table; for a model
it also writes <out>/<model-slug>.summary.json, which passes
run_summary.validate_summary, and <out>/<model-slug>.replies.jsonl with each raw reply.
Both stay outside the repository. The tests call no model.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HARNESS_VERSION = "routing-tier 0.1.0"


def _sibling(name: str):
    """Load a module next to this file, as test_agent_trace.py loads agent_trace.py."""
    key = f"clawbio_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module  # @dataclass reads sys.modules[cls.__module__]
    spec.loader.exec_module(module)
    return module


routing_index = _sibling("routing_index")
run_summary = _sibling("run_summary")


def _clawbio(module: str):
    """Import a clawbio module, with the repository on sys.path when run as a script."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    return importlib.import_module(module)


# --- cases ---------------------------------------------------------------------

KINDS = ("positive", "confusable", "out_of_scope", "injection")
FIELDS = ("id", "request", "expected_skill", "forbidden_skills", "kind", "source")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _text(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_case(case, skill_names: Iterable[str] | None = None) -> list[str]:
    """Every problem with one case, each starting with the field it is about."""
    if not isinstance(case, dict):
        return ["case must be a JSON object"]
    problems = [f"{field} is missing" for field in FIELDS if field not in case]
    for field in ("id", "request", "source"):
        if field in case and not _text(case[field]):
            problems.append(f"{field} must be a non-empty string")
    if "kind" in case and case["kind"] not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    names = None if skill_names is None else set(skill_names)
    expected = case.get("expected_skill")
    if "expected_skill" in case and expected is not None:
        if not (isinstance(expected, str) and NAME.fullmatch(expected)):
            problems.append("expected_skill must be a skill name or null")
        elif names is not None and expected not in names:
            problems.append(f"expected_skill {expected} is not a catalog skill")
    forbidden = case.get("forbidden_skills")
    if "forbidden_skills" in case:
        if not (isinstance(forbidden, list)
                and all(isinstance(s, str) and NAME.fullmatch(s) for s in forbidden)):
            problems.append("forbidden_skills must be a list of skill names")
            forbidden = None
        elif len(set(forbidden)) != len(forbidden):
            problems.append("forbidden_skills must not repeat a skill")
        if forbidden is not None:
            if expected is not None and expected in forbidden:
                problems.append("forbidden_skills must not hold the expected skill")
            if names is not None:
                problems += [f"forbidden_skills {s} is not a catalog skill" for s in forbidden if s not in names]
    kind = case.get("kind")
    if kind == "positive" and "expected_skill" in case and expected is None:
        problems.append("expected_skill must name a skill for a positive case")
    if kind == "out_of_scope" and expected is not None:
        problems.append("expected_skill must be null for an out_of_scope case")
    if kind == "confusable" and isinstance(forbidden, list) and not forbidden:
        problems.append("forbidden_skills must name the skill a confusable case resembles")
    return problems


def load_cases(path, skill_names: Iterable[str] | None = None) -> list[dict]:
    """The cases of a .jsonl file, one JSON object a line; blank lines are skipped.

    Raises ValueError listing every bad line, including a repeated id.
    """
    path = Path(path)
    names = None if skill_names is None else set(skill_names)
    cases, problems, seen = [], [], {}
    # split("\n"), not splitlines(): a JSON string may hold U+2028 unescaped.
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except ValueError:
            problems.append(f"line {number}: not JSON")
            continue
        problems += [f"line {number}: {p}" for p in validate_case(case, names)]
        if isinstance(case, dict) and isinstance(case.get("id"), str):
            if case["id"] in seen:
                problems.append(f"line {number}: id {case['id']} is used twice (first on line {seen[case['id']]})")
            seen.setdefault(case["id"], number)
        cases.append(case)
    if problems:
        raise ValueError(f"{path.name} has bad cases:\n" + "\n".join(problems))
    if not cases:
        raise ValueError(f"no cases in {path.name}")
    return cases


# --- prompt --------------------------------------------------------------------

ROUTING_SECTIONS = ("Skill Routing", "Running a Demo")
_SECTION = re.compile(r"#{1,2}[ \t]+(.*?)[ \t]*")


def routing_rules(markdown: str, sections: Iterable[str] = ROUTING_SECTIONS) -> str:
    """The named "## " sections of AGENTS.md, each up to the next "#" or "##" heading."""
    found: dict[str, list[str]] = {}
    current = None
    fence = False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        heading = None if fence else _SECTION.fullmatch(line)
        if heading:
            current = heading.group(1) if line.startswith("## ") else None
            if current is not None and current not in found:
                found[current] = []
            elif current is not None:
                current = None  # a repeated heading: keep the first
        if current is not None:
            found[current].append(line)
    blocks = []
    for name in sections:
        if name not in found:
            raise ValueError(f"no '## {name}' section in the routing rules")
        blocks.append("\n".join(found[name]).strip())
    return "\n\n".join(blocks)


SYSTEM_TEMPLATE = """You route requests to ClawBio skills. Follow the routing rules below. The skill \
index after them stands in for skills/catalog.json: one block per skill, with its name, CLI alias \
("none" when it has no clawbio.py run entry), description, trigger keywords and its "Do NOT fire" list.

Answer with one JSON object and nothing else:
{{"skill": "<skill name as the index writes it>" or null, "command": "<one shell command that runs the skill>" or null}}

Set "skill" to null when no skill fits the request or it is too unclear to route, and then set \
"command" to null too.

# Routing rules

{rules}

# Skill index

{index}"""


def build_prompt(index_text: str, rules_text: str, request: str) -> tuple[str, str]:
    """System and user text for one case. The system text is the same for every case."""
    system = SYSTEM_TEMPLATE.format(rules=rules_text.strip(), index=index_text.strip())
    return system, f"Request:\n{request}"


# --- parse_answer --------------------------------------------------------------

_DECODER = json.JSONDecoder()


def _objects(text: str) -> list[dict]:
    """Every JSON object in text, outermost only, left to right."""
    found, i = [], 0
    while (start := text.find("{", i)) >= 0:
        try:
            value, end = _DECODER.raw_decode(text, start)
        except ValueError:
            i = start + 1
            continue
        if isinstance(value, dict):
            found.append(value)
        i = end
    return found


def parse_answer(text) -> dict:
    """{"skill", "command"} from a reply, or {"malformed": reason}.

    Strips a leading <think>...</think> block, then reads the one JSON object in what
    is left, inside a markdown fence or not; a second object anywhere makes the reply
    malformed. The skill must be a skill name or null, and the name must also appear
    verbatim in the reply after any <think> block, so a name found only in the
    reasoning, or written only as a JSON escape, is malformed.
    """
    if not isinstance(text, str) or not text.strip():
        return {"malformed": "empty reply"}
    body = text.strip()
    if body.startswith("<think>"):
        end = body.find("</think>")
        if end < 0:
            return {"malformed": "the reply has an unclosed <think> block"}
        body = body[end + len("</think>"):].strip()
        if not body:
            return {"malformed": "no JSON object after the <think> block"}
    objects = _objects(body)
    if not objects:
        return {"malformed": "no JSON object in the reply"}
    if len(objects) > 1:
        return {"malformed": "more than one JSON object in the reply"}
    answer = objects[0]
    if "skill" not in answer:
        return {"malformed": "the JSON object has no skill"}
    skill, command = answer["skill"], answer.get("command")
    if skill is not None:
        if not (isinstance(skill, str) and NAME.fullmatch(skill)):
            return {"malformed": "skill must be a skill name or null"}
        if skill not in body:  # body: the reply without its <think> block
            return {"malformed": "skill name must appear verbatim in the reply after any <think> block"}
    if command is not None and not isinstance(command, str):
        return {"malformed": "command must be a string or null"}
    return {"skill": skill, "command": command}


# --- commands --------------------------------------------------------------------


@dataclass(frozen=True)
class SkillCommand:
    """How a skill is run: its clawbio.py run alias and allowed extra flags.

    flags is None when there is no allowlist (no alias, or an alias that clawbio.cli
    does not register). snake_case: the CLI also accepts --a_b for --a-b (nf-core).
    """
    alias: str | None
    flags: frozenset | None
    snake_case: bool = False


def command_rules(catalog: Mapping, cli_skills: Mapping, nfcore_aliases: Iterable[str] = ()) -> dict:
    """{skill name: SkillCommand} from the catalog and a SKILLS-shaped allowlist.

    The allowlist is read for the catalog's cli_alias of each skill, since one script
    folder can back several aliases (pharmgx-reporter backs pharmgx and drugphoto).
    """
    nfcore = set(nfcore_aliases)
    rules = {}
    for skill in catalog["skills"]:
        alias = skill.get("cli_alias")
        info = cli_skills.get(alias) if alias else None
        flags = None
        if info is not None:
            flags = frozenset(info.get("allowed_extra_flags", ())) | frozenset(
                info.get("allowed_extra_flags_without_values", ()))
        rules[skill["name"]] = SkillCommand(alias=alias, flags=flags, snake_case=alias in nfcore)
    return rules


def cli_command_rules(catalog: Mapping) -> dict:
    """command_rules with the live allowlists, read by importing clawbio.cli (never changed)."""
    cli = _clawbio("clawbio.cli")
    return command_rules(catalog, cli.SKILLS, getattr(cli, "_NFCORE_PIPELINE_SKILLS", ()))


BASE_FLAGS = frozenset({"--input", "--output", "--demo"})
_OPERATOR = re.compile(r"[;&|<>()]+")
_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
_NUMBER = re.compile(r"-\d[\d.eE+-]*")
_INTERPRETER = {"Rscript": "R"}  # besides python: claw-amplicon-qc's demo is an R script
# skills/<folder>/[subfolders/]<script>; no "." or ".." segment, so the path stays in the folder.
_SCRIPT = re.compile(r"skills/([^/]+)/(?:(?!\.\.?/)[^/]+/)*(?!\.\.?$)[^/]+\.(py|R)")


def _snake(flag: str) -> str:
    return "--" + flag[2:].replace("_", "-") if flag.startswith("--") else flag


def _check(skill, command, rules: Mapping) -> tuple[str | None, bool]:
    """(problem or None, whether the flags were checked)."""
    if skill is None:
        return (None if command is None or not command.strip() else "a command without a skill"), False
    if command is None or not command.strip():
        return "no command", False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return "not a shell command (unbalanced quotes)", False
    if any(_OPERATOR.fullmatch(t) for t in tokens):
        return "more than one command: pipes, redirects and && are not one command", False
    rule = rules.get(skill)
    if rule is None:
        return f"{skill} is not a catalog skill", False
    i = 0
    if tokens[:2] == ["uv", "run"]:
        i = 2
        while i < len(tokens) and tokens[i].startswith("--extra"):
            i += 1 if "=" in tokens[i] else 2
    if i < len(tokens) and tokens[i] == "clawbio":
        rest = tokens[i + 1:]
    elif i + 1 < len(tokens) and (interpreter := _INTERPRETER.get(tokens[i]) or
                                  ("py" if _PYTHON.fullmatch(tokens[i]) else None)):
        target = tokens[i + 1].removeprefix("./")
        if target == "clawbio.py" and interpreter == "py":
            rest = tokens[i + 2:]
        elif ((script := _SCRIPT.fullmatch(target)) and NAME.fullmatch(script.group(1))
              and script.group(2) == interpreter):
            folder = script.group(1)
            return (None if folder == skill else f"the command names {folder}, not {skill}"), False
        else:
            return "not a ClawBio command", False
    else:
        return "not a ClawBio command", False
    if len(rest) < 2 or rest[0] != "run":
        return "not a skill run (clawbio.py run <skill>)", False
    name = rest[1]
    if name not in (skill, rule.alias):
        return f"the command names {name}, not {skill}", False
    if rule.alias is None:
        return f"{skill} has no clawbio.py run entry; run its script", False
    for token in rest[2:]:
        if not token.startswith("-") or _NUMBER.fullmatch(token):
            continue  # a value
        flag = token.split("=", 1)[0]
        key = _snake(flag) if rule.snake_case else flag
        if key in BASE_FLAGS:
            continue
        if rule.flags is None:
            return f"clawbio.cli has no allowlist for {rule.alias}, so {flag} cannot be checked", False
        if key not in rule.flags:
            return f"{flag} is not an allowed flag for clawbio.py run {rule.alias}", False
    return None, True


def check_command(skill, command, rules: Mapping) -> str | None:
    """None when command is a valid way to run skill, else the problem."""
    return _check(skill, command, rules)[0]


# --- score and aggregate -----------------------------------------------------------

OUTCOMES = ("correct", "violation", "abstained", "wrong", "malformed", "failed")


def _resolve(skill, rules: Mapping | None):
    """A CLI alias counts as its skill: `clawbio.py run` accepts both."""
    if skill is None or not rules or skill in rules:
        return skill
    by_alias = {rule.alias: name for name, rule in sorted(rules.items()) if rule.alias}
    return by_alias.get(skill, skill)


def score(case: Mapping, answer: Mapping, rules: Mapping | None = None) -> dict:
    """One scored answer. outcome is exactly one of OUTCOMES.

    violation comes first: a pick in forbidden_skills is never correct. abstained is
    True for every answer with a null skill, correct or not. command_valid is None
    when no rules are given.
    """
    expected, forbidden = case["expected_skill"], set(case["forbidden_skills"])
    result = {"case_id": case["id"], "kind": case["kind"], "expected": expected,
              "answered": None, "picked": None, "failure": answer.get("failed"),
              "malformed_reason": answer.get("malformed"), "command": answer.get("command")}
    if "failed" in answer:
        outcome = "failed"
    elif "malformed" in answer:
        outcome = "malformed"
    else:
        result["answered"] = answer["skill"]
        picked = result["picked"] = _resolve(answer["skill"], rules)
        if picked is not None and picked in forbidden:
            outcome = "violation"
        elif picked == expected:
            outcome = "correct"
        elif picked is None:
            outcome = "abstained"
        else:
            outcome = "wrong"
    result.update(outcome=outcome, correct=outcome == "correct", violation=outcome == "violation",
                  malformed=outcome == "malformed", failed=outcome == "failed",
                  abstained=outcome not in ("failed", "malformed") and answer["skill"] is None)
    if rules is None:
        problem, checked, valid = None, False, None
    elif outcome in ("failed", "malformed"):
        problem, checked, valid = "no answer", False, False
    else:
        problem, checked = _check(result["picked"], answer.get("command"), rules)
        valid = problem is None
    result.update(command_valid=valid, command_problem=problem, flags_checked=checked)
    return result


def _counts() -> dict:
    return {"n": 0, **dict.fromkeys(OUTCOMES, 0), "abstained_any": 0, "command_valid": 0, "commands_checked": 0}


def aggregate(results: Iterable[Mapping]) -> dict:
    """Counts per kind and overall. The OUTCOMES counts of a group sum to its n.

    abstained counts wrong abstentions (an outcome); abstained_any counts every null pick.
    """
    overall, by_kind = _counts(), {kind: _counts() for kind in KINDS}
    for result in results:
        for counts in (overall, by_kind.setdefault(result["kind"], _counts())):
            counts["n"] += 1
            counts[result["outcome"]] += 1
            counts["abstained_any"] += bool(result["abstained"])
            counts["command_valid"] += result["command_valid"] is True
            counts["commands_checked"] += result["command_valid"] is not None
    return {"overall": overall, "by_kind": by_kind}


def agreement(results: Iterable[Mapping]) -> dict:
    """Cases run more than once, and how many gave the same answer every time."""
    answers: dict[str, list] = {}
    for result in results:
        mark = result["outcome"] if result["outcome"] in ("failed", "malformed") else result["picked"]
        answers.setdefault(result["case_id"], []).append(mark)
    repeated = [marks for marks in answers.values() if len(marks) > 1]
    return {"cases": len(repeated), "agreeing": sum(len(set(marks)) == 1 for marks in repeated)}


# --- keyword baseline ----------------------------------------------------------------

ORCHESTRATOR = REPO / "skills" / "bio-orchestrator" / "orchestrator.py"


def keyword_router() -> Callable[[str], str | None]:
    """detect_skill_from_query of the bio-orchestrator, loaded in-process."""
    key = "clawbio_bio_orchestrator"
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, ORCHESTRATOR)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[key]
            raise
    return module.detect_skill_from_query


def keyword_baseline(request: str, router: Callable[[str], str | None] | None = None) -> dict:
    """The keyword router's pick in the shape parse_answer returns. It builds no command."""
    skill = (router or keyword_router())(request)
    if skill is not None and not isinstance(skill, str):
        raise TypeError(f"the keyword router returned {type(skill).__name__}, not a skill name")
    return {"skill": skill, "command": None}


def run_keyword(cases: list[Mapping], *, rules: Mapping | None, repeats: int = 1, router=None) -> list[dict]:
    router = router or keyword_router()
    return [{**score(case, keyword_baseline(case["request"], router), rules), "repeat": repeat}
            for repeat in range(repeats) for case in cases]


# --- provider calls ----------------------------------------------------------------

TRIES = 3
OUTPUT_ALLOWANCE = 2000  # output tokens assumed for a call before any usage is known
_HTTP = re.compile(r"\(HTTP (\d{3})\)")


@dataclass(frozen=True)
class Call:
    text: str | None
    failure: str | None  # transient, truncated, timeout, http_<code>, provider_error
    retries: int  # rate limits and lost connections, retried by the harness
    prompt_tokens: int | None
    completion_tokens: int | None
    seconds: float  # time in the provider, summed over tries, without the backoff
    model: str | None


def _failure(message: str) -> str:
    """Kind of a ProviderError, from the fixed messages of clawbio/providers/chat.py."""
    if status := _HTTP.search(message):
        return "transient" if status.group(1) == "429" else f"http_{status.group(1)}"
    if message.startswith("Could not connect"):
        return "transient"
    if "timed out" in message:
        return "timeout"
    if "finish_reason=length" in message:
        return "truncated"
    return "provider_error"


def call_model(provider, system: str, user: str, *, tries: int = TRIES, backoff: float = 1.0,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.perf_counter) -> Call:
    """One routing call. Rate limits and lost connections are retried with backoff and
    counted apart from model failures; other failures are returned at once."""
    provider_error = _clawbio("clawbio.providers").ProviderError
    retries, seconds = 0, 0.0
    for attempt in range(tries):
        start = clock()
        try:
            reply = provider.generate(user, system=system)
        except provider_error as e:
            seconds += clock() - start
            failure = _failure(str(e))
            if failure != "transient":
                return Call(None, failure, retries, None, None, seconds, None)
            retries += 1
            if attempt + 1 < tries:
                sleep(backoff * 2 ** attempt)
            continue
        seconds += clock() - start
        tokens = reply.prompt_tokens, reply.completion_tokens
        if getattr(reply, "finish_reason", "stop") == "length":
            return Call(None, "truncated", retries, *tokens, seconds, reply.model)
        return Call(reply.text, None, retries, *tokens, seconds, reply.model)
    return Call(None, "transient", retries, None, None, seconds, None)


class SpendCap:
    """Stops a priced run before any call that would take spending past the cap.

    The next call is assumed to cost as much as the costliest call so far; before any
    usage is known, two characters a token plus OUTPUT_ALLOWANCE output tokens. A call
    that returned no usage is charged that conservative price too. Prompt caching is
    ignored, so spending is an upper bound.
    """

    def __init__(self, cap_usd: float, input_usd_per_million: float, output_usd_per_million: float):
        self.cap_usd = cap_usd
        self.input_price = input_usd_per_million
        self.output_price = output_usd_per_million
        self.spent_usd = 0.0
        self.unpriced_calls = 0
        self._costliest = 0.0

    def _conservative(self, prompt_text: str) -> float:
        return (len(prompt_text) / 2 * self.input_price + OUTPUT_ALLOWANCE * self.output_price) / 1e6

    def estimate(self, prompt_text: str) -> float:
        return self._costliest or self._conservative(prompt_text)

    def allows(self, estimate: float) -> bool:
        return self.spent_usd + estimate <= self.cap_usd

    def add(self, call: Call, prompt_text: str) -> None:
        if call.failure == "transient":
            return  # the request never reached the model
        if call.prompt_tokens is None or call.completion_tokens is None:
            cost = self._conservative(prompt_text)
            self.unpriced_calls += 1
        else:
            cost = (call.prompt_tokens * self.input_price + call.completion_tokens * self.output_price) / 1e6
        self.spent_usd += cost
        self._costliest = max(self._costliest, cost)


def run_model(cases: list[Mapping], provider, prompt: Callable[[str], tuple[str, str]], *,
              rules: Mapping | None, repeats: int = 1, cap: SpendCap | None = None,
              sleep=time.sleep, clock=time.perf_counter) -> tuple[list[dict], dict]:
    """Every case, repeats times, repeat by repeat, so a stop at the cap leaves whole repeats first."""
    results = []
    stats = {"calls": 0, "transient_retries": 0, "failures": {}, "prompt_tokens": 0, "completion_tokens": 0,
             "calls_without_usage": 0, "call_seconds": 0.0, "stopped_by_spend_cap": False}
    models = set()
    for repeat in range(repeats):
        for case in cases:
            system, user = prompt(case["request"])
            if cap is not None and not cap.allows(cap.estimate(system + user)):
                stats["stopped_by_spend_cap"] = True
                break
            call = call_model(provider, system, user, sleep=sleep, clock=clock)
            if cap is not None:
                cap.add(call, system + user)
            stats["calls"] += 1
            stats["transient_retries"] += call.retries
            stats["call_seconds"] += call.seconds
            if call.failure:
                stats["failures"][call.failure] = stats["failures"].get(call.failure, 0) + 1
            elif call.prompt_tokens is None or call.completion_tokens is None:
                stats["calls_without_usage"] += 1
            stats["prompt_tokens"] += call.prompt_tokens or 0
            stats["completion_tokens"] += call.completion_tokens or 0
            if call.model:
                models.add(call.model)
            answer = parse_answer(call.text) if call.failure is None else {"failed": call.failure}
            results.append({**score(case, answer, rules), "repeat": repeat, "seconds": round(call.seconds, 3),
                            "retries": call.retries, "reply": call.text})
        if stats["stopped_by_spend_cap"]:
            break
    stats["models_returned"] = sorted(models)
    return results, stats


# --- command line ---------------------------------------------------------------------


def model_slug(model: str) -> str:
    """A file-name-safe form of a model ID: open-model:8b -> open-model-8b."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", model.lower())
    slug = re.sub(r"\.{2,}", ".", slug).strip("-.")
    if not slug:
        raise ValueError(f"cannot make a file name from the model ID {model!r}")
    return slug


def render_results(agg: Mapping, commands: bool = True) -> str:
    columns = ("Kind", "n", *OUTCOMES, "command valid")
    lines = ["| " + " | ".join(columns) + " |", "| --- |" + " ---: |" * (len(columns) - 1)]
    for kind, counts in [*agg["by_kind"].items(), ("overall", agg["overall"])]:
        valid = str(counts["command_valid"]) if commands else "n/a"
        lines.append("| " + " | ".join([kind, str(counts["n"]), *(str(counts[o]) for o in OUTCOMES), valid]) + " |")
    return "\n".join(lines) + "\n"


def git_commit(root: Path) -> tuple[str, bool]:
    """(HEAD commit, whether tracked files match it)."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                            capture_output=True, text=True, check=True)
    return head.stdout.strip(), status.stdout.strip() == ""


def make_provider(name: str, model: str, *, api_key: str | None, base_url: str, timeout: float):
    return _clawbio("clawbio.providers").create_provider(name, model, api_key=api_key, base_url=base_url,
                                                         timeout=timeout)


def _default_base_url(name: str) -> str:
    providers = _clawbio("clawbio.providers")
    return {"openai": providers.OpenAIProvider, "ollama": providers.OllamaProvider}[name].default_base_url


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_prices(parser, path: Path) -> dict:
    try:
        prices = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        parser.error(f"--prices: cannot read {path}: {e}")
    keys = ("input_usd_per_million", "output_usd_per_million", "source", "date")
    if not isinstance(prices, dict) or any(k not in prices for k in keys):
        parser.error(f"--prices must hold a JSON object with {', '.join(keys)}")
    for key in keys[:2]:
        value = prices[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0:
            parser.error(f"--prices: {key} must be a number of 0 or more")
    return prices


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the routing tier over a cases file and print a table. "
                                            "Model runs also write a run summary outside the repository.")
    p.add_argument("--cases", type=Path, required=True, help="cases, one JSON object a line")
    p.add_argument("--provider", choices=("keyword", "ollama", "openai"), required=True)
    p.add_argument("--model", help="model ID, pinned (an Ollama tag with --digest, or a dated version ID)")
    p.add_argument("--base-url", help="OpenAI-compatible endpoint (default: the provider's own)")
    p.add_argument("--api-key-env", help="openai: the environment variable holding this vendor's key; "
                                         "the key is passed explicitly, never read from OPENAI_API_KEY by default")
    p.add_argument("--vendor", help="who serves the model, recorded as model.provider (default: --provider)")
    p.add_argument("--open-weights", action="store_true", help="openai: the hosted model is open-weights")
    p.add_argument("--digest", help="the model digest, recorded in the summary")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--out", type=Path, help="folder for the summary and replies, outside the repository")
    p.add_argument("--date", default=date.today().isoformat(), help="run date for the summary, YYYY-MM-DD")
    p.add_argument("--max-cost-usd", type=float, help="openai: stop before any call that would pass this")
    p.add_argument("--prices", type=Path, help="openai: JSON with input_usd_per_million, "
                                               "output_usd_per_million, source and date")
    p.add_argument("--context-window", type=int, help="the context window set on the server, as you set it")
    p.add_argument("--gpu", help="GPU model name, recorded as given")
    p.add_argument("--cpu", help="CPU model name, recorded as given")
    p.add_argument("--memory-gb", type=float, help="memory in GB, recorded as given")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds per request")
    p.add_argument("--task-set-version", default="draft")
    p.add_argument("--root", type=Path, default=REPO,
                   help="folder holding skills/catalog.json and skills/<name>/SKILL.md (default: this repo)")
    p.add_argument("--rules", type=Path, default=REPO / "AGENTS.md", help="file with the routing rules")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be 1 or more")
    catalog = json.loads((args.root / "skills" / "catalog.json").read_text(encoding="utf-8"))
    try:
        cases = load_cases(args.cases, skill_names=[s["name"] for s in catalog["skills"]])
    except ValueError as e:
        parser.error(f"--cases: {e}")
    rules = cli_command_rules(catalog)

    if args.provider == "keyword":
        if args.out is not None:
            parser.error("--out: the keyword baseline calls no model and writes no summary")
        results = run_keyword(cases, rules=rules, repeats=args.repeats)
        print(f"keyword baseline (bio-orchestrator detect_skill_from_query), {len(cases)} cases "
              f"x {args.repeats} repeat(s); the router builds no command")
        print(render_results(aggregate(results), commands=False))
        return 0

    if not args.model:
        parser.error(f"--model is required for --provider {args.provider}")
    api_key = None
    prices = None
    if args.provider == "openai":
        if not args.api_key_env:
            parser.error("--api-key-env is required for --provider openai: name the variable holding this "
                         "vendor's key, so no other key is sent to the endpoint")
        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            parser.error(f"--api-key-env: {args.api_key_env} is not set")
        if args.max_cost_usd is None or not args.max_cost_usd > 0:
            parser.error("--max-cost-usd is required for --provider openai and must be above 0")
        if args.prices is None:
            parser.error("--prices is required for --provider openai")
        prices = _load_prices(parser, args.prices)
    if args.out is None:
        parser.error(f"--out is required for --provider {args.provider}")
    out = args.out.resolve()
    if out.is_relative_to(REPO.resolve()):
        parser.error(f"--out {args.out} is inside the repository; summaries are copied in by hand "
                     "and replies stay outside it")
    slug = model_slug(args.model)
    summary_path, replies_path = out / f"{slug}.summary.json", out / f"{slug}.replies.jsonl"
    for path in (summary_path, replies_path):
        if path.exists():
            parser.error(f"--out: {path} already exists")
    if not run_summary._iso_date(args.date):
        parser.error("--date must be an ISO date, YYYY-MM-DD")
    try:
        commit, clean = git_commit(REPO)
    except (OSError, subprocess.CalledProcessError) as e:
        parser.error(f"cannot read the ClawBio commit with git: {e}")

    skill_md = {}
    for skill in catalog["skills"]:
        path = args.root / "skills" / skill["name"] / "SKILL.md"
        if path.is_file():
            skill_md[skill["name"]] = path.read_text(encoding="utf-8")
    index_text = routing_index.render_index(routing_index.build_index(catalog, skill_md))
    try:
        rules_text = routing_rules(args.rules.read_text(encoding="utf-8"))
    except ValueError as e:
        parser.error(f"--rules: {e}")
    system_text = build_prompt(index_text, rules_text, "")[0]
    base_url = args.base_url or _default_base_url(args.provider)
    open_weights = args.provider == "ollama" or args.open_weights
    cap = None if prices is None else SpendCap(args.max_cost_usd, prices["input_usd_per_million"],
                                               prices["output_usd_per_million"])

    def summary(results, stats, wall) -> dict:
        returned = stats["models_returned"]
        model = {"provider": args.vendor or args.provider, "requested": args.model,
                 "returned": ", ".join(returned) if returned else args.model, "open_weights": open_weights}
        if args.digest:
            model["digest"] = args.digest
        cost = None
        if cap is not None:
            cost = {"usd": round(cap.spent_usd, 6), "price_source": prices["source"],
                    "price_date": prices["date"], "spend_cap_usd": args.max_cost_usd,
                    "input_usd_per_million": prices["input_usd_per_million"],
                    "output_usd_per_million": prices["output_usd_per_million"],
                    "unpriced_calls": cap.unpriced_calls}
        reliability = {k: v for k, v in stats.items() if k != "models_returned"}
        reliability["call_seconds"] = round(reliability["call_seconds"], 3)
        return {
            "date": args.date, "clawbio_commit": commit, "clawbio_tree_clean": clean,
            "harness_version": HARNESS_VERSION,
            "task_set": {"version": args.task_set_version,
                         "sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest()},
            "scope": {"tiers": ["routing"], "n_cases": len(cases), "n_repeats": args.repeats},
            "model": model,
            "settings": {"sampling": "provider defaults", "transport": f"clawbio.providers {args.provider}",
                         "base_url": "given" if args.base_url else "provider default",
                         "timeout_seconds": args.timeout, "tries_per_call": TRIES,
                         "system_prompt_sha256": _sha256(system_text)},
            "context_window": args.context_window,
            "hardware": {"gpu": args.gpu, "cpu": args.cpu, "memory_gb": args.memory_gb},
            "tokens": {"input": stats["prompt_tokens"], "output": stats["completion_tokens"]},
            "cost": cost,
            "wall_clock_seconds": round(wall, 3),
            "routing": aggregate(results),
            "agreement": agreement(results),
            "reliability": reliability,
        }

    empty = {"calls": 0, "transient_retries": 0, "failures": {}, "prompt_tokens": 0, "completion_tokens": 0,
             "calls_without_usage": 0, "call_seconds": 0.0, "stopped_by_spend_cap": False, "models_returned": []}
    if problems := run_summary.validate_summary(summary([], empty, 0.0)):
        parser.error("the run summary would be invalid: " + "; ".join(problems))

    if args.provider == "ollama":
        given = (f"records --context-window {args.context_window} as given"
                 if args.context_window else "records no context window, since --context-window was not given")
        print("warning: the context window is set on the Ollama server (OLLAMA_CONTEXT_LENGTH, or num_ctx "
              f"in a Modelfile), not per request. This run {given} and does not check it.", file=sys.stderr)

    provider = make_provider(args.provider, args.model, api_key=api_key or None, base_url=base_url,
                             timeout=args.timeout)
    start = time.perf_counter()
    try:
        results, stats = run_model(cases, provider, lambda request: build_prompt(index_text, rules_text, request),
                                   rules=rules, repeats=args.repeats, cap=cap)
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    record = summary(results, stats, time.perf_counter() - start)
    problems = run_summary.validate_summary(record)
    out.mkdir(parents=True, exist_ok=True)
    replies_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    summary_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.model} ({args.provider}), {len(cases)} cases x {args.repeats} repeat(s)")
    print(render_results(record["routing"]))
    failures = ", ".join(f"{k} {v}" for k, v in sorted(stats["failures"].items())) or "none"
    print(f"calls {stats['calls']}; retried after a rate limit or lost connection {stats['transient_retries']}; "
          f"failures: {failures}; tokens {stats['prompt_tokens']:,} in, {stats['completion_tokens']:,} out; "
          f"wall clock {record['wall_clock_seconds']:,.0f} s"
          + ("; stopped at the spend cap" if stats["stopped_by_spend_cap"] else "")
          + (f"; cost {record['cost']['usd']:.4f} USD" if record["cost"] else ""))
    print(f"wrote {summary_path} and {replies_path}")
    if problems:
        print("error: the summary fails validation: " + "; ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
