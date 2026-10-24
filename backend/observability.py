"""Optional external traces plus a truthful configuration status."""
import os
import importlib.util

def status():
    langsmith = os.getenv('LANGSMITH_TRACING', '').lower() == 'true' and bool(os.getenv('LANGSMITH_API_KEY'))
    agentops = bool(os.getenv('AGENTOPS_API_KEY')) and importlib.util.find_spec('agentops') is not None
    return {'local_timeline': True,
            'langsmith_configured': langsmith,
            'langsmith_project': os.getenv('LANGSMITH_PROJECT', 'waterguard') if langsmith else None,
            'agentops_configured': agentops,
            'note': 'Configured means local credentials and instrumentation are enabled; verify delivery in each service dashboard.'}

def instrument(name,fn):
    if not status()['agentops_configured']:
        return fn
    from agentops.sdk.decorators import operation
    return operation(name=name,capture_request=False,capture_response=False)(fn)

def trace_workflow(work):
    if not status()['agentops_configured']:
        return work()
    from agentops.sdk.decorators import trace
    @trace(name='WaterGuard investigation')
    def run():
        return work()
    return run()
