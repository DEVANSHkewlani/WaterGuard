"""Optional LLM paraphrase; numeric evidence always comes from WNTR."""
import os
import re

def narrative(evidence: str, candidate: dict) -> str:
    if os.getenv('WATERGUARD_USE_LLM') != '1' or not os.getenv('OPENAI_API_KEY'):
        return evidence
    try:
        from openai import OpenAI
        response = OpenAI().responses.create(
            model=os.getenv('WATERGUARD_LLM_MODEL', 'gpt-4o-mini'),
            input=[{'role':'system','content':
                    'Paraphrase the supplied hydraulic evidence in one short sentence. '
                    'Do not add numbers, new facts, physical actions or certainty.'},
                   {'role':'user','content':f'Candidate {candidate}. Evidence: {evidence}'}],
            max_output_tokens=90)
        phrase = response.output_text.strip()
        if not phrase or re.search(r'\d', phrase):
            return evidence
        return evidence + ' Narrative (unverified): ' + phrase
    except Exception:
        return evidence
