# Practitioner report: Cross-survey survey digital twins

## 1. Executive summary

Use these digital twins as decision support, not as a blanket substitute for field measurement. The evidence is strongest for clear, binary policy-attitude questions and weakest for exact answers on nuanced multi-point vignette outcomes.

The practical answer is:

- **For low-stakes, reversible, internal, or time-sensitive decisions:** These twins are useful, especially when the new question resembles the tested Pew climate-policy items. For the six binary climate-policy questions, the stronger model, **openai:gpt-5.5**, picked the real respondent’s answer **83.8%** of the time and beat both random guessing and guessing from the group average.
- **For medium-stakes decisions:** Use twins for direction and prioritization, but review failure patterns and consider a small validation sample when the question has more than two options, rare answer categories, sensitive wording, or model disagreement.
- **For high-stakes, public, publishable, expensive-to-reverse, or policy-critical decisions:** Use twin output as one input, not the final evidence. Field data or a stronger validation study is worth the cost.
- **For probability-sensitive decisions:** Be especially careful. Calibration means that when a twin says it is 70% sure, it should be right about 70% of the time. The OpenAI model was better calibrated overall than the Gemini model, but both produced some very confident wrong guesses. These failures can distort rankings, cutoffs, and “which respondents are most likely to…” targeting.

The strongest caveat is **overconfident failure on minority or rare response options**. On the climate-policy questions, the twins often correctly identified broad majority support, but sometimes assigned only 1–5% probability to the real answer when a respondent opposed a proposal. On the seven-point vignette blame question, the Gemini model sometimes assigned zero probability to the actual answer, including cases where the true answer was far from the prediction.

The main practitioner takeaway is heterogeneity: **the twins worked well on some question types and poorly on others.** Do not summarize this benchmark as simply “digital twins work” or “digital twins fail.” They are much more credible for clear two-option policy preferences than for exact seven-point ordinal judgments about a vignette where the full vignette prose was not available.

---

## 2. Study setup

### Survey sources and contexts

This benchmark used five recorded `zwill` twin studies under benchmark name `cross_survey_twin_benchmark_seed789`.

The surveys were:

1. **w158_ccpolicy**
   - Context: A nationally representative Pew Research Center American Trends Panel survey of U.S. adults fielded in **October 2024**.
   - Topic: Climate-change policy proposals.
   - Respondents in source survey: **9,214**.
   - Questions in source survey: **6**.
   - Held out: six binary favor/oppose questions.
   - Scored rows: **600 per model**, 100 per held-out question.
   - This is the strongest evidence in the benchmark because it has many more scored rows than the other studies.

2. **w157_skillimp**
   - Context: A nationally representative Pew Research Center American Trends Panel survey of U.S. adults fielded in **October 2024**.
   - Topic: Importance of worker skills.
   - Respondents in source survey: **5,333**.
   - Questions in source survey: **9**.
   - Held out: one five-option importance question.
   - Study design: 20 sampled respondents, 5 context questions, seed 789, complete cases, stratified by actual answer, not balanced to actual distribution.
   - Scored rows: **20 per model**.

3. **w152_humanvai**
   - Context: Pew Research Center American Trends Panel W152. The provided artifact does not include a field date.
   - Topic: Views of whether AI would perform job tasks better, worse, or about the same as people.
   - Respondents in source survey: **5,318**.
   - Questions in source survey: **8**.
   - Held out: one four-option AI medical diagnosis question.
   - Study design: 20 sampled respondents, 5 context questions, seed 789, complete cases, stratified by actual answer, not balanced to actual distribution.
   - Scored rows: **20 per model**.

4. **w163_sm9**
   - Context: A nationally representative Pew Research Center American Trends Panel survey of U.S. adults fielded in **February 2025**.
   - Topic: Perceptions of social media.
   - Respondents in source survey: **5,020**.
   - Questions in source survey: **5**.
   - Held out: one four-option social-media attitude question.
   - Study design: 20 sampled respondents, 4 context questions, seed 789, complete cases, stratified by actual answer, not balanced to actual distribution.
   - Scored rows: **20 per model**.

