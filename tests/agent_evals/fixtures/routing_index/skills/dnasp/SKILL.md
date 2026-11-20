# DnaSP

## Trigger

Fire when a user requests population-genetic analysis of aligned DNA, a supported
VCF, or a DnaSP-compatible statistic listed below. Do NOT fire for sequence
alignment, read mapping, haplotype phasing, clinical advice, or significance
tests other than the coalescent P-values `--n-sim` gives for D, R2 and Fs.

## Scope

Analyse genetic variation in supplied alignments using 16 selected DnaSP methods.
This is not a complete replacement for the DnaSP GUI or all its analysis modes.
Read [the statistical reference](docs/index.md) for definitions, exclusions,
source conventions, file formats, examples and release validation evidence.
