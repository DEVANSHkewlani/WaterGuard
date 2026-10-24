"""Enter optional, explicitly sourced Net1 operational context for a demo.

These records influence recommendation wording, not the physical fault score.
"""
import argparse
import json
import sqlite3
from backend.context import DB, init_db, fetch_context
from backend.hydraulics import network


def main(argv=None, db=DB):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    maintenance = sub.add_parser('add-maintenance', help='Record actual or labeled demo pipe work')
    maintenance.add_argument('--pipe', required=True)
    maintenance.add_argument('--planned', action='store_true')
    maintenance.add_argument('--note', required=True)
    demand = sub.add_parser('add-demand', help='Record a known demand schedule')
    demand.add_argument('--junction', required=True)
    demand.add_argument('--multiplier', required=True, type=float)
    demand.add_argument('--note', required=True)
    weather = sub.add_parser('add-weather', help='Record weather context')
    weather.add_argument('--condition', required=True)
    weather.add_argument('--note', required=True)
    sub.add_parser('show', help='Show Net1 context records')
    args = parser.parse_args(argv)
    if args.command == 'show':
        print(json.dumps(fetch_context('Net1', db), indent=2))
        return
    model = network('Net1')
    if args.command == 'add-maintenance' and args.pipe not in model.pipe_name_list:
        parser.error('Pipe ID does not exist in Net1')
    if args.command == 'add-demand':
        if args.junction not in model.junction_name_list or args.multiplier <= 0:
            parser.error('Enter a Net1 junction and a positive demand multiplier')
    init_db(db)
    with sqlite3.connect(db) as conn:
        if args.command == 'add-maintenance':
            conn.execute('INSERT INTO maintenance VALUES (?,?,?,?)',
                         ('Net1', args.pipe, int(args.planned), args.note))
        elif args.command == 'add-demand':
            conn.execute('INSERT INTO demand_schedule VALUES (?,?,?,?)',
                         ('Net1', args.junction, args.multiplier, args.note))
        else:
            conn.execute('INSERT INTO weather VALUES (?,?,?)',
                         ('Net1', args.condition, args.note))
    print('Context recorded. It informs the report; it is not a simulated sensor measurement.')


if __name__ == '__main__':
    main()
