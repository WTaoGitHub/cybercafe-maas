#!/usr/bin/env python3
"""Estimate DeepSeek-OCR token usage on ten seeded random local test images."""
import json
from pathlib import Path
import random
import statistics

import benchmark_service as service


def main():
    dataset = service.ROOT.parent / 'datasets/ocr-mixed-v1'
    rows = [json.loads(line) for line in (dataset / 'manifest.jsonl').read_text().splitlines()]
    sample = random.Random(20261030).sample(rows, 10)
    output = service.ROOT / 'local-results/random10-tokens-20261030'
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, item in enumerate(sample, 1):
        result = service.call(dataset / item['image'], max_tokens=4096)
        result.update(source=item['source'], image_id=item['id'], bytes=item['bytes'],
                      width=item['width'], height=item['height'])
        records.append(result)
        (output / 'records.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(json.dumps({'index': index, 'source': item['source'], 'status': result['status'],
                          'usage': result.get('usage'), 'finish': result.get('finish_reason'),
                          'elapsed_s': round(result['elapsed_s'], 2)}, ensure_ascii=False), flush=True)
        if result['status'] != 200:
            print('Stopped on error; no retries.', flush=True)
            break
    good = [r for r in records if r['status'] == 200 and r.get('usage')]
    summary = {'model': service.MODEL, 'seed': 20261030, 'selected': len(sample),
               'attempted': len(records), 'successful_with_usage': len(good),
               'truncated': sum(r.get('finish_reason') == 'length' for r in good)}
    if good:
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            values = [r['usage'][field] for r in good]
            summary[field] = {'mean': statistics.mean(values), 'median': statistics.median(values),
                              'min': min(values), 'max': max(values)}
        summary['mean_bytes'] = statistics.mean(r['bytes'] for r in good)
        summary['mean_pixels'] = statistics.mean(r['width'] * r['height'] for r in good)
        summary['sources'] = {source: sum(r['source'] == source for r in good)
                              for source in sorted({r['source'] for r in good})}
        summary['by_source'] = {source: {field: statistics.mean(r['usage'][field] for r in good if r['source'] == source)
                                        for field in ('prompt_tokens', 'completion_tokens')}
                                for source in summary['sources']}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
