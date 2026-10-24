"""Sensor mismatch and simulator-grounded explanation."""
def score(observed, predicted, pressure_sigma=0.8, flow_sigma=0.001):
    """Mean squared standardised residual; flow inputs use m³/s."""
    terms = []
    for kind, sigma in (('pressures', pressure_sigma), ('flows', flow_sigma)):
        for key, value in observed.get(kind, {}).items():
            if key not in predicted[kind]:
                raise ValueError(f'Unknown sensor {key}')
            terms.append(((value - predicted[kind][key]) / sigma) ** 2)
    if not terms:
        raise ValueError('At least one reading is required')
    return sum(terms) / len(terms)

def explain(row, observed, baseline):
    pred = row['predicted']
    items = []
    for key, value in observed.get('pressures', {}).items():
        items.append((abs(value - baseline['pressures'][key]),
                      f'pressure at {key}: baseline {baseline["pressures"][key]:.2f} m, '
                      f'observed {value:.2f} m, simulated {pred["pressures"][key]:.2f} m'))
    for key, value in observed.get('flows', {}).items():
        items.append((abs(value - baseline['flows'][key]) / 0.001,
                      f'flow at {key}: baseline {baseline["flows"][key]:.4f} m³/s, '
                      f'observed {value:.4f} m³/s, simulated {pred["flows"][key]:.4f} m³/s'))
    return 'Best measured deviation: ' + max(items, default=(0, 'none'))[1] + '.'