5. **dataverse_dnzt11_dnzt11_vignette_outcomes**
   - Context: U.S. online respondent survey experiment about local-government service failures and government downsizing. Respondents read two vignettes: one about trash/recycling not being picked up after Department of Public Works downsizing, and one about termination of vacuum leaf collection service in Clark County.
   - Important caveat: the replication package provides summarized condition labels, **not the full vignette prose**.
   - Respondents in source survey: **1,199**.
   - Questions in source survey: **6**.
   - Held out: one seven-option blame-attribution question.
   - Study design: 20 sampled respondents, 5 context questions, seed 789, complete cases, stratified by actual answer, not balanced to actual distribution.
   - Scored rows: **20 per model**.

Weighting status was not provided in the recorded artifacts. Treat the benchmark as a test of respondent-level prediction on the sampled complete cases, not as a weighted population-estimate validation.

### Held-out questions

The held-out questions used human-readable answer labels, not just numeric code labels.

#### w158_ccpolicy: climate policy, six binary questions

Each question had options:

- Favor
- Oppose

The held-out questions were:

1. **a**: “Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere”

2. **b**: “Do you favor or oppose the following proposals to reduce the effects of global climate change? Taxing corporations based on the amount of carbon emissions they produce”

3. **c**: “Do you favor or oppose the following proposals to reduce the effects of global climate Providing a tax credit to encourage businesses to develop technology which captures and stores carbon emissions so they do not enter the atmosphere”

4. **d**: “Do you favor or oppose the following proposals to reduce the effects of global climate change? CCPOLICY_d_W158. Requiring power plants to eliminate all carbon emissions by 2040”

5. **e**: “Do you favor or oppose the following proposals to reduce the effects of global climate change? Requiring oil and gas companies to seal methane gas leaks from oil wells”

6. **f**: “Do you favor or oppose below proposals to reduce effects of global climate change? Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation”

Questions **c** and **d** include visible wording/codebook artifacts in the recorded text. That does not invalidate the scored benchmark, but it is a reminder to inspect question wording carefully before applying twins to new survey items.

#### w157_skillimp: worker skills, five options

Held-out question:

“Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts”

Options:

- Extremely important
- Very important
- Somewhat important
- Not too important
- Not at all important

#### w152_humanvai: AI and medical diagnosis, four options

Held-out question:

“Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis”

Options:

- AI would do this better
- AI would do this worse
- AI would do this about the same
- Not sure

#### w163_sm9: social media and underrepresented groups, four options

Held-out question:

“How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups”

Options:

- Very well
- Somewhat well
- Not too well
- Not at all well

#### dataverse vignette outcomes: blame attribution, seven options

Held-out question:

“Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.”

Options:

- Strongly disagree
- Disagree
- Somewhat disagree
- Neither agree nor disagree
- Somewhat agree
- Agree
- Strongly agree

### Models tested

Two models were tested:

- **openai:gpt-5.5**
- **google:gemini-2.5-pro**

Across the benchmark, the OpenAI model was the safer default for practitioner use because it had slightly higher mean accuracy and substantially better confidence quality. Gemini sometimes had equal or higher exact-answer accuracy on small samples, but it produced more severe overconfident probability failures.

### Quality checks

The recorded import and extraction checks were clean:

- w158_ccpolicy: 1,200 rows imported and extracted; issue count 0.
- w157_skillimp: 40 rows imported and extracted; issue count 0.
- w152_humanvai: 40 rows imported and extracted; issue count 0.
- w163_sm9: 40 rows imported and extracted; issue count 0.
- dataverse vignette outcomes: 40 rows imported and extracted; issue count 0.

All five surveys had `has_context: true`, no open quarantine issues, and committed survey records. No malformed-response issues were recorded in the imported results.

### Baselines used

Two baselines matter for practitioner interpretation:

1. **Random guessing**
   - This means assigning equal probability to each answer option.
   - For a two-option question, random guessing has a 50% chance of picking the right answer.
   - For a four-option question, random guessing has a 25% chance.
   - Beating random guessing means the twin is doing better than chance.

2. **Group-average guessing**
   - This means guessing based on how the whole sample answered the held-out question.
   - Beating group-average guessing is more important than beating random guessing because it shows the twin used respondent-specific context, not just the overall popularity of an answer.
   - This baseline is only available for observed held-out questions. For genuinely new questions, you do not yet know how the whole group answered, so this baseline is unavailable.

---

## 3. Overall performance

The twins are practically useful when the question is clear, the answer space is small, and respondent context carries a strong signal. They are much less reliable for exact answers on longer ordinal scales or when rare options matter.

Accuracy here means: **how often the twin’s highest-probability answer matched the real respondent’s answer.**

