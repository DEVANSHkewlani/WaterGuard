"""Parallel, local context reads. Empty tables are valid evidence."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
DB = Path(__file__).with_name('context.sqlite3')

def init_db(path=DB):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS maintenance '
                   '(network TEXT, location_id TEXT, planned INTEGER, note TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS demand_schedule '
                   '(network TEXT, junction_id TEXT, multiplier REAL, note TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS weather '
                   '(network TEXT, condition TEXT, note TEXT)')


def context_rows(table, name, path=DB):
    if table not in ('maintenance', 'demand_schedule', 'weather'):
        raise ValueError('Unknown context table')
    init_db(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE network=?', (name,))]


def fetch_context(name, path=DB):
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(context_rows, k, name, path)
                   for k in ('maintenance', 'demand_schedule', 'weather')}
        return {k: v.result() for k, v in futures.items()}
