"""Explain recorded measurements without turning observations into truth verdicts."""

GUIDANCE = {
    'File structure': ('Decoding confirms that the file can be inspected.', ['A readable file can still contain ordinary or deceptive edits.'], 'Compare dimensions and context with the earliest source.', 'File dimensions and format'),
    'Editing metadata': ('Software labels can help trace a file workflow.', ['Cropping, colour correction, and export tools can leave software labels.', 'Platforms often remove metadata.'], 'Ask for the original file and inspect its history.', 'Software metadata'),
    'Recompression residual': ('This measures differences after a JPEG quality-90 recompression.', ['Texture and edges', 'Earlier JPEG compression', 'Format conversion or ordinary editing'], 'Compare the overlay with an original reference; bright pixels are not confirmed edits.', 'Mean pixel difference; 0–255 channel scale'),
    'Fine-detail variation': ('Laplacian variance describes image detail, not authenticity.', ['Focus and blur', 'Texture, sharpening, and compression'], 'Inspect the original at its native resolution.', 'Laplacian variance; no authenticity threshold'),
    'Video structure': ('Distributed samples provide partial visual coverage.', ['Short edits can occur between inspected frames.'], 'Watch the complete recording and inspect the gaps between samples.', 'Sample count and duration'),
    'Visual changes': ('This describes grayscale change between separated samples.', ['Scene cuts', 'Camera or subject movement', 'Lighting changes'], 'Inspect the surrounding timeline before attributing a change to editing.', 'Mean grayscale difference; 0–255 channel scale'),
    'Detail variation': ('Changes in detail describe visual consistency across samples.', ['Focus changes', 'Lighting or resolution changes', 'Video encoding'], 'Compare nearby frames in the original recording.', 'Standard deviation of sampled detail measurements'),
    'Emotionally loaded language': ('Matched wording may deserve closer reading.', ['Quoted speech', 'Legitimate persuasive or emotional reporting'], 'Verify the specific factual claim rather than judging its tone.', 'Small English-language lexicon match'),
    'Attribution cues': ('Attribution wording points to possible evidence sources.', ['A source may be paraphrased, misquoted, or absent.'], 'Open the named source and verify that it supports the same statement.', 'Attribution phrase count'),
    'Linked evidence': ('Links offer potential paths to supporting evidence.', ['Links may be unrelated, outdated, or broken.'], 'Open relevant links and compare their actual content with the claim.', 'Links detected in pasted text; not verified'),
    'Emphasis': ('Capital letters and exclamation marks describe writing style.', ['Headlines, quotations, and normal writing choices'], 'Check the article’s evidence independently of its presentation.', 'Uppercase-word and exclamation-mark counts'),
}


def build_explorer(report):
    cards=[]
    for finding in report.get('findings',[]):
        why, alternatives, next_check, unit=GUIDANCE.get(finding['name'],
            ('This is a recorded observation; its meaning needs context.', ['The measurement alone does not establish authenticity.'], 'Check the original source and recorded limitations.', 'As recorded by the analysis'))
        cards.append({'title':finding['name'],'observation':finding.get('detail',''),'measurement':finding.get('value'),
                      'measurement_label':unit,'why':why,'alternatives':alternatives,'next_check':next_check,
                      'result':'Observation only; not proof of manipulation'})
    coverage=[]
    def check(name,status,detail): coverage.append({'name':name,'status':status,'detail':detail})
    kind=report.get('kind'); metrics=report.get('metrics',{})
    check('Recorded forensic or language checks','completed' if cards else 'not_recorded',f'{len(cards)} observations recorded.')
    if kind in ('image','video'):
        if report.get('model'):
            check('Image-model inference','completed', 'Whole-image AI-generation classifier; scores are uncalibrated.' if report.get('prediction') else 'Experimental and uncalibrated. Video results use image-model inference on sampled frames.')
        else:
            check('Image-model inference','skipped','No model output was recorded. Eligible faces or trained weights may have been unavailable; the stored report does not establish which cause applied.')
        state=report.get('provenance',{}).get('status')
        check('Embedded Content Credentials',{'unavailable':'skipped','verification_error':'failed','invalid':'failed'}.get(state,'completed' if state else 'not_recorded'),
              {'absent':'Check completed; no embedded credentials found. This does not imply authenticity.', 'trusted':'Signature and bundled trust checks passed; factual truth is not established.', 'valid_untrusted':'Integrity passed; signer is not trusted by the bundled list.', 'invalid':'Credentials failed verification; this is not a content verdict.', 'unavailable':'Verification tool unavailable.', 'verification_error':'Verification could not finish.'}.get(state,'No provenance result was recorded.'))
    if kind=='image':
        if report.get('prediction'):
            check('Prediction stability','completed',report['prediction']['explanation']+' Original, JPEG-quality-60 and half-resolution inputs checked; this is not an independent ensemble.')
        check('Reference comparison','completed' if report.get('comparison') else 'skipped','Images were resized to matching dimensions; no geometric alignment.' if report.get('comparison') else 'No reference comparison was included.')
        check('Full-resolution interactive inspection','skipped','The explorer uses retained thumbnails. Zoom enlarges the preview and does not recover original detail.')
    if kind=='video':
        check('Visual sampling','completed' if metrics.get('samples') else 'not_recorded',f"{metrics.get('frames_sampled',0)} frames sampled across {metrics.get('duration_seconds','unknown')} seconds. Gaps between samples remain uninspected except for the recorded short windows.")
        check('Consecutive-frame windows','completed' if metrics.get('temporal_windows') else 'not_recorded',f"{len(metrics.get('temporal_windows',[]))} short windows recorded; measurements are not a temporal deepfake classifier.")
        check('Audio, lip-sync and trained temporal detection','skipped','No audio, lip-sync or trained temporal classifier is included.')
    if kind=='news':
        lookup=report.get('lookup',{}); state=lookup.get('status')
        check('Online candidate-review lookup','skipped' if state in (None,'disabled') else 'partial' if state=='partial' else 'completed',
              'Not requested; no online claim lookup was performed.' if state in (None,'disabled') else 'Candidate reviews require manual claim and context matching. '+('Some requests failed.' if state=='partial' else 'No match is not a false-claim verdict.'))
        check('Primary-source and claim equivalence verification','skipped','Sources and dates must be compared by a human. No automated factual verdict is assigned.')
    samples=metrics.get('samples',[])
    gaps=[{'from_seconds':a['time'],'to_seconds':b['time'],'gap_seconds':round(b['time']-a['time'],3)} for a,b in zip(samples,samples[1:])]
    return {'cards':cards,'coverage':coverage,'sample_gaps':gaps,'notice':'All measurements are observations. Completed means the check ran, not that the content is authentic.'}