### Summary by survey

| Survey / question family | Best practical read | OpenAI accuracy | Gemini accuracy | Group-average accuracy | Random guessing |
|---|---:|---:|---:|---:|---:|
| w158 climate policy, six binary favor/oppose items | Strongest evidence; usable for low-stakes and some medium-stakes directional work | 83.8% | 78.8% | 79.5% | 50.0% |
| w157 interpersonal skills, five options | Promising but small sample; exact intensity and rare low-importance options need caution | 65.0% | 70.0% | 45.0% | 20.0% |
| w152 AI medical diagnosis, four options | Moderate directional value; not enough for high-stakes claims | 60.0% | 50.0% | 30.0% | 25.0% |
| w163 social media voice, four options | Moderate value, mostly for coarse direction | 60.0% | 55.0% | 50.0% | 25.0% |
| Dataverse vignette blame, seven options | Weak for exact response; do not rely on it for vignette-effect conclusions | 30.0% | 40.0% | 30.0% | 14.3% |

The w158 climate study carries much more evidentiary weight than the other four studies because it scored **600 rows per model**. The other four studies scored only **20 rows per model**, so their results should be treated as directional signals rather than stable performance estimates.

### Model-level pattern

Across the five surveys:

- **openai:gpt-5.5**
  - Mean accuracy across surveys: **59.8%**
  - Better confidence quality overall.
  - Strongest result: w158 climate policy, **83.8%** accuracy and good calibration.
  - Weakest result: Dataverse seven-point vignette item, **30.0%** accuracy and worse than the group average on probability quality.

- **google:gemini-2.5-pro**
  - Mean accuracy across surveys: **58.8%**
  - Higher risk of overconfident wrong probabilities.
  - Strong exact-answer results on some small samples, such as w157 at **70.0%**, but poor confidence quality there because a few actual answers received near-zero or zero probability.
  - Very poor probability quality on the seven-point vignette item.

For practical use: if you need a single default model from this benchmark, prefer **openai:gpt-5.5**, especially when you will use probabilities. If you use Gemini, treat its confidence numbers cautiously and inspect cases where it assigns very low probabilities to plausible answer options.

### Confidence quality

Confidence quality means: **whether the model’s stated confidence matches reality. When a calibrated twin says 70% sure, it should be right about 70% of the time.**

This matters whenever the output will be used for:

- ranking respondents,
- setting probability cutoffs,
- identifying “likely supporters” or “likely opponents,”
- estimating uncertainty,
- deciding whether a difference is large enough to act on.

The strongest calibration result was the OpenAI model on the w158 climate-policy questions. Its confidence was close to reality overall, with only a small average calibration gap. But even there, the model produced very confident misses: for several actual “Oppose” answers, it predicted “Favor” and assigned only 1–4% probability to the true answer.

Gemini’s confidence quality was more problematic. On the w157 skill-importance item and the Dataverse vignette item, it sometimes assigned **0% probability** to the actual answer. That is a red flag for any workflow that relies on probability values.

---

## 4. Where twins worked well

### 4.1 Clear binary climate-policy questions worked best

The best-supported use case in this benchmark is a clear binary favor/oppose policy question similar to the w158 climate-policy items.

For the six climate questions, the OpenAI model:

- picked the actual answer **83.8%** of the time,
- assigned an average probability of **79.1%** to the real answer,
- beat random guessing by a large margin,
- beat group-average guessing overall, which means it used respondent-specific information beyond the overall popularity of “Favor.”

This is strong enough for many low-stakes uses, such as:

- internal message testing,
- prioritizing which policy ideas to explore,
- deciding whether a result is worth fielding,
- generation of directional expectations before a real survey,
- sensitivity checks across related policy proposals.

It is not, by itself, enough for a public claim about population support or subgroup differences unless the stakes are low and the decision is reversible.

#### Strongest individual climate-policy items

The clearest respondent-specific wins came from items where the group was more divided or where respondent context likely carried more signal.

