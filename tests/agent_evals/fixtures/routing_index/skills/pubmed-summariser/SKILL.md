## Core Capabilities

1. **PubMed query**: Search by gene name (e.g. `BRCA1`) or disease term (e.g. `type 2 diabetes`)
2. **Structured extraction**: Title, authors, journal, publication date, complete abstract, labeled sections, PMID and PubMed URL
3. **Summary choice**: First-sentence excerpts by default, or explicit OpenAI/Ollama generation from complete abstracts
4. **Saved output**: JSON source records and summary provenance, Markdown and HTML reports, and a reproducibility bundle

## Trigger

- Fire when asked for a PubMed research briefing or recent papers about a gene or disease.
- Do NOT fire for patient interpretation, systematic reviews, full-text extraction, or multi-source evidence synthesis.

## Scope

One task: produce a briefing from a bounded PubMed search. Each paper is summarized separately. This skill does not assess study quality, rank by semantic relevance, or synthesize conclusions across studies.

## Input Formats
