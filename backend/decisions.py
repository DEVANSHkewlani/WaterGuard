"""Operator-facing advice; no physical operation is triggered."""

def recommend(assessment, context):
    if assessment['ambiguous'] or not assessment['ranked']:
        return {
            'action': 'MORE EVIDENCE NEEDED',
            'text': 'The available readings do not separate the leading causes reliably.',
            'next_steps': [
                'Check the sensor values and the current pump and valve states.',
                'Obtain an independent pressure or flow reading in the affected area.',
                'Keep the leading locations on the inspection list; do not treat rank 1 as confirmed.'
            ],
            'caution': 'No remote valve operation or automatic crew dispatch is performed.'
        }
    h = assessment['ranked'][0]['candidate']
    planned = any(m['planned'] and m['location_id'] == h['location_id']
                  for m in context['maintenance'])
    if h['fault_type'] == 'pipe_leak':
        action = 'PRIORITISE LEAK INSPECTION'
        steps = [
            f'Check the area around pipe {h["location_id"]} for visible leakage and corroborating readings.',
            'Compare upstream and downstream flows and pressures with the model prediction.',
            'Ask the operator to assess field isolation only after checking the actual valve layout.'
        ]
    elif h['fault_type'] == 'valve_closure':
        action = 'VERIFY PLANNED VALVE WORK' if planned else 'CHECK RESTRICTION OR VALVE STATE'
        steps = [
            f'Confirm the actual valve status and any maintenance near pipe {h["location_id"]}.',
            'Compare flow before and after the suspected restriction.',
            'If unplanned, escalate the discrepancy to the operator for field verification.'
        ]
    else:
        action = 'VERIFY DEMAND CHANGE'
        steps = [
            f'Check for a known demand event near junction {h["location_id"]}.',
            'Compare local consumption and inlet flow with the same time period on a normal day.',
            'Collect another reading if a nearby leak produces a similar signature.'
        ]
    return {
        'action': action,
        'text': 'This is the best fitting tested scenario, subject to field verification.',
        'next_steps': steps,
        'caution': 'Simulation is decision support. No physical action is executed.'
    }