| Held-out question | OpenAI accuracy | Group-average accuracy | Practical interpretation |
|---|---:|---:|---|
| “Taxing corporations based on the amount of carbon emissions they produce” | 79% | 70% | Strong respondent-specific value; useful for directional decisions. |
| “Requiring power plants to eliminate all carbon emissions by 2040” | 79% | 62% | Strong respondent-specific value; both models beat group average. |
| “Providing a tax credit to encourage businesses to develop technology which captures and stores carbon emissions...” | 85% | 81% | Good OpenAI result; Gemini did not beat group average. |
| “Providing a tax credit to Americans who improve their home energy efficiency...” | 88% | 86% | High accuracy, but rare opposition was often missed. |
| “Requiring oil and gas companies to seal methane gas leaks from oil wells” | 83% | 87% | High accuracy, but much of the result reflects broad support. |
| “Planting about a trillion trees around the world...” | 89% | 91% | High accuracy, but group-average guessing did even better; weak evidence of individual-level value. |

The distinction between high accuracy and beating the group average matters. On the trillion-trees item, a model can look good mainly because most people favor the proposal. That may still be useful for predicting the aggregate direction, but it is less evidence that the twin captured individual-level differences.

### 4.2 Divisive binary policy items were more informative than lopsided ones

The climate item “Requiring power plants to eliminate all carbon emissions by 2040” is a good example. The OpenAI model scored **79%** accuracy and the Gemini model scored **80%**, while group-average guessing scored only **62%**. That suggests the respondent context helped the twins distinguish supporters from opponents.

By contrast, on the item “Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere,” group-average guessing scored **91%** because support was highly prevalent in the scored sample. The OpenAI model scored **89%**. That is still a high accuracy number, but it is not strong evidence that the twin improved on knowing the group tendency.

Practical implication: for new binary policy questions, twins are most valuable when you need to distinguish people or subgroups, not merely recover a lopsided majority answer.

### 4.3 Some four-option attitude items showed useful directional signal

The four-option Pew attitude questions were smaller studies, so the evidence is less stable, but they were not failures.

For the AI medical diagnosis question:

“Thinking about artificial intelligence (AI) today, do you think AI would do better, worse or about the same as people whose job it is to... Make a medical diagnosis”

OpenAI picked the real answer **60%** of the time, compared with **30%** for group-average guessing and **25%** for random guessing. Gemini picked the real answer **50%** of the time. Both models assigned more probability to the real answer than random guessing and more than group-average guessing.

For the social-media question:

“How well do you think each of the following statements describes social media? Social media... Helps give a voice to underrepresented groups”

OpenAI picked the real answer **60%** of the time, compared with **50%** for group-average guessing and **25%** for random guessing. Gemini picked the real answer **55%** of the time.

Practical implication: for four-option attitude questions, twins may be useful for low-stakes directional work, especially if you collapse categories into broader interpretations such as favorable vs. unfavorable or optimistic vs. skeptical. Do not assume the exact option will be correct often enough for high-stakes individual-level classification.

### 4.4 The five-option interpersonal-skills question showed promise, but with small-sample caution

The held-out question was:

“Now thinking about workers in general, how important do you think each of the following is for a worker to be successful in today's economy? Interpersonal skills, such as getting along with people and resolving conflicts”

OpenAI accuracy was **65%** and Gemini accuracy was **70%**, compared with **45%** for group-average guessing and **20%** for random guessing. On exact answer choice, that looks promising.

However, the sample was only 20 respondents per model, and the Gemini model’s probability quality was poor because of extreme misses. OpenAI was the more usable model if probability values matter.

Practical implication: twins may help anticipate broad views on importance-type questions, but exact distinctions between “Extremely important” and “Very important,” or between low-frequency categories such as “Not too important” and “Not at all important,” need validation.

---

## 5. Where twins failed

### 5.1 Rare or minority options were often missed

The most important failure mode is not random noise. It is systematic overconfidence about common or plausible answers when the respondent actually chose a rarer option.

On the climate-policy items, this showed up mostly as actual **“Oppose”** responses predicted as **“Favor.”**

Examples from the scored rows:

- For “Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere,” several respondents who actually answered **Oppose** were predicted as **Favor** with only **1–2%** probability assigned to the true answer by the OpenAI model.
- For “Providing a tax credit to Americans who improve their home energy efficiency, such as by installing heat pumps or adding insulation,” some actual **Oppose** answers were predicted as **Favor** with only **2%** probability assigned to the actual answer.
- For “Requiring oil and gas companies to seal methane gas leaks from oil wells,” an actual **Oppose** answer was predicted as **Favor** with only **3%** probability assigned to the actual answer.

This matters if the practitioner’s decision depends on identifying opponents, skeptics, or rare subgroups. High overall accuracy can hide poor performance on the minority option.

