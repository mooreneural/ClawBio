## What This Skill Does

The NutriGx Advisor generates a **personalised nutrition report** from consumer
genetic data (23andMe, AncestryDNA raw files or VCF). It interrogates a curated
set of nutritionally-relevant SNPs drawn from GWAS Catalog, ClinVar, and
peer-reviewed nutrigenomics literature, then translates genotype calls into
actionable dietary and supplementation guidance — all computed locally.

**Key outputs**
- Markdown nutrition report with risk scores and recommendations
- Radar chart of nutrient risk profile
- Gene × nutrient heatmap
- Reproducibility bundle (`commands.sh`, `environment.yml`, SHA-256 checksums)

---

## Trigger Phrases

The Bio Orchestrator should route to this skill when the user says anything like:

- "personalised nutrition", "nutrigenomics", "diet genetics"
- "what should I eat based on my DNA"
- "nutrient metabolism", "vitamin absorption genetics"
- "MTHFR", "APOE", "FTO", "BCMO1", "VDR", "FADS1/2"
- "folate", "omega-3", "vitamin D", "caffeine metabolism", "lactose", "gluten"
- Input files: `.txt` or `.csv` (23andMe), `.csv` (AncestryDNA), `.vcf`

---

## Curated SNP Panel

### Macronutrient Metabolism
