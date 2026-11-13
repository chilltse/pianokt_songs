#!/usr/bin/env python3
"""Real online smoke test. Does not download MIDI or create accepted results."""
from pathlib import Path
from catalog_source import Fetcher, DETAIL_URL, parse_detail, FetchError


def main():
    url=DETAIL_URL.format(id=203)
    page=''
    try:
        page=Fetcher(delay=.2).get(url).decode('utf-8',errors='replace')
        info=parse_detail(page,url)
    except (ValueError,FetchError) as e:
        if page:
            Path('mutopia_debug_203.html').write_text(page,encoding='utf-8')
            raise SystemExit(f'[FAIL] {e}. Raw HTML saved to mutopia_debug_203.html')
        raise SystemExit(f'[FAIL] Could not fetch actual Mutopia page: {e}')
    print('[OK] Actual Mutopia piece page parsed successfully:')
    for k in ('title','composer','instrument','genre','license','license_url','midi_url','ly_url'):
        print(f'  {k}: {info[k]}')


if __name__ == '__main__':
    main()
