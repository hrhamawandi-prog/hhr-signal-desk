"""Offline SRT/WebVTT timing review. No upload, edits, or certification."""
import argparse
import hashlib
import json
from pathlib import Path
import re

STAMP = re.compile(r'^(?:(\d{2,}):)?(\d{2}):(\d{2})[.,](\d{3})$')

def stamp(value):
    m = STAMP.fullmatch(value)
    if not m:
        raise ValueError('Expected HH:MM:SS,mmm or WebVTT MM:SS.mmm')
    h, minutes, seconds, millis = m.groups()
    if int(minutes) > 59 or int(seconds) > 59:
        raise ValueError('Minute/second outside 00..59')
    return int(h or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000

def review(text, max_cps=20, max_line=42, min_duration=0.7, max_duration=7):
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')
    is_vtt = text.startswith('WEBVTT')
    cues, findings = [], []
    add = lambda code, cue, detail: findings.append({'code': code, 'cue': cue, 'detail': detail})
    for block in re.split(r'\n[ \t]*\n', text.strip()):
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0].strip()
        if is_vtt and (first.startswith('WEBVTT') or first == 'NOTE' or first.startswith('NOTE ') or first in {'STYLE', 'REGION'}):
            continue
        timing_index = next((i for i, line in enumerate(lines[:2]) if '-->' in line), None)
        cue = len(cues) + 1
        if timing_index is None:
            add('UNPARSED_BLOCK', cue, 'Block has no timing line in its first two lines.')
            continue
        try:
            left, right = lines[timing_index].split('-->')
            start, end = stamp(left.strip()), stamp(right.strip().split()[0])
        except (ValueError, IndexError):
            add('INVALID_TIMING', cue, 'Timing syntax or timestamp range is invalid.')
            cues.append({'cue': cue, 'start': None, 'end': None})
            continue
        content = lines[timing_index + 1:]
        # Approximate visible text; voice/formatting markup is excluded.
        visible = [re.sub(r'<[^>]*>', '', line) for line in content]
        chars = sum(len(line) for line in visible)
        duration = end - start
        entry = {'cue': cue, 'start': start, 'end': end, 'duration': round(duration, 3), 'characters': chars, 'lines': len(visible)}
        cues.append(entry)
        if not ''.join(visible).strip():
            add('EMPTY_TEXT', cue, 'Cue has no visible text.')
        if duration <= 0:
            add('NON_POSITIVE_DURATION', cue, 'End must be later than start.')
        else:
            entry['characters_per_second'] = round(chars / duration, 2)
            if chars / duration > max_cps:
                add('FAST_READING', cue, f'Approximate reading rate exceeds configured {max_cps:g} characters/second.')
            if duration < min_duration:
                add('SHORT_DURATION', cue, f'Duration below configured {min_duration:g} seconds.')
            if duration > max_duration:
                add('LONG_DURATION', cue, f'Duration above configured {max_duration:g} seconds.')
        if len(visible) > 2:
            add('MANY_LINES', cue, 'More than two text lines; review for the target display.')
        if any(len(line) > max_line for line in visible):
            add('LONG_LINE', cue, f'A line exceeds configured {max_line} characters.')
    if not cues:
        add('NO_CUES', None, 'No timed cues could be parsed; this is not a clean result.')
    valid = [c for c in cues if c.get('start') is not None and c['end'] > c['start']]
    for previous, current in zip(valid, valid[1:]):
        if current['start'] < previous['start']:
            add('OUT_OF_ORDER', current['cue'], 'Start precedes the previous valid cue start.')
    active = []
    for cue in sorted(valid, key=lambda c: (c['start'], c['cue'])):
        active = [other for other in active if other['end'] > cue['start']]
        if active:
            add('OVERLAP', cue['cue'], 'Review overlap with ' + str(len(active)) + ' cue(s): ' + ', '.join(str(other['cue']) for other in active[:10]) + (' (first 10 shown)' if len(active) > 10 else '') + '. Overlap can be intentional.')
        active.append(cue)
    return {'product': 'HHR Nexus Subtitle Timing QA', 'version': '0.1.0', 'input_sha256': hashlib.sha256(text.encode()).hexdigest(), 'format': 'WebVTT' if is_vtt else 'SRT-like', 'cues_parsed': len(cues), 'settings': {'max_cps': max_cps, 'max_line': max_line, 'min_duration': min_duration, 'max_duration': max_duration}, 'findings_count': len(findings), 'findings': findings, 'cue_metrics': cues, 'limitations': 'Heuristic timing review only. No audio/video synchronization, translation assessment, complete format validation, accessibility certification, or guarantee. Configured thresholds are preferences, not universal standards. Transcript text is omitted from the report.'}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-cps', type=float, default=20)
    p.add_argument('--max-line', type=int, default=42)
    p.add_argument('--min-duration', type=float, default=0.7)
    p.add_argument('--max-duration', type=float, default=7)
    a = p.parse_args()
    if a.max_cps <= 0 or a.max_line <= 0 or a.min_duration < 0 or a.max_duration <= a.min_duration:
        p.error('Invalid review thresholds')
    if a.input.stat().st_size > 2 * 1024 * 1024:
        p.error('Limit: 2 MB UTF-8 subtitle file')
    text = a.input.read_text(encoding='utf-8-sig')
    result = review(text, a.max_cps, a.max_line, a.min_duration, a.max_duration)
    a.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'cues_parsed': result['cues_parsed'], 'findings_count': result['findings_count']}))

if __name__ == '__main__':
    main()
