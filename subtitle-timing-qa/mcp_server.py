"""Minimal local stdio MCP adapter for the original subtitle timing reviewer."""
import json
import math
import sys
from subtitle_qa import review

VERSIONS = {'2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'}
TOOL = {'name': 'review_subtitle_timing', 'description': 'Review supplied SRT/basic WebVTT text locally. Returns numbered heuristic findings and metrics, omitting transcript text. Does not edit, upload, access files, or certify quality.', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'maxLength': 100000}, 'max_cps': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 100}, 'max_line': {'type': 'integer', 'minimum': 1, 'maximum': 500}, 'min_duration': {'type': 'number', 'minimum': 0, 'maximum': 10}, 'max_duration': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 120}}, 'required': ['text'], 'additionalProperties': False}, 'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}}

def error(mid, code, message):
    return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': message}}

def call_tool(params):
    if params.get('name') != TOOL['name']:
        raise ValueError('Unknown tool')
    args = params.get('arguments', {})
    if not isinstance(args, dict) or set(args) - set(TOOL['inputSchema']['properties']):
        raise ValueError('Unsupported arguments')
    text = args.get('text')
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError('text must be a string of at most 100000 characters')
    defaults = {'max_cps': 20, 'max_line': 42, 'min_duration': 0.7, 'max_duration': 7}
    bounds = {'max_cps': (0, 100), 'max_line': (1, 500), 'min_duration': (0, 10), 'max_duration': (0, 120)}
    settings = {k: args.get(k, v) for k, v in defaults.items()}
    for name, value in settings.items():
        low, high = bounds[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Invalid threshold: ' + name)
    if settings['max_cps'] <= 0 or settings['max_duration'] <= settings['min_duration'] or not isinstance(settings['max_line'], int):
        raise ValueError('Invalid review threshold combination')
    report = review(text, **settings)
    return {'content': [{'type': 'text', 'text': json.dumps(report, ensure_ascii=False)}], 'isError': False}

def main():
    initialized = False
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    while True:
        raw = sys.stdin.buffer.readline(2 * 1024 * 1024 + 1)
        if not raw:
            break
        if len(raw) > 2 * 1024 * 1024:
            # Stop on oversized framing without echoing untrusted content.
            print(json.dumps(error(None, -32700, 'Message too large')), flush=True)
            break
        try:
            msg = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            print(json.dumps(error(None, -32700, 'Invalid UTF-8 JSON')), flush=True)
            continue
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
            response = error(None, -32600, 'Invalid request')
        elif 'id' not in msg:
            continue
        else:
            mid, method, params = msg['id'], msg.get('method'), msg.get('params', {})
            if not isinstance(params, dict):
                response = error(mid, -32602, 'Invalid parameters')
            elif method == 'initialize':
                requested = params.get('protocolVersion')
                version = requested if isinstance(requested, str) and requested in VERSIONS else '2025-11-25'
                initialized = True
                response = {'jsonrpc': '2.0', 'id': mid, 'result': {'protocolVersion': version, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'hhr-nexus-subtitle-timing', 'version': '0.1.0'}, 'instructions': 'Local heuristic subtitle timing review. Thresholds are preferences, not certification.'}}
            elif method == 'ping':
                response = {'jsonrpc': '2.0', 'id': mid, 'result': {}}
            elif not initialized:
                response = error(mid, -32000, 'Initialize before using tools')
            elif method == 'tools/list':
                response = {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': [TOOL]}}
            elif method == 'tools/call':
                try:
                    result = call_tool(params)
                except ValueError as exc:
                    result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
                response = {'jsonrpc': '2.0', 'id': mid, 'result': result}
            else:
                response = error(mid, -32601, 'Method not supported')
        print(json.dumps(response, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
