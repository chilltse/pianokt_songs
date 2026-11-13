"""Mutopia public catalogue discovery and evidence gathering.

Only official Mutopia per-work details are used for licensing evidence. This
module does not infer rights from a GitHub repo license or from the music's age.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse, urlencode, parse_qs

import requests
from bs4 import BeautifulSoup

HOST = 'https://www.mutopiaproject.org'
LIST_URL = HOST + '/cgibin/make-table.cgi'
DETAIL_URL = HOST + '/cgibin/piece-info.cgi?id={id}'
MAX_HTML = 1_000_000
MAX_MIDI = 3_000_000
MAX_LY = 1_000_000

class FetchError(RuntimeError):
    pass

class Fetcher:
    def __init__(self, delay=0.3, timeout=25, retries=2):
        self.delay=delay
        self.timeout=timeout
        self.retries=retries
        self.session=requests.Session()
        self.session.headers.update({'User-Agent':'PianoKT-curated-MIDI-research/0.2 (respectful-public-catalogue-client)'})
        self.last=0.0

    def get(self, url, max_bytes=MAX_HTML):
        parsed=urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in {'www.mutopiaproject.org','mutopiaproject.org'}:
            raise FetchError('URL not on approved official Mutopia host: '+url)
        for i in range(self.retries+1):
            try:
                d=self.delay-(time.monotonic()-self.last)
                if d>0: time.sleep(d)
                self.last=time.monotonic()
                with self.session.get(url,timeout=self.timeout,stream=True,allow_redirects=True) as r:
                    r.raise_for_status()
                    if urlparse(r.url).hostname not in {'www.mutopiaproject.org','mutopiaproject.org'}:
                        raise FetchError('Untrusted download redirect')
                    buf=bytearray()
                    for chunk in r.iter_content(32*1024):
                        buf.extend(chunk)
                        if len(buf)>max_bytes: raise FetchError('File too large: '+url)
                    return bytes(buf)
            except (requests.RequestException, FetchError) as e:
                if i>=self.retries: raise FetchError(str(e)) from e
                time.sleep(min(2*(i+1),5))
        raise AssertionError('unreachable')


def parse_listing(html):
    soup=BeautifulSoup(html,'html.parser')
    ids=[]
    for a in soup.find_all('a',href=True):
        href=a['href']
        if 'piece-info.cgi' not in href: continue
        vals=parse_qs(urlparse(href).query).get('id',[])
        if vals and vals[0].isdigit() and int(vals[0]) not in ids: ids.append(int(vals[0]))
    # The Mutopia table has a specific Next 10 link, which includes startat.
    next_offset=None
    for a in soup.find_all('a',href=True):
        if re.search(r'Next\s+10',a.get_text(' ',strip=True),re.I):
            qs=parse_qs(urlparse(a['href']).query)
            val=qs.get('startat',[None])[0]
            if val is not None and str(val).isdigit(): next_offset=int(val)
            break
    return ids,next_offset


def listing_url(startat=0):
    return LIST_URL+'?'+urlencode({'Instrument':'Piano','startat':startat})


def parse_license(label, href):
    name=' '.join(label.lower().split())
    if name=='public domain':return 'Public Domain'
    if 'creative commons' not in name:return ''
    ver=re.search(r'(\d\.\d)',name)
    if not ver:return ''
    if 'noncommercial' in name or 'non-commercial' in name:return ''
    if 'attribution-sharealike' in name or 'attribution sharealike' in name:
        if 'germany' in name or '/de/' in href.lower():return 'CC-BY-SA-'+ver.group(1)+'-DE'
        return 'CC-BY-SA-'+ver.group(1)
    if 'attribution' in name:return 'CC-BY-'+ver.group(1)
    if 'cc0' in name:return 'CC0-1.0'
    return ''


def parse_detail(html,url):
    soup=BeautifulSoup(html,'html.parser')
    heading=soup.find('h2')
    if not heading:raise ValueError('Missing piece title h2')
    title=heading.get_text(' ',strip=True)
    composer_tag=soup.find('h4')
    composer=re.sub(r'^by\s+','',composer_tag.get_text(' ',strip=True),flags=re.I) if composer_tag else ''
    composer=re.sub(r'\s*\(\d{3,4}\s*[–-]\s*\d{2,4}\)\s*$','',composer).strip()
    # Mutopia's detail pages are not a stable plain-text table.  Some pages
    # split label/value/separator across different HTML elements, which adds
    # unexpected whitespace when BeautifulSoup flattens the DOM.  Do NOT
    # require instrument + style + a following field to share one regex match.
    text=re.sub(r'\s+', ' ', soup.get_text(' ', strip=True).replace('\xa0',' '))
    # Match each label independently; delimit at the next recognised label or
    # the pipe used by the standard Mutopia template.
    labels={
        'instrument':r'Instrument\s*\(\s*s\s*\)\s*:',
        'style':r'\bStyle\s*:',
        'opus':r'\bOpus\s*:',
        'meter':r'\bMeter\s*:',
        'date':r'\bDate\s+of\s+composition\s*:',
        'source':r'\bSource\s*:',
        'copyright':r'\bCopyright\s*:',
        'last_updated':r'\bLast\s+updated\s*:',
        'music_id':r'\bMusic\s+ID\s+Number\s*:',
        'typeset':r'\bTypeset\s+using\s*:',
        'download':r'\bDownload\s*:',
        'maintainer':r'\bMaintainer\s*:',
        'disclaimer':r'\bDisclaimer\s*:',
    }
    positions=[]
    for k,pattern in labels.items():
        for m in re.finditer(pattern,text,re.I):
            positions.append((m.start(),m.end(),k))
    positions.sort()
    values={}
    for idx,(st,en,key) in enumerate(positions):
        nxt=positions[idx+1][0] if idx+1<len(positions) else len(text)
        part=text[en:nxt].strip().strip(' |:,;').strip()
        # Pipe is a visual divider in Mutopia, not part of instrument/style.
        values.setdefault(key,part.split('|',1)[0].strip())
    instrument=values.get('instrument','')
    style=values.get('style','')
    if not instrument or not style:
        raise ValueError('Cannot read instrument/style on Mutopia page')
    links=soup.find_all('a',href=True)
    def link_named(term):
        for a in links:
            if a.get_text(' ',strip=True).lower()==term.lower():return urljoin(url,a['href'])
        return ''
    midi_url=link_named('MIDI file')
    ly_url=link_named('LilyPond file')
    if not midi_url or not ly_url:raise ValueError('Missing MIDI or LilyPond links')
    label=values.get('copyright','')
    license_a=None
    for a in links:
        k=a.get_text(' ',strip=True)
        if k.lower()=='public domain' or k.lower().startswith('creative commons'):
            license_a=a
            break
    if license_a:label=license_a.get_text(' ',strip=True)
    license_id=parse_license(label,license_a['href'] if license_a else '')
    license_url=urljoin(url,license_a['href']) if license_a else ''
    if not license_id:raise ValueError('Unsupported or undocumented license: '+label)
    maintainer=values.get('maintainer','')
    if not title or not composer:raise ValueError('Missing title or composer')
    return dict(title=title,composer=composer,genre=style,license=license_id,
                license_url=license_url,source_url=url,attribution=('Typeset by '+maintainer+'; Mutopia Project') if maintainer else 'Mutopia Project',
                instrument=instrument,midi_url=midi_url,ly_url=ly_url,
                source_license_label=label)


def parse_ly_header(source):
    """LilyPond header is untrusted text; extract quoted descriptive strings only."""
    def grab(key):
        m=re.search(r'(?m)^\s*'+re.escape(key)+r'\s*=\s*"([^"\n]{1,300})"',source)
        return m.group(1).strip() if m else ''
    return {k:grab(k) for k in ('title','composer','style','copyright','maintainer','mutopiatitle','mutopiainstrument')}


def score_staff_proof(source):
    """Strong conservative proof of two score staves explicitly named up/down or LH/RH.

    Does NOT assign hands by register; score staves must be identified and the
    MIDI track names must later agree with those exact staff names.
    """
    # Within the actual score only (some LilyPond files contain definitions above).
    score_pos=source.rfind('\\score')
    score=source[score_pos:] if score_pos>=0 else source
    staff=re.findall(r'\\(?:context|new)\s+Staff\s*=\s*"([^"\n]+)"',score)
    if len(staff)!=2 or staff[0]==staff[1]:return None
    def side(s):
        norm=re.sub('[^a-z]','',s.lower())
        if norm in ('up','upper','right','righthand','rh','destra','one','treble'):return 'R'
        if norm in ('down','lower','left','lefthand','lh','sinistra','two','bass'):return 'L'
        return None
    sides=[side(s) for s in staff]
    if set(sides)!={'L','R'}:return None
    return dict(zip(staff,sides))
