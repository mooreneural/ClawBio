## Trigger

**Fire this skill when:**

- The user asks about methylation, MTHFR variants, folate cycle, or
  homocysteine risk from a genotype file.
- A raw SNP file (23andMe / ADNTRO / Ancestry format) is provided and the
  clinical question involves methylation, BH4, dopamine/serotonin synthesis
  capacity, or neurodevelopmental contexts (ADHD, depression, anxiety).
- The upstream workflow (PharmGx Reporter, NutriGx Advisor) has flagged
  MTHFR or MTRR and the clinician needs the full methylation panel.
- Keywords present: `methylation`, `MTHFR`, `BH4`, `folate cycle`,
  `metilación`, `ciclo de metilación`, `homocysteine`, `5-MTHF`,
  `methylcobalamin`, `neurotransmitter synthesis`, `dopamine upstream`.

**Do NOT fire this skill when:**

- The question is purely about folic acid supplementation without a genotype file.
- The user is asking about MTHFR in the context of thrombophilia/clotting
  only — use PharmGx Reporter for warfarin/anticoagulation questions.
- Only N-GENE polygenic risk data is available (no raw SNP file) — this skill
  requires genotype-level input; PRS percentiles are not sufficient.
- The SNP file format is VCF, FASTQ, BAM, or PLINK binary — these require
  preprocessing before this skill can run.
- The clinical question is exclusively pharmacogenomic (CYP enzymes) — use
  PharmGx Reporter instead.

---

## Workflow

1. **Receive input** — Accept either a raw genotype file path or a pre-parsed
   `snp_dict`. If a file is provided, call `parse_genotype_file()` to extract
   the rsID → genotype mapping.
