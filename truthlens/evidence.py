"""Bounded visual evidence, claim candidates and provenance verification."""
import base64
import io
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def thumbnail(image, size=560):
    image = image.copy().convert('RGB')
    image.thumbnail((size, size))
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=76)
    return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')


def visual_evidence(image):
    small = image.copy()
    small.thumbnail((560, 560))
    output = io.BytesIO()
    small.save(output, format='JPEG', quality=90)
    with Image.open(io.BytesIO(output.getvalue())) as recompressed:
        diff = np.asarray(ImageChops.difference(small, recompressed.convert('RGB')), dtype=np.float32).mean(axis=2)
    magnitude = np.clip(diff * 12, 0, 255).astype(np.uint8)
    heatmap = np.stack((magnitude, np.minimum(magnitude.astype(int) * 2, 255).astype(np.uint8), 255 - magnitude), axis=2)
    overlay = Image.blend(small, Image.fromarray(heatmap), .65)
    return {'original': thumbnail(small), 'residual_overlay': thumbnail(overlay),
            'description': 'JPEG quality-90 residual amplified 12× and blended at 65%. Yellow/red means a larger residual, not proven manipulation.'}


def detect_faces(image):
    import cv2
    array = np.asarray(image.convert('RGB'))
    height, width = array.shape[:2]
    scale = min(1., 640 / max(width, height))
    small = cv2.resize(array, (max(1, round(width * scale)), max(1, round(height * scale))))
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    boxes = detector.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
    return [{'x': int(x / scale), 'y': int(y / scale), 'width': int(w / scale), 'height': int(h / scale)}
            for x, y, w, h in sorted(boxes, key=lambda box: int(box[2]) * int(box[3]), reverse=True)[:3]]


def face_crop(image, box):
    x, y, w, h = [box[k] for k in ('x', 'y', 'width', 'height')]
    margin = int(max(w, h) * .2)
    return image.crop((max(0, x - margin), max(0, y - margin), min(image.width, x + w + margin), min(image.height, y + h + margin)))


def annotate_faces(image, boxes):
    result = image.copy()
    draw = ImageDraw.Draw(result)
    for box in boxes:
        x, y, w, h = [box[k] for k in ('x', 'y', 'width', 'height')]
        draw.rectangle((x, y, x + w, y + h), outline='#63e3c1', width=max(2, image.width // 200))
        draw.text((x, max(0, y - 14)), str(box.get('track_id', 'face')), fill='#63e3c1')
    return thumbnail(result, 320)


def box_iou(a, b):
    x1, y1 = max(a['x'], b['x']), max(a['y'], b['y'])
    x2 = min(a['x'] + a['width'], b['x'] + b['width'])
    y2 = min(a['y'] + a['height'], b['y'] + b['height'])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = a['width'] * a['height'] + b['width'] * b['height'] - intersection
    return intersection / union if union else 0


def associate_faces(boxes, previous, next_id):
    used = set()
    for box in boxes:
        candidates = [(box_iou(box, old), old) for old in previous if old['track_id'] not in used]
        score, old = max(candidates, key=lambda pair: pair[0], default=(0, None))
        if old and score >= .25:
            box['track_id'] = old['track_id']
        else:
            box['track_id'] = next_id
            next_id += 1
        used.add(box['track_id'])
    return next_id


def compare_images(image, reference):
    preview = reference.copy()
    preview.thumbnail((560, 560))
    current = image.resize(preview.size, Image.Resampling.BILINEAR)
    diff = ImageChops.difference(current, preview)
    return {'reference': thumbnail(preview), 'aligned_input':thumbnail(current), 'difference': thumbnail(diff),
            'mean_absolute_difference': round(float(np.asarray(diff, dtype=float).mean()), 3),
            'same_dimensions': image.size == reference.size,
            'description': 'Reference resized to align image dimensions only. Cropping, viewpoint, color and compression can create differences; no geometric registration is performed.'}


def provenance(data, mime):
    try:
        from c2pa import Context, Reader
    except ImportError:
        return {'status': 'unavailable', 'detail': 'Install c2pa-python to verify embedded Content Credentials.'}
    anchors = ROOT / 'models/trust-anchors.pem'
    config = {'verify': {'remote_manifest_fetch': False, 'verify_cert_anchors': True}}
    if anchors.is_file():
        config['trust'] = {'trust_anchors': anchors.read_text(encoding='utf-8')}
    try:
        with Context.from_dict(config) as context:
            with Reader(mime, io.BytesIO(data), context=context) as reader:
                store = json.loads(reader.json())
        active = store.get('active_manifest')
        manifest = store.get('manifests', {}).get(active, {})
        results = store.get('validation_results') or {}
        failures = results.get('activeManifest', {}).get('failure', [])
        statuses = store.get('validation_status', [])
        state = store.get('validation_state', 'unknown')
        # Do not infer trust merely from the absence of reported failures.
        trust_codes={'signingCredential.untrusted','timeStamp.untrusted'}
        integrity_failures=[item for item in failures + statuses if item.get('code') not in trust_codes]
        status = 'invalid' if integrity_failures or state == 'Invalid' else 'trusted' if state == 'Trusted' else 'valid_untrusted' if state == 'Valid' else 'present_untrusted'
        return {'status': status, 'sdk_validation_state': state, 'active_manifest': active,
                'claim_generator': manifest.get('claim_generator_info', manifest.get('claim_generator')),
                'signature': manifest.get('signature_info', {}), 'validation_results': results,
                'validation_status': statuses, 'trust_anchors_loaded': anchors.is_file(),
                'detail': 'Embedded credentials checked locally. Provenance does not establish factual truth. Remote manifests are not fetched.'}
    except Exception as exc:
        message = str(exc)
        if 'ManifestNotFound' in message or 'JumbfNotFound' in message:
            return {'status': 'absent', 'detail': 'No embedded credentials found. Absence is not evidence of manipulation.'}
        return {'status': 'verification_error', 'detail': 'Credentials could not be verified; the file may be unsupported or its manifest malformed.'}


STOP = set('the a an and or to of in on for is are was were be by from with this that it as at has have had its their said according'.split())


def tokens(text):
    return set(re.findall(r'[a-z0-9]{3,}', text.lower())) - STOP


def extract_claims(text):
    candidates = []
    for match in re.finditer(r'[^.!?\n]+[.!?]?', text):
        sentence = match.group().strip()
        if len(sentence) < 35 or len(sentence) > 600:
            continue
        score = len(re.findall(r'\d|\b(?:says|said|study|research|causes|will|confirmed|claims|reported|according)\b', sentence, re.I))
        candidates.append((score, {'text': sentence, 'start': match.start() + len(match.group()) - len(match.group().lstrip()), 'end': match.end(), 'assessment': 'Insufficient evidence', 'sources': []}))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _, candidate in candidates[:5]]


def safe_source_url(value):
    parsed = urllib.parse.urlsplit(str(value))
    return str(value) if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password else ''


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'TruthLens/2.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('Response exceeds lookup limit')
    return json.loads(raw)


_FEEDS = {}


def public_factchecks():
    items, failures = [], []
    for publisher, url in [('FactCheck.org', 'https://www.factcheck.org/feed/')]:
        cached = _FEEDS.get(url)
        if cached and time.time() - cached[0] < 900:
            items.extend(cached[1]); continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'TruthLens/2.0'}), timeout=6) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('Unsupported feed')
            root = ET.fromstring(raw)
            found = [{'publisher':publisher, 'title': item.findtext('title', '')[:400], 'url':safe_source_url(item.findtext('link', '')), 'date':item.findtext('pubDate', '')[:100], 'rating':'Not extracted', 'source_type':'Recent publisher feed; candidate match only'} for item in root.findall('.//item')[:50]]
            found = [item for item in found if item['url']]
            _FEEDS[url] = (time.time(), found)
            items.extend(found)
        except Exception:
            failures.append(publisher)
    return items, failures


