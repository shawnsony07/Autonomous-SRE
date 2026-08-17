from prometheus_client import Counter, Histogram

INCIDENT_COUNTER = Counter(
    'sre_incident_total',
    'Total number of SRE incidents processed',
    ['alert_type', 'resolution_status']
)

LLM_LATENCY_HISTOGRAM = Histogram(
    'sre_llm_latency_seconds',
    'Latency of the LLM root cause analysis and planning',
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]
)
