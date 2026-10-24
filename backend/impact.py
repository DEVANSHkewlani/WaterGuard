"""Quantitative hydraulic impact and repair benefit analysis.

Calculates water loss, resource disruption, and post-fix recovery percentages
by comparing faulted conditions with healthy baseline physics in WNTR.
"""
from __future__ import annotations
import math
import wntr
from .models import Candidate
from .hydraulics import network, apply_fault, simulate


def analyze_impact(network_name: str, candidate: Candidate | dict) -> dict:
    if isinstance(candidate, dict):
        candidate = Candidate(**candidate)

    # 1. Simulate healthy baseline
    wn_base = network(network_name)
    sim_base = wntr.sim.WNTRSimulator(wn_base).run_sim()
    p_base = sim_base.node['pressure'].iloc[0]
    q_base = sim_base.link['flowrate'].iloc[0]
    total_base_inflow = sum(abs(float(q_base[p])) for p in wn_base.pipe_name_list) / 2.0
    total_base_inflow_lps = max(0.1, total_base_inflow * 1000.0)

    # 2. Simulate faulted network
    wn_fault = network(network_name)
    apply_fault(wn_fault, candidate)
    sim_fault = wntr.sim.WNTRSimulator(wn_fault).run_sim()
    p_fault = sim_fault.node['pressure'].iloc[0]
    q_fault = sim_fault.link['flowrate'].iloc[0]

    # Pressure impact across consumption junctions
    junctions = list(wn_base.junction_name_list)
    p_drops = {j: float(p_base[j]) - float(p_fault[j]) for j in junctions}
    affected_junctions = [j for j, drop in p_drops.items() if drop > 0.3 or float(p_fault[j]) < 15.0]
    affected_nodes_pct = round((len(affected_junctions) / max(1, len(junctions))) * 100.0, 1)
    avg_pressure_drop = round(sum(max(0.0, d) for d in p_drops.values()) / max(1, len(junctions)), 2)
    max_pressure_drop = round(max((max(0.0, d) for d in p_drops.values()), default=0.0), 2)

    # 3. Water loss and resource impact
    if candidate.fault_type == 'pipe_leak':
        # Direct leak discharge from leak_demand
        if 'leak_demand' in sim_fault.node and '_leak_node' in sim_fault.node['leak_demand']:
            leak_flow_m3s = float(sim_fault.node['leak_demand']['_leak_node'].iloc[0])
        else:
            leak_flow_m3s = max(0.0, (sum(abs(float(q_fault[p])) for p in wn_fault.pipe_name_list) / 2.0) - total_base_inflow)
        
        leak_lps = round(leak_flow_m3s * 1000.0, 2)
        daily_loss_m3 = round(leak_flow_m3s * 86400.0, 1)
        water_loss_pct = round(min(100.0, (leak_lps / total_base_inflow_lps) * 100.0), 1)
        resource_disruption_pct = round(min(100.0, water_loss_pct * 1.2 + affected_nodes_pct * 0.3), 1)
        problem_description = f"Physical pipe rupture discharging ~{leak_lps} L/s ({daily_loss_m3} m³/day) of potable water."

    elif candidate.fault_type == 'valve_closure':
        leak_lps = 0.0
        daily_loss_m3 = 0.0
        water_loss_pct = 0.0
        # Flow reduction through restricted pipe
        orig_pipe_q = abs(float(q_base.get(candidate.location_id, 0.001)))
        new_pipe_q = abs(float(q_fault.get(candidate.location_id, 0.0)))
        pipe_throttled_pct = round(max(0.0, (orig_pipe_q - new_pipe_q) / max(0.0001, orig_pipe_q)) * 100.0, 1)
        resource_disruption_pct = round(min(100.0, pipe_throttled_pct * 0.6 + affected_nodes_pct * 0.4), 1)
        problem_description = f"Hydraulic restriction choking flow through pipe {candidate.location_id} by {pipe_throttled_pct}%."

    else:  # demand_surge
        # Extra unallocated consumption
        surge_flow_m3s = float(sim_fault.node['demand'][candidate.location_id].iloc[0]) - float(sim_base.node['demand'][candidate.location_id].iloc[0])
        leak_lps = round(max(0.0, surge_flow_m3s * 1000.0), 2)
        daily_loss_m3 = round(leak_lps * 86.4, 1)
        water_loss_pct = round(min(100.0, (leak_lps / total_base_inflow_lps) * 100.0), 1)
        resource_disruption_pct = round(min(100.0, water_loss_pct * 1.1 + affected_nodes_pct * 0.3), 1)
        problem_description = f"Abnormal unmetered consumption surge of ~{leak_lps} L/s at junction {candidate.location_id}."

    # 4. Repair and recovery percentage benefits
    recovery_benefit_pct = 100.0
    pressure_recovery_gain_m = max_pressure_drop
    efficiency_gain_pct = round(max(5.0, resource_disruption_pct * 0.95), 1)

    return {
        'network_name': network_name,
        'candidate': {
            'fault_type': candidate.fault_type,
            'location_id': candidate.location_id,
            'severity': candidate.severity
        },
        'water_loss': {
            'loss_percentage': water_loss_pct,
            'flow_rate_lps': leak_lps,
            'daily_volume_m3': daily_loss_m3,
        },
        'resource_impact': {
            'disruption_percentage': resource_disruption_pct,
            'affected_junctions_count': len(affected_junctions),
            'affected_junctions_pct': affected_nodes_pct,
            'avg_pressure_drop_m': avg_pressure_drop,
            'max_pressure_drop_m': max_pressure_drop,
            'problem_summary': problem_description
        },
        'repair_benefits': {
            'water_recovery_pct': 100.0 if water_loss_pct > 0 else 0.0,
            'water_saved_daily_m3': daily_loss_m3,
            'pressure_restoration_pct': 100.0,
            'pressure_head_gain_m': pressure_recovery_gain_m,
            'hydraulic_efficiency_gain_pct': efficiency_gain_pct,
            'benefit_summary': (
                f"Isolating/repairing this fault eliminates {daily_loss_m3} m³/day of water loss "
                f"and restores {efficiency_gain_pct}% network delivery efficiency."
                if daily_loss_m3 > 0 else
                f"Clearing this restriction restores normal line pressure by up to +{pressure_recovery_gain_m} m "
                f"and recovers {efficiency_gain_pct}% distribution capacity."
            )
        }
    }


if __name__ == '__main__':
    import sys
    net = sys.argv[1] if len(sys.argv) > 1 else 'Net1'
    f_type = sys.argv[2] if len(sys.argv) > 2 else 'pipe_leak'
    f_loc = sys.argv[3] if len(sys.argv) > 3 else '11'
    f_sev = float(sys.argv[4]) if len(sys.argv) > 4 else 0.55
    cand = Candidate(f_type, f_loc, f_sev)
    res = analyze_impact(net, cand)
    print(f"=== Quantitative Impact Analysis: {net} [{f_type} at {f_loc}, strength {f_sev}] ===")
    print(f"Water Loss: {res['water_loss']['loss_percentage']}% ({res['water_loss']['flow_rate_lps']} L/s, {res['water_loss']['daily_volume_m3']} m³/day)")
    print(f"Resource Disruption: {res['resource_impact']['disruption_percentage']}% ({res['resource_impact']['affected_junctions_pct']}% of junctions affected)")
    print(f"Projected Repair Benefit: +{res['repair_benefits']['hydraulic_efficiency_gain_pct']}% efficiency recovery; {res['repair_benefits']['benefit_summary']}")
