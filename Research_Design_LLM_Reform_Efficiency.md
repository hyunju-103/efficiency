# Research Design: LLM-Assisted Construction of a Structural Reform Database for Public Sector Efficiency Analysis

## Research objective

This study examines whether institutional and structural reforms are associated with subsequent improvements in public sector spending efficiency. The central empirical challenge is that many reforms are documented in narrative sources rather than in standardized cross-country datasets. IMF country reports provide unusually rich information on reform commitments, legal changes, implementation progress, delays, and structural benchmarks, but converting this material into a consistent country-year panel is labor intensive. We therefore use a large-language-model-assisted document coding framework to transform IMF reports into a transparent, auditable reform-event database that can be linked to DEA/SFA-based efficiency estimates.

The initial pilot uses 25 IMF reports for Egypt covering 2005–2026, including Article IV consultations, Selected Issues papers, program requests, and program reviews. Egypt is used to develop and validate the coding protocol before applying the same rules to a broader country sample.

## Reform taxonomy

The coding framework focuses on five reform areas that are closely related to the institutional environment in which public resources are allocated and implemented. Public Financial Management (PFM) covers budget formulation and execution, expenditure controls, cash and treasury management, accounting, fiscal reporting, arrears, audit, and fiscal transparency. Public Investment Management (PIM) covers project appraisal, selection, prioritization, capital budgeting, investment ceilings, monitoring, and ex-post evaluation. Public Procurement covers procurement legislation, e-procurement, competitive tendering, contract-award disclosure, and other value-for-money mechanisms. SOE Governance / State Footprint covers state ownership policy, SOE governance and disclosure, divestment, competitive neutrality, removal of preferential treatment, and state asset management. Fiscal Decentralization covers reforms that change fiscal authority or accountability across levels of government, including expenditure assignments, own-source revenues, local taxation, intergovernmental transfer systems, subnational borrowing or fiscal rules, and local PFM/accountability. Administrative decentralization or a simple reference to governorates, municipalities, or local governments is not coded as fiscal decentralization unless the document identifies an explicit fiscal change.

## LLM-assisted coding strategy

The LLM is used as an evidence extractor and classifier rather than as an evaluator. The pipeline first converts each PDF into page-level text and applies a broad retrieval dictionary to identify potentially relevant passages. Keyword retrieval is intentionally permissive: its role is to reduce the volume of text sent to the LLM, not to determine whether a reform occurred. Candidate passages retain page markers so that every extracted observation can be traced back to its source.

For each candidate passage, the LLM identifies the reform action, primary reform category, any secondary category, actor, policy year, implementation status, benchmark type and status, target/completion dates, responsible institution, a short supporting quotation, source page, and confidence. The implementation-status vocabulary is fixed ex ante: planned, official commitment, legally adopted, ongoing, partially implemented, implemented, delayed, not met, reversed, or unclear. The model must separately classify whether a passage describes a government action/commitment, an IMF recommendation, background information, or a monitoring update. IMF recommendations and purely descriptive passages are retained for audit purposes but excluded from the reform panel.

A key feature of the design is the distinction between the report year and the policy year. The policy year is taken from the event described in the text whenever possible; the report year is not silently substituted when timing is ambiguous. Repeated references to the same reform in later program reviews are also retained. They are linked into a reform lifecycle so that, for example, a measure can be observed first as a commitment, later as delayed or not met, and eventually as implemented. This avoids both double counting and the loss of implementation dynamics.

## Construction of reform measures

The LLM does not assign subjective reform-quality scores such as 0–3. Instead, quantitative variables are constructed mechanically from the coded evidence. At the country-year-category level, the database records the number of unique reforms, commitments/adoptions, full implementations, partial implementations, delays or non-observance, reversals, and whether any reform activity or implementation occurred. Where a target date or benchmark outcome establishes that a measure was due, an implementation-gap variable is defined as the number or share of due reforms that were not fully implemented. Missing target dates are not treated as evidence that a reform was due.

This approach generates three linked datasets: a report-level observation file preserving all source evidence; a reform-lifecycle file linking repeated observations of the same reform; and a country-year-category panel designed for merging with efficiency estimates. The lifecycle structure is particularly important because the empirical question is not simply whether a reform was mentioned, but whether adoption translated into implementation.

## Empirical linkage to efficiency

The resulting reform panel will be merged with country-year efficiency scores estimated using DEA and/or SFA. The baseline specification relates current efficiency to lagged reform implementation while controlling for country and year fixed effects and relevant macro-fiscal covariates:

\[
Efficiency_{it}=\alpha_i+\gamma_t+\beta Reform_{i,t-1}+X_{it}'\delta+\varepsilon_{it}.
\]

Reforms will first be examined separately by category, allowing the analysis to distinguish whether procurement, PIM, PFM, SOE, or fiscal-decentralization reforms have different associations with spending efficiency. Distributed lags can be used because institutional reforms may affect outcomes only after implementation. The implementation-gap measures provide an additional test of whether incomplete or delayed reforms are associated with weaker efficiency gains than fully implemented reforms. Unless a separate identification strategy is introduced, these estimates will be interpreted as conditional associations rather than causal effects.

## Validation and reproducibility

Before scaling the method to other countries, Egypt will serve as a validation sample. A stratified set of candidate passages will be manually coded, including both genuine reforms and likely false positives. Human and LLM coding will be compared for reform eligibility, category, policy year, and implementation status. Disagreements will be used to refine category definitions and exclusion rules, after which the coding protocol will be locked before expansion to the full sample. All raw LLM outputs, source quotations, page numbers, coding rules, and cached responses are retained, making the process reproducible and auditable. The contribution of the LLM is therefore not to replace researcher judgment, but to make systematic document coding feasible at scale while preserving traceable evidence for every observation.