### 5.2 High accuracy sometimes came from majority-class answers

On the climate item:

“Do you favor or oppose the following proposals to reduce the effects of global climate change? Planting about a trillion trees around the world to absorb carbon emissions in the atmosphere”

OpenAI accuracy was **89%**, which looks excellent. But group-average guessing scored **91%**. In the scored sample, the broad tendency toward “Favor” was so strong that a simple group-average strategy was hard to beat.

Practical implication: this twin output can still be useful if you only need a broad directional read. But it is weaker evidence for individual-level prediction or subgroup targeting.

### 5.3 Ordinal response scales produced adjacent-category confusion

The five-option and four-option ordinal items often failed by choosing an adjacent category:

- “Extremely important” vs. “Very important”
- “Very well” vs. “Somewhat well”
- “Not too well” vs. “Not at all well”
- “AI would do this better” vs. “AI would do this about the same” or “AI would do this worse”

Adjacent-category errors may be acceptable for some low-stakes uses if the decision only needs a coarse direction. They are not acceptable if the exact intensity category matters.

For the social-media item, several misses were of this kind:

- Actual **Very well** predicted as **Somewhat well**
- Actual **Somewhat well** predicted as **Very well**
- Actual **Not too well** predicted as **Somewhat well**

For the skill-importance item, misses included:

- Actual **Very important** predicted as **Extremely important**
- Actual **Somewhat important** predicted as **Extremely important**
- Actual **Not too important** predicted as **Very important**

The last two are more serious because they cross larger substantive distances than a simple adjacent-category distinction.

### 5.4 The seven-point vignette blame item was not reliable enough for practical conclusions

The weakest use case was the Dataverse vignette question:

“Based on Vignette 1, city leaders (Mayor and City Council) are to blame for the trash and recycling bins not being picked up.”

This item had seven response options from **Strongly disagree** to **Strongly agree**.

On exact answer accuracy:

- OpenAI: **30%**
- Gemini: **40%**
- Group-average guessing: **30%**
- Random guessing: **14.3%**

The raw accuracy for Gemini was above group average, but its probability quality was poor. Both models were worse than group-average guessing on the probability-quality comparison. OpenAI also did not clearly beat random guessing on the main probability-quality score.

This failure is not surprising given the design caveat: the available context included summarized condition labels, not the full vignette prose. Vignette judgments can depend heavily on small wording details, actor responsibility, and treatment framing. Those details are exactly where a digital twin needs strong context.

Very confident misses included:

- Actual **Strongly agree** predicted as **Strongly disagree** with **0%** probability assigned to the actual answer by Gemini.
- Actual **Neither agree nor disagree** predicted as **Disagree** with **0%** probability assigned to the actual answer by Gemini.
- Actual **Strongly disagree** predicted as **Somewhat agree** with **1%** probability assigned to the actual answer by Gemini.
- Actual **Agree** predicted as **Disagree** with **2%** probability assigned to the actual answer by OpenAI.

Practical implication: do not use this configuration to estimate vignette treatment effects, blame attribution, or exact seven-point response distributions without fuller validation and full vignette text.

### 5.5 Gemini’s probability values were less safe than its top answers

Gemini sometimes produced acceptable or even better exact-answer accuracy on small samples, but the probability values were frequently less trustworthy.

Examples:

- On the worker-skills item, Gemini accuracy was **70%**, but two actual answers received **0%** probability. That made its overall confidence quality much worse than OpenAI’s despite the higher exact-answer accuracy.
- On the Dataverse vignette item, Gemini accuracy was **40%**, but the model produced severe zero-probability misses and worse-than-random probability quality on some measures.
- On the AI medical diagnosis item, Gemini had higher average top confidence than OpenAI but lower accuracy and worse calibration.

Practical implication: if using Gemini, avoid treating its probability outputs as calibrated. Use it as a second model for disagreement checks, not as the primary probability engine, unless you add calibration or validation.

---

## 6. Practical use recommendations

### Recommended use by stakes

#### Low stakes, reversible, internal, or time-sensitive decisions

Use the twins when:

- the question is similar to tested question families,
- the result is strong,
- exact individual classification is not required,
- rare options are not central to the decision,
- the cost of being wrong is low.

Good examples:

- internal prioritization of which climate-policy proposals to field,
- early-stage expectation setting,
- rapid exploration of likely direction,
- identifying which questions may need a real survey,
- rough comparison across alternative binary policy wordings.

