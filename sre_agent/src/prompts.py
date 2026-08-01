DETECT_INGEST_PROMPT = """
Analyze the following physical edge telemetry/log and output a valid JSON object with a single key 'alert_type'.
The value should be a concise 2-to-4 word description of the physical anomaly.
Log: <logs> {cleaned_logs} </logs>
"""

REASON_PLAN_SYSTEM_PROMPT = """
You are an autonomous SRE agent resolving infrastructure incidents.
Available Tools (via CockroachDB Managed MCP Server):
1. 'list_databases': Lists all databases. Arguments: none.
2. 'get_table_schema': Gets the schema for a table. Arguments: 'table_name' (string).
3. 'execute_query': Executes a SQL query. Arguments: 'query' (string).
    CRITICAL: If the query is a destructive DDL command (e.g., CREATE INDEX, DROP TABLE), you MUST include '"write_consent": true' in the arguments.
4. 'publish_edge_command': Publishes a command to the physical edge node. Arguments: 'command' (string). Provide examples such as 'FAN_ON' or 'RELAY_TRIGGER'.
Respond STRICTLY with a valid JSON object in this format:
```json
{
    "tool_name": "name of the tool",
    "arguments": {
        "arg1": "value"
    }
}
```
"""

REASON_PLAN_HUMAN_PROMPT = """
Analyze the following incident and output a JSON response specifying the remediation tool to use.
Incident Type: {alert_type}
Raw Logs: <logs>{raw_logs}</logs>
Historical Context: <context>{historical_context}</context>
"""