def lookup_claims(claims, google_key='', progress=None):
    """Retrieve candidate reviews. Never map a loosely matched review to a truth verdict."""
    errors, provider = [], 'Google Fact Check Tools' if google_key else 'Recent public fact-check feeds'
    articles, failures = public_factchecks() if not google_key else ([], [])
    errors.extend(f'{name} feed unavailable' for name in failures)
    for index, claim in enumerate(claims[:3]):
        if progress:
            progress(65 + index * 8, f'Looking up claim {index + 1}')
        if google_key:
            try:
                params = urllib.parse.urlencode({'query':claim['text'][:400], 'key':google_key, 'languageCode':'en', 'pageSize':5})
                result = fetch_json('https://factchecktools.googleapis.com/v1alpha1/claims:search?' + params)
                candidates = []
                for entry in result.get('claims', []):
                    for review in entry.get('claimReview', []):
                        link = safe_source_url(review.get('url', ''))
                        if link:
                            candidates.append({'publisher':review.get('publisher', {}).get('name', ''), 'title':review.get('title', '')[:400], 'claim_reviewed':entry.get('text', '')[:600], 'url':link, 'date':review.get('reviewDate', ''), 'rating':review.get('textualRating', ''), 'source_type':'Retrieved fact-check; verify claim equivalence'})
                claim['sources'] = candidates[:5]
            except Exception:
                errors.append('Google lookup failed. Check the API key, quota, and network connection.')
        else:
            query = tokens(claim['text'])
            ranked = [(len(query & tokens(item['title'])) / max(1, len(query)), item) for item in articles]
            ranked.sort(key=lambda pair: pair[0], reverse=True)
            claim['sources'] = [{**item, 'token_overlap':round(score, 3)} for score, item in ranked[:3] if score >= .15]
        claim['assessment'] = 'Related reviews found; claim equivalence requires review' if claim['sources'] else 'Insufficient evidence'
    return {'provider':provider, 'status':'partial' if errors else 'completed', 'errors':sorted(set(errors)), 'queried_claims':min(3,len(claims)),
            'note':'Recent feeds cover only recent titles. Retrieved ratings describe the publisher’s reviewed claim, not automatically this article. No match is not a truth verdict.'}