For this tier, a strong OpenAI twin result plus a quick sanity check may be enough to act.

Recommended sanity checks:

1. Compare OpenAI and Gemini outputs. Treat model disagreement as a warning flag.
2. Inspect whether the predicted result depends mostly on a dominant answer option.
3. Look at whether rare options are substantively important.
4. If probabilities matter, check for very high-confidence predictions near 90–99%.

#### Medium stakes or moderately costly errors

Use twins for direction, but add validation when:

- the result will affect resource allocation,
- the question has four or more answer options,
- response intensity matters,
- the expected effect is small,
- subgroup differences matter,
- model confidence is high but model agreement is low.

For medium-stakes use, twins can help decide the direction of a decision, but a small validation sample or additional held-out test is worth considering.

Examples:

- deciding which of several messages to develop further,
- choosing question wording for a larger study,
- estimating likely support before a field experiment,
- prioritizing policy concepts for stakeholder review.

#### High stakes, public, publishable, expensive-to-reverse, or policy-critical decisions

Use twins as supporting evidence only.

High-stakes examples include:

- public claims about population opinion,
- publishable estimates,
- policy recommendations,
- targeting interventions at individuals or subgroups,
- decisions where a wrong inference would be costly or hard to reverse,
- claims about treatment effects in vignette experiments.

For this tier, run fuller validation or field measurement. The benchmark does not justify replacing a real survey for high-stakes claims.

#### Probability-sensitive decisions

Regardless of stakes, inspect confidence quality if you will use probabilities for ranking, thresholds, or uncertainty.

This benchmark contains a red-flag pattern: some predictions were very confident and flat wrong. That is especially dangerous if the workflow says things like:

- “contact everyone above 80% predicted support,”
- “drop any option below 10% predicted interest,”
- “rank respondents by likelihood of opposition,”
- “treat a 90% prediction as nearly certain.”

For probability-sensitive use, consider:

- calibrating probabilities on held-out data,
- capping extreme probabilities,
- using broader categories,
- checking the worst-confidence misses,
- avoiding zero probabilities for plausible options,
- using model disagreement as an uncertainty flag.

### Recommended use by question type

#### Clear binary policy attitudes

Trust level: **highest in this benchmark**

Use for low-stakes and some medium-stakes directional decisions, especially when the item resembles the w158 climate-policy questions.

Best-supported pattern:

- two options,
- familiar policy topic,
- clear favor/oppose wording,
- strong respondent context,
- not solely driven by a lopsided majority.

Still validate when:

- minority opposition matters,
- subgroup estimates matter,
- public reporting is planned,
- the question wording is materially different from tested items.

#### Four-option attitude questions

Trust level: **moderate, with small-sample caution**

Use for directional reads, not exact individual classification.

The AI and social-media items suggest the twins can beat random guessing and often beat group-average guessing, but the evidence comes from only 20 scored respondents per model per survey.

Best use:

- broad sentiment,
- coarse grouping,
- internal planning,
- early-stage hypothesis generation.

Validate when:

- exact category matters,
- “Not sure” is central,
- adjacent categories have different operational implications,
- the result will be public or costly.

#### Five-option importance questions

Trust level: **promising but not enough evidence for high confidence**

The interpersonal-skills item showed good exact-answer accuracy on a small sample, but failures clustered around intensity and rare low-importance responses.

Use for:

- broad importance ranking,
- internal expectations,
- deciding whether a topic is worth measuring.

Be careful with:

- exact intensity categories,
- rare low-importance responses,
- probability values from Gemini.

#### Seven-point vignette outcomes

Trust level: **low in this benchmark**

Do not rely on the tested configuration for exact seven-point outcomes, treatment-effect conclusions, or blame attribution.

The Dataverse vignette study is also limited because the artifact had summarized condition labels rather than the full vignette prose. For vignette studies, the exact text is often the treatment. Without it, the twin lacks key information.

Use only for:

- very rough internal exploration,
- identifying whether the setup needs better context,
- designing a validation study.

Do not use alone for:

- estimating vignette effects,
- claiming support or blame levels,
- public-facing findings,
- high-stakes policy conclusions.

### How to apply twins to genuinely new survey questions

For new questions, do not claim that a twin beats group-average guessing unless you have held-out real answers for that new question. The group-average baseline requires knowing how the real sample answered.

A practical workflow:

