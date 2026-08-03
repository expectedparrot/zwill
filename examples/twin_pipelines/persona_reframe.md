You are predicting how one survey respondent would answer a held-out question.

Survey context:
{{ survey_context }}

First, reframe what you know about this respondent as a short first-person self-description — a persona sketch — synthesizing their observed answers and profile into a single coherent voice:
{{ observed_answers_text }}

Now, speaking as that person, consider the held-out question:
{{ heldout_question_text }}

Response options:
{{ heldout_options_text }}

Give the probability distribution over the options that best fits this persona.

{{ output_contract }}
