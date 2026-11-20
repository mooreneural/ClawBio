"""Build the routing index that the routing tier of the model evaluation puts in its prompt.

One entry per catalog skill: name, CLI alias, description, trigger keywords and the
entries of its SKILL.md "Do NOT fire" list, each with the skills it points to. The
catalog does not carry those lists, so they are parsed here from each SKILL.md.

Only the "## Trigger" section is read ("## Trigger Phrases" too), outside code fences.
A "Do NOT fire" entry is either an item of the list under a line such as
"**Do NOT fire when:**" or "**Do NOT fire when** the user wants:", or the rest of a
paragraph or bullet in which a sentence starts with "Do NOT fire" (dnasp writes one
sentence, pubmed-summariser one bullet). A wrapped item is one entry; a line that is
not indented under an item ends the list.

An entry points to the skills it names. A name in backticks with a hyphen
(`vcf-annotator`) counts anywhere, with the other names in backticks listed beside it
(`gwas-prs` / `prs`). After "use", "route to", "that is", "→" or "->", every name in
the list counts, in backticks or bare with a hyphen ("(use vcf-annotator)"). Other
words in backticks, such as `head` or `quant_method`, are commands or fields, and other
hyphenated words, such as "single-cell", are ordinary words. Names are not checked
against the catalog.

Running this file builds the index from skills/ and prints its size; it calls no model.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DoNotFire:
    text: str
    alternatives: tuple[str, ...] = ()  # skills the entry points to, in order


@dataclass(frozen=True)
class IndexEntry:
    name: str
    cli_alias: str | None
    description: str
    trigger_keywords: tuple[str, ...]
    do_not_fire: tuple[DoNotFire, ...]


_HEADING = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*")
_TRIGGER = re.compile(r"Triggers?\b")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_ITEM = re.compile(r"([ \t]*)(?:[-*+]|\d{1,9}[.)])(?:[ \t]+(.*))?")
# A line that opens a list: "Do NOT fire ...:" with or without bold.
_HEADER = re.compile(r"[ \t]*(?:\*\*|__)?Do NOT fire\b.*:(?:\*\*|__)?[ \t]*")
# "Do NOT fire" starting a sentence; '(see "Do NOT fire" item on trans)' is a mention, not a rule.
_RULE = re.compile(r"(?:^|(?<=[.!?])\s+)(\*\*|__)?Do NOT fire\b")

_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# A code span, or a bare hyphenated name; "k-mer/distance", "--n-sim" and "a-b.py" are not names.
_PIECE = re.compile(r"`([^`]*)`|(?<![\w.:/@`-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w/@`-]|\.\w)")
_JOIN = re.compile(r"\s*[,/]\s*(?:(?:or|and)\s+)?|\s+(?:or|and)\s+")
_POINTER = re.compile(r"(?:\b(?:use|route\s+to|that\s+is)|→|->)\s+(?:the\s+)?$", re.IGNORECASE)


def parse_do_not_fire(skill_md: str) -> list[DoNotFire]:
    """The "Do NOT fire" entries of a SKILL.md, in order."""
    entries = []
    for lines in _trigger_sections(skill_md):
        i = 0
        while i < len(lines):
            if _HEADER.fullmatch(lines[i]):
                texts, i = _list_items(lines, i + 1)
            elif not lines[i].strip() or _HEADING.fullmatch(lines[i]):
                i += 1
                continue
            else:
                block, i = (_item if _ITEM.fullmatch(lines[i]) else _paragraph)(lines, i)
                texts = [_rule(block)]
            entries += [DoNotFire(text, _alternatives(text)) for text in texts if text]
    return entries


def _trigger_sections(text: str) -> list[list[str]]:
    """The lines of each Trigger section; a fenced code block becomes one blank line."""
    sections: list[list[str]] = []
    current: list[str] | None = None
    level, fence = 0, ""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence:
            stripped = line.strip()
            if stripped.startswith(fence) and set(stripped) == {fence[0]}:
                fence = ""
            continue
        if opening := _FENCE.match(line):
            fence = opening.group(1)
            if current is not None:
                current.append("")
            continue
        heading = _HEADING.fullmatch(line)
        if heading and current is not None and len(heading.group(1)) <= level:
            current = None
        if heading and current is None and _TRIGGER.match(heading.group(2) or ""):
            current, level = [], len(heading.group(1))
            sections.append(current)
        elif current is not None:
            current.append(line)
    return sections


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _list_items(lines: list[str], i: int) -> tuple[list[str], int]:
    """The items of the list at or after lines[i] (blank lines may precede it or separate items)."""
    texts: list[str] = []
    level = None
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        item = _ITEM.fullmatch(lines[i])
        if not item or (level is not None and len(item.group(1)) > level):
            break
        level = len(item.group(1)) if level is None else level
        text, i = _item(lines, i)
        texts.append(text)
    return texts, i


def _item(lines: list[str], i: int) -> tuple[str, int]:
    """One list item with the lines indented under it, joined; no lazy continuation."""
    item = _ITEM.fullmatch(lines[i])
    level, parts = len(item.group(1)), [(item.group(2) or "").strip()]
    i += 1
    while i < len(lines) and lines[i].strip() and _indent(lines[i]) > level and not _HEADING.fullmatch(lines[i]):
        parts.append(lines[i].strip())
        i += 1
    return " ".join(parts).strip(), i


def _paragraph(lines: list[str], i: int) -> tuple[str, int]:
    parts = [lines[i].strip()]
    i += 1
    while i < len(lines) and lines[i].strip() and not any(
            p.fullmatch(lines[i]) for p in (_ITEM, _HEADING, _HEADER)):
        parts.append(lines[i].strip())
        i += 1
    return " ".join(parts), i


def _rule(block: str) -> str:
    """What follows "Do NOT fire" when a sentence of the block starts with it, else ""."""
    match = _RULE.search(block)
    if not match:
        return ""
    rest = block[match.end():]
    if match.group(1):  # "**Do NOT fire when:** text": the rule is the text after the label
        _, closed, after = rest.partition(match.group(1))
        rest = after if closed else rest
    return rest.lstrip(" :").strip()


def _alternatives(text: str) -> tuple[str, ...]:
    lists: list[tuple[bool, list[tuple[str, bool]]]] = []  # (after a pointer, [(name, in backticks)])
    end = None
    for piece in _PIECE.finditer(text):
        code, bare = piece.groups()
        name = bare if code is None else code if _NAME.fullmatch(code) else None
        if name is None:  # a code span that is not a name, such as `--n-sim`, ends a list
            end = None
            continue
        if end is not None and _JOIN.fullmatch(text, end, piece.start()):
            lists[-1][1].append((name, code is not None))
        else:
            lists.append((bool(_POINTER.search(text, 0, piece.start())), [(name, code is not None)]))
        end = piece.end()
    names = []
    for pointed, items in lists:
        if pointed:
            names += [name for name, _ in items]
        elif any(quoted and "-" in name for name, quoted in items):
            names += [name for name, quoted in items if quoted]
    return tuple(dict.fromkeys(names))


def build_index(catalog: Mapping, skill_md_by_name: Mapping[str, str]) -> list[IndexEntry]:
    """One entry per catalog skill, sorted by name; SKILL.md texts of other skills are ignored."""
    entries: dict[str, IndexEntry] = {}
    for skill in catalog["skills"]:
        name = skill["name"]
        if name in entries:
            raise ValueError(f"the catalog lists {name} twice")
        skill_md = skill_md_by_name.get(name)
        entries[name] = IndexEntry(
            name, skill.get("cli_alias"), skill.get("description") or "",
            tuple(skill.get("trigger_keywords") or ()),
            tuple(parse_do_not_fire(skill_md)) if skill_md is not None else ())
    return [entries[name] for name in sorted(entries)]


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_index(index: list[IndexEntry]) -> str:
    """One block per skill, blocks separated by a blank line; empty fields are left out."""
    blocks = []
    for entry in index:
        lines = [f"{entry.name} | alias: {entry.cli_alias or 'none'}"]
        if description := _one_line(entry.description):
            lines.append(description)
        if keywords := [k for k in map(_one_line, entry.trigger_keywords) if k]:
            lines.append("keywords: " + "; ".join(keywords))
        if entry.do_not_fire:
            lines += ["Do NOT fire:", *(f"- {_one_line(d.text)}" for d in entry.do_not_fire)]
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print the size of the routing index; calls no model.")
    parser.add_argument("--root", type=Path, default=REPO,
                        help="folder holding skills/catalog.json and skills/<name>/SKILL.md (default: this repo)")
    root = parser.parse_args(argv).root
    catalog = json.loads((root / "skills" / "catalog.json").read_text(encoding="utf-8"))
    skill_md = {}
    for skill in catalog["skills"]:
        path = root / "skills" / skill["name"] / "SKILL.md"
        if path.is_file():
            skill_md[skill["name"]] = path.read_text(encoding="utf-8")
    index = build_index(catalog, skill_md)
    entries = [d for entry in index for d in entry.do_not_fire]
    chars = len(render_index(index))
    print(f"skills: {len(index)}")
    print(f"SKILL.md files with Do NOT fire entries: {sum(1 for entry in index if entry.do_not_fire)}")
    print(f"Do NOT fire entries: {len(entries)}")
    print(f"entries naming an alternative: {sum(1 for d in entries if d.alternatives)}")
    print(f"characters: {chars}")
    print(f"characters / 4: {chars // 4}")


if __name__ == "__main__":
    main()
