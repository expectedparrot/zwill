# Pew W154 DIFF1 Source Context

This survey uses Pew Research Center American Trends Panel Wave 154 data, fielded in September 2024.

The imported battery is `DIFF1`, which asks:

> In general, how do you think men and women compare when it comes to each of the following?

The five imported items cover hobbies and personal interests, physical abilities, approach to parenting, expression of feelings, and workplace strengths.

Responses are coded as:

- `1`: Men and women are basically similar
- `2`: Men and women are basically different

The example uses normalized files from `llm-survey-priors/papers/microdata_twins/data/computed_objects/normalized`. Respondent weights and covariates are imported from `W154_DIFF1_respondents.csv`; question wording and option labels are imported from `W154_DIFF1_metadata.json`.
