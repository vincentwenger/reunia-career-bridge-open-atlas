#!/usr/bin/env python3
"""Keep retired meeting-era selectors out of shared Career Bridge styles."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = [
    ROOT / 'products/reunia/static/css/base.css',
    ROOT / 'products/reunia/static/css/navbar.css',
    ROOT / 'products/reunia/static/css/components.css',
    ROOT / 'products/reunia/static/css/app-layout.css',
    ROOT / 'products/reunia/static/css/career-theme.css',
]
RETIRED = {
    '.meeting-preparation-page',
    '.meeting-recorder-page',
    '.meeting-review-page',
    '.context-save-button',
    '.home-text-link',
    '.career-accent',
}

failures=[]
for path in SHARED:
    text=path.read_text(encoding='utf-8')
    for selector in sorted(RETIRED):
        if selector in text:
            failures.append(f'{path.relative_to(ROOT)} contains {selector}')
if failures:
    print('Retired selectors remain in shared CSS:')
    print('\n'.join(f'- {item}' for item in failures))
    raise SystemExit(1)
print('Retired shared CSS selectors are absent.')
