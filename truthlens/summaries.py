"""Plain-language guidance derived from observations, never an authenticity verdict."""
def evidence_summary(report):
    kind=report.get('kind')
    metrics=report.get('metrics',{})
    noticed=[]
    if kind=='news':
        claims=report.get('claims',[])
        noticed.append(f'{len(claims)} claim candidates are available for human review.')
        noticed.append(f'{sum(bool(c.get("sources")) for c in claims)} candidates have related review links; claim equivalence is not established.')
        why='Claims need evidence that matches the exact statement, date, and context. Persuasive wording is not proof of misinformation.'
        next_steps=['Open the original source and check the date and context.','Compare each claim with the attached review or primary evidence.','Save a human assessment only after checking that the evidence supports the same claim.']
    elif kind=='video':
        noticed.append(f'{metrics.get("frames_sampled", "Selected")} frames were inspected.')
        why='Scene cuts, movement, focus, and compression can explain visual changes. Sampled frames can miss short edits; audio and lip-sync have not been assessed.'
        next_steps=['Watch the full original recording, including the moments before and after the excerpt.','Use the timeline to inspect changes in context.','Check the original publisher and corroborating recordings before sharing.']
    else:
        software=metrics.get('software')
        noticed.append('The file was decoded and checked for visual editing clues.')
        noticed.append('An editing-software label was found.' if software else 'No editing-software label was found; sharing can remove metadata.')
        why='Normal cropping, sharpening, texture, and recompression can produce similar clues. A face-model score is experimental and does not establish manipulation.'
        next_steps=['Locate the earliest available image and its publisher.','Compare with an original reference if you have one.','Check visual clues in context before drawing a conclusion.']
    provenance=report.get('provenance',{}).get('status')
    if provenance: noticed.append({'trusted':'Embedded signed history passed the bundled trust checks.','absent':'No embedded signed history was found.','invalid':'Embedded signed history failed verification.'}.get(provenance,'The signed-history check needs further review.'))
    prediction=report.get('prediction')
    if kind=='image' and prediction:
        noticed.append(prediction['explanation'])
        why='A whole-image classifier compared a photograph with AI-generated imagery on the original, JPEG-compressed and reduced-resolution versions. All three scores must pass fixed thresholds. This is a prediction, not verification of all edits or factual context.'
    return {'noticed':noticed,'why':why,'next_steps':next_steps,'result':prediction['label'] if prediction else 'Not verified'}