1. **Classify the new question**
   - Binary policy attitude: strongest analog in this benchmark.
   - Four-option general attitude: moderate analog.
   - Five- or seven-point ordinal item: more caution.
   - Vignette outcome: high caution unless full vignette text and relevant context are included.

2. **Check whether respondent context is likely informative**
   - The twins need context questions that plausibly predict the held-out response.
   - If the context is mostly unrelated, expect weaker performance.

3. **Run at least two models if the decision warrants it**
   - Use OpenAI as the safer default from this benchmark.
   - Use Gemini as a disagreement check, but be cautious with its probabilities.

4. **Inspect answer-option rarity**
   - If a rare option matters, do not rely on high overall accuracy.
   - Ask specifically whether the twin can identify that rare option.

5. **Use probabilities cautiously**
   - If the top answer is all you need for a low-stakes decision, a strong result may be enough.
   - If probability thresholds matter, run calibration checks.

6. **Validate when the decision moves up the stakes ladder**
   - For medium stakes, consider a small validation sample.
   - For high stakes, field a real survey or run a larger held-out validation.

---

## 7. Next study recommendations

### Quick sanity checks worth doing before routine use

These are relatively low-cost checks that would materially improve confidence:

1. **Expand the small 20-respondent studies**
   - Four of the five studies used only 20 scored respondents per model.
   - The direction is informative, but performance estimates are unstable at that size.
   - Increasing held-out respondent counts would clarify whether the 50–70% accuracy results are durable.

2. **Add more held-out questions per survey**
   - w158 is stronger because it tested six related items.
   - The other studies tested only one held-out question each.
   - More held-out items would reveal whether performance is specific to one item or general to the question family.

3. **Review rare-option performance separately**
   - Overall accuracy is not enough.
   - Track performance for minority answers such as “Oppose,” “Not sure,” “Not at all important,” and scale endpoints such as “Strongly disagree” or “Strongly agree.”

4. **Compare exact-category and coarse-category performance**
   - For ordinal scales, exact answers may be too demanding for some practical uses.
   - Report both exact prediction and broader groupings when the decision only needs a coarse read.

5. **Keep model disagreement visible**
   - Display OpenAI and Gemini side by side.
   - Treat large disagreement as a validation trigger.

6. **Inspect overconfident misses**
   - Before using probability cutoffs, review cases where the model assigned very low probability to the real answer.
   - This is especially important for Gemini based on these artifacts.

### Validation worth the cost for higher-stakes use

For higher-stakes decisions, the next study should include:

1. **Larger held-out samples**
   - Especially for the four smaller studies currently based on 20 respondents per model.

2. **Subgroup analysis**
   - Test whether performance differs by politically or demographically relevant groups.
   - This matters because a model can perform well overall while failing for a subgroup.

3. **More surveys and more question families**
   - The benchmark currently covers climate policy, worker skills, AI performance perceptions, social media perceptions, and one local-government vignette outcome.
   - Extend to new topics before generalizing.

4. **Calibration checks by model and question type**
   - OpenAI had better confidence quality overall, but still had extreme misses.
   - Gemini needs special attention before using probabilities.

5. **Full vignette text for vignette studies**
   - The Dataverse study had summarized condition labels rather than full vignette prose.
   - For vignette experiments, include the actual text shown to respondents.

6. **Prompt and probability-output improvements**
   - Consider prompts or post-processing that discourage zero probabilities for plausible options.
   - For ordinal scales, prompt the model to respect ordered categories and uncertainty between adjacent options.
   - Validate any prompt change with held-out answers before relying on it.

7. **Weighting and population-estimate validation**
   - The provided artifacts did not include weighting status.
   - If the goal is population estimation rather than respondent-level prediction, validate weighted aggregate estimates directly.

---

## Bottom line

These survey-built digital twins can help practitioners make decisions, but only when used in the right lane.

The most defensible current use is **low-stakes or medium-stakes directional work on clear binary policy questions**, especially questions similar to the Pew climate-policy items. The OpenAI model’s w158 result is strong enough to be operationally useful for internal decision support.

The least defensible use is **exact prediction on complex multi-point vignette outcomes**, especially without full stimulus text. The Dataverse vignette result should trigger additional validation, not action by itself.

The main operating rule is:

**Use twins where the benchmark shows question-type fit, keep probability calibration visible, and pay for extra validation when the decision is costly, public, irreversible, subgroup-sensitive, or dependent on rare answer options.**
