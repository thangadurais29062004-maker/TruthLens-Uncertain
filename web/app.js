'use strict';
const $ = id => document.getElementById(id);
let kind = 'image', selectedFile = null, objectURL = null, activeReport = null, busy = false;
let historyRefresh = null, historyCache = [], selectedReports = new Set(), activeView = 'dashboard';
let batchFiles = [], batchItems = [], batchRunning = false, batchStopRequested = false, batchJob = null;
let historyRequestVersion = 0;
let currentJob = null, authMode = 'login', registrationAllowed = true;
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) { if (response.status === 401) openAuth(); throw new Error(payload.error || 'The request could not be completed.'); }
  return payload;
}
function openAuth() { if (!$('auth-dialog').open) $('auth-dialog').showModal(); }
$('auth-dialog').addEventListener('cancel', event => event.preventDefault());
$('auth-toggle').addEventListener('click', () => {
  if (!registrationAllowed) return;
  authMode = authMode === 'login' ? 'register' : 'login';
  $('auth-heading').textContent = authMode === 'login' ? 'Sign in to TruthLens' : 'Create your account';
  $('auth-submit').textContent = authMode === 'login' ? 'Sign in' : 'Create account';
  $('auth-toggle').textContent = authMode === 'login' ? 'Create a local account' : 'Already registered? Sign in';
  $('auth-password').minLength = authMode === 'register' ? 12 : 1;
  $('auth-password').autocomplete = authMode === 'register' ? 'new-password' : 'current-password';
  $('auth-error').hidden = true;
});
$('auth-form').addEventListener('submit', async event => {
  event.preventDefault(); $('auth-error').hidden = true; $('auth-submit').disabled = true;
  try {
    const result = await api(`/api/auth/${authMode}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('auth-username').value,password:$('auth-password').value})});
    $('auth-password').value = ''; $('auth-dialog').close(); $('current-user').textContent = result.user.username; $('sign-out').hidden = false;
    await loadHistory();
    await refreshBatch();
  } catch (error) { $('auth-error').textContent = error.message; $('auth-error').hidden = false; }
  finally { $('auth-submit').disabled = false; }
});
$('sign-out').addEventListener('click', async () => {
  if (batchUploading) {setView('batch');$('batch-error').textContent='Wait for uploads to finish before signing out.';$('batch-error').hidden=false;return;}
  batchRequestVersion++;clearTimeout(batchRefresh);
  if (busy) {setView('analyze');return showError('Finish or cancel your active analysis before signing out.');}
  try { await api('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); serverBatchId=null;batchRunning=false;activeReport = null; $('report').hidden = true; $('current-user').textContent = ''; $('sign-out').hidden = true; $('history-list').replaceChildren(); $('history-count').textContent = '0'; historyRequestVersion++; historyCache = []; selectedReports.clear(); batchFiles = []; batchItems = []; $('batch-input').value = ''; $('batch-results').hidden = true; updateBatchSelection(); renderLibrary(); renderDashboard(); openAuth(); } catch(error) { showError(error.message); }
});
$('cancel-job').addEventListener('click',async () => {
  if (!currentJob) return;
  $('cancel-job').disabled = true;
  try { await api(`/api/reports/${currentJob}/cancel`,{method:'POST',headers:{'X-TruthLens-Action':'cancel'}}); $('progress-stage').textContent = 'Stopping analysis…'; }
  catch(error) { showError(error.message); }
});
function showError(message) { $('error-message').textContent = message; $('error-message').hidden = false; }
function setView(view) {
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== `view-${view}`);
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === view));
  activeView = view;
  $('view-label').textContent = ({dashboard:'Overview',analyze:'New investigation',batch:'Batch analysis',history:'Report library',methods:'Model & methods'})[view] || 'Overview';
  document.querySelectorAll('.nav').forEach(el => { if (el.dataset.view === view) el.setAttribute('aria-current','page'); else el.removeAttribute('aria-current'); });
  if (['history','dashboard'].includes(view)) loadHistory();
  if(view==='batch' && !batchUploading)refreshBatch();
  if(view==='methods')loadValidation();
  window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelectorAll('[data-start]').forEach(button => button.addEventListener('click', () => { setView('analyze'); if (!busy) setKind(button.dataset.start); }));
function clearFile() {
  selectedFile = null;
  $('file-input').value = '';
  $('file-preview').hidden = true;
  $('preview-video').pause();
  $('preview-video').removeAttribute('src');
  $('preview-image').removeAttribute('src');
  if (objectURL) URL.revokeObjectURL(objectURL);
  objectURL = null;
}
function setKind(next) {
  if (busy) return;
  kind = next;
  clearFile();
  $('error-message').hidden = true;
  document.querySelectorAll('.tab').forEach(el => { el.classList.toggle('active', el.dataset.kind === kind); el.setAttribute('aria-selected', String(el.dataset.kind === kind)); el.tabIndex = el.dataset.kind === kind ? 0 : -1; });
  $('media-input').hidden = kind === 'news';
  $('news-input').hidden = kind !== 'news';
  $('comparison-input').hidden = kind !== 'image';
  $('lookup-input').hidden = kind !== 'news';
  $('reference-input').value = '';
  $('file-input').accept = kind === 'image' ? 'image/jpeg,image/png,image/webp' : '.mp4,.mov,.webm,.avi';
  $('drop-title').textContent = kind === 'image' ? 'Drop an image to investigate' : 'Drop a video to investigate';
  $('file-help').textContent = kind === 'image' ? 'JPEG, PNG, WebP · up to 50 MB · 20 megapixels' : 'MP4, MOV, WebM, AVI · 50 MB · 2 minutes · up to 4K';
}
const tabs = [...document.querySelectorAll('.tab')];
tabs.forEach((button, index) => {
  button.addEventListener('click', () => setKind(button.dataset.kind));
  button.addEventListener('keydown', event => {
    if (busy) return;
    const directions = {ArrowRight: 1, ArrowLeft: -1};
    if (event.key in directions) { event.preventDefault(); const next = tabs[(index + directions[event.key] + tabs.length) % tabs.length]; setKind(next.dataset.kind); next.focus(); }
    if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); const next = tabs[event.key === 'Home' ? 0 : tabs.length - 1]; setKind(next.dataset.kind); next.focus(); }
  });
});
function chooseFile(file) {
  if (busy || !file) return;
  $('error-message').hidden = true;
  if (file.size === 0 || file.size > 50 * 1024 * 1024) return showError('Choose a nonempty file up to 50 MB.');
  const valid = kind === 'image' ? /\.(jpe?g|png|webp)$/i : /\.(mp4|mov|webm|avi)$/i;
  if (!valid.test(file.name)) return showError(`Choose a supported ${kind} file.`);
  clearFile();
  selectedFile = file;
  objectURL = URL.createObjectURL(file);
  $('file-name').textContent = file.name;
  $('file-size').textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB`;
  $('preview-image').hidden = kind !== 'image';
  $('preview-video').hidden = kind !== 'video';
  $(kind === 'image' ? 'preview-image' : 'preview-video').src = objectURL;
  $('file-preview').hidden = false;
}
$('file-input').addEventListener('change', e => chooseFile(e.target.files[0]));
$('remove-file').addEventListener('click', () => { if (!busy) clearFile(); });
['dragenter', 'dragover'].forEach(type => $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.add('dragging'); }));
['dragleave', 'drop'].forEach(type => $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.remove('dragging'); if (type === 'drop') chooseFile(e.dataTransfer.files[0]); }));
$('article-text').addEventListener('input', () => $('character-count').textContent = `${$('article-text').value.length.toLocaleString()} / 50,000`);
$('sample-news').addEventListener('click', () => {
  $('article-title').value = 'Sample: a claim that needs evidence';
  $('article-text').value = 'SHOCKING! A new miracle device is guaranteed to cut every household’s electricity bill by 90%. Everyone knows the power companies do not want this secret revealed. According to an unnamed expert, the device works in all homes. The article provides no study, manufacturer details, or independently measured results. Buy it today!';
  $('article-text').dispatchEvent(new Event('input'));
});
function setBusy(value) {
  busy = value;
  if(value){
    $('report').hidden=true; activeReport=null;
    $('progress-bar').value=0;
    $('progress-stage').textContent='Submitting image or article…';
    $('progress-detail').textContent='Starting a new analysis';
  }
  $('analyze-button').disabled = value;
  $('analyze-button').textContent = value ? 'Analysis in progress…' : 'Analyze content';
  $('progress').hidden = !value;
  document.querySelectorAll('#analysis-form input, #analysis-form textarea, #analysis-form button:not(#analyze-button):not(#cancel-job), .tab').forEach(el => el.disabled = value);
  $('cancel-job').disabled = false;
}
$('analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  if (batchRunning) return showError('Your batch is running. Finish or stop it before starting another analysis.');
  $('error-message').hidden = true;
  const submittedKind = kind;
  let body, name, contentType, primaryLength = null;
  if (kind === 'news') {
    const text = $('article-text').value.trim();
    if (text.length < 40) return showError('Paste at least 40 characters of article text.');
    body = JSON.stringify({text, source_url: $('source-url').value, online: $('online-lookup').checked});
    name = $('article-title').value.trim() || 'Untitled article';
    contentType = 'application/json';
  } else {
    if (!selectedFile) return showError(`Select a ${kind} file first.`);
    body = selectedFile; name = selectedFile.name; contentType = 'application/octet-stream';
    const reference = $('reference-input').files[0];
    if (kind === 'image' && reference) {
      if (!/\.(jpe?g|png|webp)$/i.test(reference.name) || reference.size + selectedFile.size > 50 * 1024 * 1024) return showError('Choose a supported reference image. Combined size must be under 50 MB.');
      primaryLength = selectedFile.size; body = new Blob([selectedFile,reference]);
    }
  }
  setBusy(true);
  try {
    const headers = {'Content-Type':contentType}; if (primaryLength) headers['X-TruthLens-Primary-Length'] = String(primaryLength);
    const job = await api(`/api/analyze/${submittedKind}?name=${encodeURIComponent(name)}`, {method:'POST',headers,body});
    currentJob = job.id;
    let completed = false;
    for (let attempt = 0; attempt < 600; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const result = await api(`/api/reports/${job.id}`);
      $('progress-bar').value = result.report.progress || 0;
      $('progress-stage').textContent = result.report.stage || 'Preparing report';
      $('progress-detail').textContent = `${result.report.progress || 0}% · ${result.status}`;
      if (result.status === 'failed') throw new Error(result.report.error);
      if (result.status === 'cancelled') throw new Error('Analysis cancelled. Temporary uploads were removed.');
      if (result.status === 'complete') { renderReport(result.report); completed = true; break; }
    }
    if (!completed) throw new Error('Analysis is still running. Check report history shortly.');
    await loadHistory();
  } catch (error) { showError(error.message); }
  finally { currentJob = null; setBusy(false); await loadHistory(); }
});

function safeImage(value) { return /^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(value || '') ? value : ''; }
function safeLink(value) { try { const url = new URL(value); return ['https:','http:'].includes(url.protocol) ? url.href : '#'; } catch { return '#'; } }
function friendlySummary(report) {
  if (report.kind==='image' && report.prediction) return {title:report.prediction.label,text:report.prediction.explanation,action:report.prediction.notice};
  const descriptions = {
    image: 'We checked the image for editing clues and, where possible, examined faces. These checks cannot confirm whether the image is real or altered.',
    video: 'We checked selected video frames for faces and visual changes. These checks cannot confirm whether the video is real or altered.',
    news: 'We picked out claims and wording worth checking. Writing style alone cannot tell us whether an article is true.'
  };
  return {title: 'More evidence is needed', text: descriptions[report.kind] || report.summary,
    action: report.kind === 'news' ? 'Before sharing, check the main claims against the original study, statement, or other reliable sources.' : 'Before sharing, look for the original image or recording and check who published it.'};
}
function friendlyFinding(finding, report) {
  const descriptions = {
    'File structure': ['The image opened successfully', 'We could read the file. This tells us it is usable, but does not tell us whether it has been edited.'],
    'Editing metadata': ['Editing information', report.metrics?.software ? 'The file mentions editing software. Ordinary edits, such as cropping or adjusting brightness, can leave this information too.' : 'No editing-software label was found. Many files lose this information when they are shared, so this does not prove the image is original.'],
    'Recompression residual': ['Compression patterns', 'We compared the image with a newly compressed copy. Bright areas in the comparison can come from texture or normal compression; they do not prove tampering.'],
    'Fine-detail variation': ['Image detail', 'We measured sharpness and texture. Blur, sharpening, and compression can all change this measurement.'],
    'Video structure': ['Video frames checked', `We checked ${report.metrics?.frames_sampled ?? 'selected'} frames across the video. Brief edits between these frames could be missed.`],
    'Visual changes': ['Changes between frames', 'We measured how the picture changes over time. Camera movement and scene cuts can cause large changes too.'],
    'Detail variation': ['Changes in sharpness', 'We compared detail across frames. Lighting, focus, and video compression can change how sharp a frame looks.'],
    'Emotionally loaded language': ['Strong or persuasive wording', 'Some phrases encourage a strong reaction. Read them carefully, but remember that this wording does not make a claim false.'],
    'Attribution cues': ['References to sources', 'We looked for wording that mentions experts, studies, or reports. You still need to check that those sources exist and support the claim.'],
    'Linked evidence': ['Links to supporting evidence', 'We counted links in the article text. The links have not been opened or checked.'],
    'Emphasis': ['Attention-grabbing style', 'We checked for capital letters and exclamation marks. These are writing choices, not evidence that an article is false.']
  };
  const [title, text] = descriptions[finding.name] || [finding.name, finding.detail];
  return {title, text};
}
function credentialSummary(status) {
  return ({absent: ['No signed history found', 'This file has no embedded Content Credentials. Many ordinary files do not have them.'],
    trusted: ['Signed history verified', 'The file’s signed history passed the checks against the bundled trust list. This does not prove that its content is true.'],
    valid_untrusted: ['Signature checked; signer not trusted', 'The signature passed the integrity checks, but its signer is not trusted by the bundled list.'],
    invalid: ['Signed history could not be verified', 'The credentials failed verification. Review the original file and its history before relying on them.'],
    unavailable: ['Signed-history check unavailable', 'The verification tool is not available for this check.'],
    verification_error: ['Signed-history check could not finish', 'We could not complete verification. This is not a verdict about the content.']})[status] || ['Signed history needs review', 'The credentials need further review. Open the details to see what the verifier reported.'];
}
function explorerHTML(report) {
  const explorer=report.explorer;if(!explorer)return '';
  return `<section class="visual-section"><span class="eyebrow">EVIDENCE EXPLORER</span><h3>Analysis coverage</h3><p class="input-help">${esc(explorer.notice)}</p><div class="coverage-list">${explorer.coverage.map(check=>`<article><span class="coverage-state ${esc(check.status)}">${esc(({completed:'Completed',skipped:'Skipped',failed:'Could not verify',partial:'Partial',not_recorded:'Not recorded'})[check.status] || check.status)}</span><div><strong>${esc(check.name)}</strong><p>${esc(check.detail)}</p></div></article>`).join('')}</div><h3>Explore each observation</h3><div class="explorer-cards">${explorer.cards.map((card,index)=>`<details class="observation-card" ${index===0?'open':''}><summary><span>${String(index+1).padStart(2,'0')}</span><strong>${esc(card.title)}</strong><small>${card.measurement!=null?esc(card.measurement):'Recorded observation'}</small></summary><div class="observation-body"><h4>Observation & measurement</h4><p>${esc(card.observation)}</p><p class="measurement-label">${esc(card.measurement_label)}</p><h4>Why it matters</h4><p>${esc(card.why)}</p><h4>Alternative explanations</h4><ul>${card.alternatives.map(text=>`<li>${esc(text)}</li>`).join('')}</ul><h4>Next check</h4><p>${esc(card.next_check)}</p><small>${esc(card.result)}</small></div></details>`).join('')}</div></section>`;
}
function imageExplorerHTML(report) {
  if(!report.visuals?.original)return '';
  return `<section class="visual-section image-explorer"><h3>Interactive image comparison</h3><p class="input-help">Retained previews, not full-resolution originals. Zoom enlarges pixels already present; bright differences are not confirmed edits.</p><div class="image-controls"><label>Compare with<select id="image-mode"><option value="residual">Compression overlay</option>${report.comparison?.aligned_input?'<option value="reference">Reference · dimensions matched</option><option value="difference">Reference pixel difference</option>':''}</select></label><label>Preview zoom<select id="image-zoom"><option value="100">1×</option><option value="200">2×</option><option value="300">3×</option></select></label><button id="image-reset" type="button" class="secondary">Reset view</button></div><div class="image-scroll"><div class="compare-stage" id="compare-stage"><img id="compare-base" src="${safeImage(report.visuals.original)}" alt="Submitted image preview"><img id="compare-overlay" src="${safeImage(report.visuals.residual_overlay)}" alt="Compression residual overlay" class="compare-overlay"></div></div><label class="wipe-label" for="image-wipe">Comparison reveal <output id="image-wipe-value">50%</output></label><input id="image-wipe" type="range" min="0" max="100" value="50" aria-label="Reveal comparison overlay"><p id="image-comparison-note" class="input-help">0% shows the input; 100% shows the full compression overlay. Normal texture and recompression also create differences.</p></section>`;
}
function bindImageExplorer(report) {
  if(!$('image-mode'))return;
  function update(){
    const mode=$('image-mode').value;
    const reference=mode!=='residual';
    $('compare-base').src=safeImage(reference?report.comparison.aligned_input:report.visuals.original);
    $('compare-overlay').src=safeImage(mode==='reference'?report.comparison.reference:mode==='difference'?report.comparison.difference:report.visuals.residual_overlay);
    $('compare-base').alt=reference?'Submitted image resized to reference dimensions':'Submitted image preview';
    $('compare-overlay').alt=mode==='reference'?'Supplied reference preview':mode==='difference'?'Pixel difference preview':'Compression residual overlay';
    $('compare-stage').style.width=$('image-zoom').value+'%';
    $('compare-overlay').style.clipPath=`inset(0 ${100-Number($('image-wipe').value)}% 0 0)`;
    $('image-wipe-value').textContent=$('image-wipe').value+'%';
    $('image-comparison-note').textContent=reference?report.comparison.description:'0% shows the input; 100% shows the full compression overlay. Normal texture and recompression also create differences.';
  }
  for(const id of ['image-mode','image-zoom','image-wipe'])$(id).addEventListener('input',update);
  $('image-reset').addEventListener('click',()=>{$('image-mode').value='residual';$('image-zoom').value='100';$('image-wipe').value='50';update();});
  update();
}
function predictionHTML(report) {
  const p=report.prediction;
  if(!p)return '';
  const b=p.benchmark, t=b?.selective;
  const measured=b?.original?.metrics?`<p class="input-help">Local sample: ${b.original.metrics.count} images · ${(b.original.metrics.accuracy*100).toFixed(1)}% binary accuracy. ${t?`${(t.accuracy*100).toFixed(1)}% correct among ${t.count} decided predictions; ${t.uncertain} uncertain. Likely-real precision ${(t.real_precision*100).toFixed(1)}%; likely-AI precision ${(t.fake_precision*100).toFixed(1)}%.`:''} These are dataset measurements, not confidence in this image.</p>`:'';
  return `<section class="surface evidence-guide"><div class="eyebrow">REAL / AI-GENERATED IMAGE PREDICTION</div><h3>${esc(p.task)}</h3><p>${esc(p.notice)}</p>${measured}<div class="coverage-list">${Object.entries(p.scores).map(([name,value])=>`<article class="coverage-card"><span class="coverage-pill">${value==null?'Unavailable':(value*100).toFixed(1)+'%'}</span><div><strong>${esc(name.replaceAll('_',' '))}</strong><p>Synthetic-image model score; not confidence.</p></div></article>`).join('')}</div><p class="input-help">${esc(p.policy)} Likely real requires every score ≤20%; likely AI-generated requires every score ≥80%. Other results remain uncertain.</p><p class="input-help">Existing reports retain their original model. Submit the image again to use the current detector.</p></section>`;
}
function evidenceHTML(report) {
  let html = imageExplorerHTML(report);
  if (report.visuals) html += `<section class="visual-section"><h3>Visual evidence</h3><div class="evidence-grid"><figure><img src="${safeImage(report.visuals.original)}" alt="Submitted image excerpt"><figcaption>Your image</figcaption></figure><figure><img src="${safeImage(report.visuals.residual_overlay)}" alt="Amplified recompression residual overlay"><figcaption>Compression comparison</figcaption></figure></div><p class="input-help">Bright areas show differences after compression. They do not mark confirmed edits.</p>${report.visuals.face_preview ? `<img class="face-evidence" src="${safeImage(report.visuals.face_preview)}" alt="Detected face regions">` : ''}</section>`;
  if (report.comparison) html += `<section class="visual-section"><h3>Reference comparison</h3><div class="evidence-grid"><figure><img src="${safeImage(report.comparison.reference)}" alt="User supplied reference"><figcaption>Reference image</figcaption></figure><figure><img src="${safeImage(report.comparison.difference)}" alt="Pixel differences from reference"><figcaption>Pixel difference · mean ${esc(report.comparison.mean_absolute_difference)}</figcaption></figure></div><p class="input-help">${esc(report.comparison.description)}</p></section>`;
  if (report.kind === 'video') html += `<section class="visual-section"><h3>Video evidence timeline</h3><p class="input-help">Track IDs use geometric overlap, not identity recognition. Select a timestamp to open the recorded frame. Seeking the original requires a matching file checksum.</p><div class="timeline">${(report.metrics.samples || []).map((sample,index) => `<button class="frame-card" data-sample="${index}" aria-label="Inspect frame at ${sample.time} seconds"><img src="${safeImage(sample.thumbnail)}" alt="Annotated frame at ${sample.time} seconds"><strong>${sample.time.toFixed(2)}s</strong><span>${(sample.faces || []).length} faces · detail ${sample.detail}</span><span>Change: ${sample.difference ?? 'Not recorded'}</span>${sample.model_probability != null ? `<span>Model output ${(sample.model_probability * 100).toFixed(1)}%</span>` : ''}</button>`).join('')}</div><p class="input-help">${(report.metrics.temporal_windows || []).length} short consecutive-frame windows inspected around large visual changes. Measurements are not temporal deepfake predictions. Only listed samples and short windows were inspected; gaps remain.</p><section id="frame-detail" class="frame-detail" aria-live="polite" hidden></section><details><summary>See gaps between sampled timestamps</summary><ul>${(report.explorer?.sample_gaps || []).map(g=>`<li>${g.from_seconds.toFixed(2)}–${g.to_seconds.toFixed(2)} seconds: ${g.gap_seconds.toFixed(2)} seconds between samples</li>`).join('')}</ul><p>These are sample intervals, not confirmed continuous inspection coverage. Short windows may cover small portions.</p></details></section>`;
  if (report.highlighted_passages?.length) html += `<section class="visual-section"><h3>Highlighted language</h3><div class="passage-list">${report.highlighted_passages.map(p => `<span><mark>${esc(p.text)}</mark> </span>`).join('')}</div></section>`;
  if (report.claims?.length) html += `<section class="visual-section"><h3>Claims to check</h3><p class="input-help">${report.lookup?.status === 'disabled' || !report.lookup ? 'Online source lookup was not requested for this report.' : `Source lookup: ${esc(report.lookup.provider || 'online sources')}. ${report.lookup.status === 'partial' ? 'Some sources could not be reached.' : 'Available sources were checked.'}`} Related reviews still need to be compared with the claim.</p>${(report.lookup?.errors || []).map(error => `<p class="error">${esc(error)}</p>`).join('')}${report.claims.map(claim => `<article class="claim-card"><p>${esc(claim.text)}</p><strong>${claim.sources.length ? 'Related reviews found — compare them with this claim' : 'Not verified · no supporting review attached'}</strong>${claim.sources.map(source => `<div class="source-card"><a href="${esc(safeLink(source.url))}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.claim_reviewed || 'Read source')}</a><p>${esc(source.publisher)} · ${esc(source.date)} · Publisher rating: ${esc(source.rating)}</p><p><strong>Reviewed claim:</strong> ${esc(source.claim_reviewed || 'Exact claim wording not supplied by this source.')}</p><p class="input-help">Compare wording, publication date, location, and context. A related review does not automatically verify this article.</p><small>${esc(source.source_type)}</small></div>`).join('')}</article>`).join('')}</section>`;
  if (report.provenance) {
    const [title, text] = credentialSummary(report.provenance.status);
    html += `<section class="provenance-panel"><div class="mini-label">FILE HISTORY · CONTENT CREDENTIALS</div><h3>${esc(title)}</h3><p>${esc(text)}</p><details><summary>See verification details</summary><p>${esc(report.provenance.detail)}</p>${report.provenance.signature?.issuer ? `<p>Issuer: ${esc(report.provenance.signature.issuer)}</p>` : ''}</details></section>`;
  }
  return html;
}
function renderReport(report) {
  activeReport = report;
  const summary = friendlySummary(report);
  const guide=report.evidence_summary;
  const labels=report.labels || {folder:'',tags:[],favourite:false};
  const findings = report.findings.map(f => { const friendly = friendlyFinding(f, report); return `<div class="finding"><span class="finding-badge ${f.status === 'review' ? 'review' : ''}">${f.status === 'review' ? 'Take a look' : 'Checked'}</span><div><h3>${esc(friendly.title)}</h3><p>${esc(friendly.text)}</p></div></div>`; }).join('');
  const model = report.model ? `<div class="model-result"><h3>${esc(report.model.name)}</h3><p>Experimental model score: <strong>${(report.model.manipulated_probability * 100).toFixed(1)}%</strong> · ${esc(report.model.method)}</p><p>This score is not a verified probability that the content is fake.</p><p>${esc(report.model.scope)}. ${esc(report.model.note)}</p><p>${esc(report.model.evaluation_status || '')}</p>${report.model.source ? `<a href="${esc(safeLink(report.model.source))}" target="_blank" rel="noopener noreferrer">Model source and evaluation</a>` : ''}</div>` : report.kind === "news" ? "" : `<div class="model-result"><p>No eligible model prediction. Face-domain models are skipped when no face is detected.</p></div>`;
  $('report').innerHTML = `<div class="report-header"><div><div class="eyebrow">YOUR RESULTS / ${esc(report.kind === 'news' ? 'ARTICLE' : report.kind.toUpperCase())}</div><h2 class="report-title">${esc(report.name)}</h2></div><div class="report-actions"><button class="secondary" id="print-report">Download PDF</button><button class="secondary" id="download-report">Export data (JSON)</button></div></div><form id="report-label-form" class="surface report-labels"><label>Folder<input id="report-folder" maxlength="64" value="${esc(labels.folder)}" placeholder="e.g. Research"></label><label>Tags · comma separated<input id="report-tags" value="${esc(labels.tags.join(', '))}" placeholder="Up to 5 tags"></label><label class="favourite-label"><input type="checkbox" id="report-favourite" ${labels.favourite?'checked':''}>Favourite</label><button class="secondary" type="submit">Save organization</button><span id="label-feedback" role="status"></span></form><section class="result-overview" aria-label="Result summary"><span class="verdict">${report.prediction ? 'Model prediction · AI generation' : 'Not verified'}</span><h3>${esc(summary.title)}</h3><p>${esc(summary.text)}</p>${guide?'':`<div class="next-action"><strong>What to do next</strong><p>${esc(summary.action)}</p></div>`}</section>${guide?`<section class="surface evidence-guide"><h3>What we noticed</h3><ul>${guide.noticed.map(text=>`<li>${esc(text)}</li>`).join('')}</ul><h3>Why it matters</h3><p>${esc(guide.why)}</p><h3>What to check next</h3><ol>${guide.next_steps.map(text=>`<li>${esc(text)}</li>`).join('')}</ol></section>`:''}${predictionHTML(report)}${explorerHTML(report)}<details class="technical-details"><summary>Quick finding descriptions</summary><div class="findings">${findings}</div></details><details class="technical-details"><summary>How this was checked · technical details</summary>${model}<h3>Detailed observations</h3>${report.findings.map(f => `<p><strong>${esc(f.name)}</strong>${f.value != null ? ` · ${esc(f.value)}` : ''}<br>${esc(f.detail)}</p>`).join('')}<h3>What these checks cannot tell you</h3><ul>${report.limitations.map(item => `<li>${esc(item)}</li>`).join('')}</ul><pre>${esc(JSON.stringify(report.metrics, (key,value) => ['thumbnail','face_preview'].includes(key) ? '[Image shown in report]' : value, 2))}</pre><div class="hash">SHA-256: ${esc(report.sha256)}<br>Report ${esc(report.id)} · ${esc(new Date(report.created_at).toLocaleString())}</div></details>`;
  $('download-report').addEventListener('click', () => {
    const link = document.createElement('a'); link.href = `/api/reports/${activeReport.id}/download`; link.download = `truthlens-${activeReport.id}.json`; document.body.appendChild(link); link.click(); link.remove();
  });
  $('report').hidden = false;
  $('report-label-form').addEventListener('submit',async event=>{
    event.preventDefault();
    try {await api(`/api/reports/${report.id}/labels`,{method:'PATCH',headers:{'Content-Type':'application/json','X-TruthLens-Action':'review'},body:JSON.stringify({folder:$('report-folder').value,tags:$('report-tags').value.split(',').map(t=>t.trim()).filter(Boolean),favourite:$('report-favourite').checked})});$('label-feedback').textContent='Saved';await loadHistory();}
    catch(error){$('label-feedback').textContent=error.message;}
  });
  $('report').insertAdjacentHTML('beforeend',evidenceHTML(report));
  bindImageExplorer(report);
  if(!guide)$('report').insertAdjacentHTML('beforeend',`<section class="next-steps"><h3>Your next steps</h3><ol>${report.next_steps.map(item => `<li>${esc(item)}</li>`).join('')}</ol></section>`);
  document.querySelectorAll('.claim-card').forEach((card,index) => {
    const review=report.claims[index].analyst_review;
    card.insertAdjacentHTML('beforeend',`<div class="analyst-review"><label for="claim-assessment-${index}">Your assessment <span class="optional">human review</span></label><select id="claim-assessment-${index}">${['Insufficient evidence','Supported','Contradicted'].map(value=>`<option ${review?.assessment===value?'selected':''}>${value}</option>`).join('')}</select><label for="claim-note-${index}">Evidence and reasoning</label><textarea id="claim-note-${index}" rows="2" maxlength="2000" placeholder="Describe which sources support your assessment…">${esc(review?.note || '')}</textarea><button class="secondary" data-review="${index}">Save assessment</button>${review?`<p class="input-help">Saved by ${esc(review.reviewer)}. ${esc(review.label)}</p>`:''}</div>`);
  });
  document.querySelectorAll('[data-review]').forEach(button=>button.addEventListener('click',async()=>{
    const index=button.dataset.review; button.disabled=true;
    try { const result=await api(`/api/reports/${activeReport.id}/claims/${index}`,{method:'PATCH',headers:{'Content-Type':'application/json','X-TruthLens-Action':'review'},body:JSON.stringify({assessment:$(`claim-assessment-${index}`).value,note:$(`claim-note-${index}`).value})}); renderReport(result.report); }
    catch(error){showError(error.message);button.disabled=false;}
  }));
  $('print-report').textContent = 'Download PDF';
  $('print-report').addEventListener('click', () => { const link = document.createElement('a'); link.href = `/api/reports/${activeReport.id}/pdf`; link.download = `truthlens-${activeReport.id}.pdf`; document.body.appendChild(link); link.click(); link.remove(); });
  document.querySelectorAll('[data-sample]').forEach(button=>button.addEventListener('click',()=>{
    const sample=report.metrics.samples[Number(button.dataset.sample)];
    $('frame-detail').hidden=false;
    $('frame-detail').innerHTML=`<h3>Recorded frame · ${sample.time.toFixed(2)} seconds</h3><img src="${safeImage(sample.thumbnail)}" alt="Recorded annotated frame at ${sample.time} seconds"><p>${(sample.faces || []).length} detected faces · detail ${esc(sample.detail)} · change from prior distributed sample ${esc(sample.difference ?? 'Not recorded')}</p>${sample.model_probability!=null?`<p>Experimental image-model output: ${(sample.model_probability*100).toFixed(1)}%. This is uncalibrated and is not a temporal prediction.</p>`:''}<button class="secondary" id="seek-original">Seek in original video</button><p id="seek-feedback" role="status" class="input-help">Track IDs describe geometric overlap, not identity. This is a retained thumbnail.</p>`;
    $('seek-original').addEventListener('click',async()=>{
      const file=selectedFile, feedback=$('seek-feedback'), video=$('preview-video');
      if(!file || !video.src){feedback.textContent='Select the original video first. The recorded frame remains available.';return;}
      try {
        feedback.textContent='Checking file checksum…';
        const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
        const hash=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
        if(activeReport?.id!==report.id || !feedback.isConnected){return;}
        if(hash!==report.sha256 || file!==selectedFile){feedback.textContent='This file does not match the report. Select the original recording.';return;}
        video.currentTime=sample.time;video.scrollIntoView({block:'center'});feedback.textContent='Matching original selected.';
      } catch(error){feedback.textContent='The file could not be verified. Inspect the retained frame instead.';}
    });
    $('frame-detail').scrollIntoView({block:'center',behavior:'instant'});
  }));
  $('report').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',block:'start'});
}

const typeLabels = {image:'Image',video:'Video',news:'Article'};
const typeIcons = {image:'▧',video:'▷',news:'≡'};
const statusLabels = {complete:'Completed',processing:'In progress',queued:'Queued',failed:'Failed',cancelled:'Cancelled',waiting:'Waiting',skipped:'Not submitted'};
function statusPill(status) { return `<span class="status-pill ${['complete','processing','queued','failed','cancelled'].includes(status) ? status : ''}">${esc(statusLabels[status] || status)}</span>`; }
function reportDate(created) { return new Date(created * 1000).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}); }
function renderDashboard() {
  $('stat-total').textContent = historyCache.length;
  $('stat-complete').textContent = historyCache.filter(r=>r.status==='complete').length;
  $('stat-active').textContent = historyCache.filter(r=>['processing','queued'].includes(r.status)).length;
  $('stat-attention').textContent = historyCache.filter(r=>['failed','cancelled'].includes(r.status)).length;
  $('recent-reports').innerHTML = historyCache.length ? historyCache.slice(0,4).map(r=>`<div class="recent-row"><span class="launch-icon ${r.kind==='image'?'violet':r.kind==='video'?'mint':'peach'}" aria-hidden="true">${typeIcons[r.kind] || '◇'}</span><div class="recent-info"><strong title="${esc(r.name)}">${esc(r.name)}</strong><small>${esc(typeLabels[r.kind] || r.kind)} · ${esc(reportDate(r.created))}</small></div>${statusPill(r.status)}<button data-open="${esc(r.id)}" aria-label="Open ${esc(r.name)}">↗</button></div>`).join('') : '<div class="empty"><h2>Your next discovery starts here.</h2><p>Analyze your first file or article. Its report will appear here.</p><button class="secondary" data-start="image">Start an investigation ↗</button></div>';
  $('recent-reports').querySelector('[data-start]')?.addEventListener('click',()=>{setView('analyze');setKind('image');});
}
function renderLibrary() {
  const query=$('history-search').value.trim().toLocaleLowerCase();
  const content=$('history-kind').value, status=$('history-status').value, order=$('history-sort').value;
  const reports=historyCache.filter(r=>(!query || [r.name,r.folder,...(r.tags || [])].join(' ').toLocaleLowerCase().includes(query)) && (!content || r.kind===content) && (!status || r.status===status) && (!$('history-favourites').checked || r.favourite) && (!$('history-folder').value.trim() || r.folder.toLocaleLowerCase()===$('history-folder').value.trim().toLocaleLowerCase()));
  reports.sort((a,b)=>order==='name' ? a.name.localeCompare(b.name) : order==='oldest' ? a.created-b.created : b.created-a.created);
  $('history-matches').textContent = `${reports.length} of ${historyCache.length} reports`;
  $('selected-count').textContent = selectedReports.size;
  $('library-download').disabled = !selectedReports.size;
  $('history-list').innerHTML=reports.length ? reports.map(r=>`<div class="history-row"><input type="checkbox" data-select="${esc(r.id)}" aria-label="Select ${esc(r.name)}" ${selectedReports.has(r.id)?'checked':''} ${r.status!=='complete'?'disabled':''}><span class="kind-tag" aria-hidden="true">${typeIcons[r.kind] || '◇'}</span><div class="history-info"><strong>${r.favourite?'★ ':''}${esc(r.name)}</strong><small>${esc([r.folder,...(r.tags || [])].filter(Boolean).join(' · '))}</small><p>${esc(typeLabels[r.kind] || r.kind)} · ${esc(reportDate(r.created))}</p></div>${statusPill(r.status)}<button class="secondary" data-open="${esc(r.id)}">${r.status==='complete'?'View report':'View status'}</button><button class="delete-report" data-delete="${esc(r.id)}" aria-label="Delete ${esc(r.name)}" ${['queued','processing'].includes(r.status)?'disabled':''}>Delete</button></div>`).join('') : `<div class="empty"><h2>${historyCache.length ? 'No reports match those filters.' : 'No investigations yet.'}</h2><p>${historyCache.length ? 'Try another name or reset the filters.' : 'Your first report will appear after you analyze content.'}</p></div>`;
}
async function openReport(id) {
  try { const result=await api(`/api/reports/${id}`); setView('analyze'); if (result.status==='complete') renderReport(result.report); else showError(['failed','cancelled'].includes(result.status) ? result.report.error : `This investigation is ${statusLabels[result.status] || result.status}. ${result.report.stage || 'Check the library shortly.'}`); }
  catch(error) { setView('analyze');showError(error.message); }
}
async function loadHistory() {
  const version=++historyRequestVersion;
  try {
    const reports=await api('/api/reports');
    if (version!==historyRequestVersion) return;
    historyCache=reports;
    const eligible=new Set(reports.filter(r=>r.status==='complete').map(r=>r.id));
    selectedReports=new Set([...selectedReports].filter(id=>eligible.has(id)));
    $('history-count').textContent=reports.length;
    renderLibrary();renderDashboard();
    clearTimeout(historyRefresh);
    if (reports.some(r=>['queued','processing'].includes(r.status))) historyRefresh=setTimeout(loadHistory,3000);
  } catch(error) { if(version===historyRequestVersion) $('history-list').textContent=error.message; }
}
for (const id of ['history-search','history-kind','history-status','history-sort','history-folder','history-favourites']) $(id).addEventListener(['history-search','history-folder'].includes(id)?'input':'change',renderLibrary);
$('history-reset').addEventListener('click',()=>{ $('history-search').value='';$('history-kind').value='';$('history-status').value='';$('history-sort').value='newest';$('history-folder').value='';$('history-favourites').checked=false;renderLibrary(); });
$('history-list').addEventListener('change',event=>{
  const input=event.target.closest('[data-select]');if(!input)return;
  if (input.checked && selectedReports.size>=20) {input.checked=false;$('library-error').textContent='Select up to 20 reports for one download.';$('library-error').hidden=false;return;}
  if(input.checked) selectedReports.add(input.dataset.select);else selectedReports.delete(input.dataset.select);
  $('library-error').hidden=true;$('selected-count').textContent=selectedReports.size;$('library-download').disabled=!selectedReports.size;
});
for(const id of ['recent-reports','history-list','batch-list']) $(id).addEventListener('click',async event=>{
  const open=event.target.closest('[data-open]');if(open)return openReport(open.dataset.open);
  const remove=event.target.closest('[data-delete]');
  if(remove && confirm('Permanently delete this local report?')) {
    try { await api(`/api/reports/${remove.dataset.delete}`,{method:'DELETE',headers:{'X-TruthLens-Action':'delete'}});if(activeReport?.id===remove.dataset.delete){activeReport=null;$('report').hidden=true;}await loadHistory(); }
    catch(error){$('library-error').textContent=error.message;$('library-error').hidden=false;}
  }
});
function downloadBundle(ids) {
  if(!ids.length || ids.length>20)return;
  const link=document.createElement('a');link.href='/api/reports/bundle?ids='+encodeURIComponent(ids.join(','));link.download='truthlens-report-bundle.zip';document.body.appendChild(link);link.click();link.remove();
}
$('library-download').addEventListener('click',()=>downloadBundle([...selectedReports]));
function updateBatchSelection() {
  const bytes=batchFiles.reduce((n,file)=>n+file.size,0);
  $('batch-selection').textContent=batchFiles.length ? `${batchFiles.length} files · ${(bytes/1024/1024).toFixed(1)} MB total` : 'No files selected';
}
function selectBatch(files) {
  if(batchRunning)return;
  const candidates=[...files];
  const error=candidates.length>8 ? 'Choose up to 8 files.' : candidates.some(f=>!f.size || f.size>50*1024*1024) ? 'Each file must be nonempty and no larger than 50 MB.' : candidates.reduce((n,f)=>n+f.size,0)>200*1024*1024 ? 'Keep the combined size under 200 MB.' : candidates.some(f=>! /\.(jpe?g|png|webp|mp4|mov|webm|avi)$/i.test(f.name)) ? 'Choose supported image or video files.' : '';
  $('batch-error').hidden=!error;
  if(error){$('batch-error').textContent=error;batchFiles=[];$('batch-input').value='';}else batchFiles=candidates;
  updateBatchSelection();
}
$('batch-input').addEventListener('change',event=>selectBatch(event.target.files));
$('batch-clear').addEventListener('click',()=>{if(batchRunning)return;batchFiles=[];$('batch-input').value='';$('batch-error').hidden=true;updateBatchSelection();});
for(const type of ['dragenter','dragover']) $('batch-dropzone').addEventListener(type,event=>{event.preventDefault();if(!batchRunning)$('batch-dropzone').classList.add('dragging');});
for(const type of ['dragleave','drop']) $('batch-dropzone').addEventListener(type,event=>{event.preventDefault();$('batch-dropzone').classList.remove('dragging');if(type==='drop')selectBatch(event.dataTransfer.files);});
function renderBatch() {
  const done=batchItems.filter(r=>r.status==='complete').length;
  const handled=batchItems.filter(r=>['complete','failed','cancelled','skipped'].includes(r.status)).length;
  $('batch-results').hidden=!batchItems.length;
  $('batch-summary').textContent=batchRunning ? `${handled} of ${batchItems.length} finished · ${done} reports ready` : `${done} of ${batchItems.length} reports ready`;
  $('batch-stop').hidden=!batchRunning;
  $('batch-stop').disabled=batchStopRequested;
  $('batch-stop').textContent=batchStopRequested?'Stopping…':'Stop batch';
  $('batch-download').disabled=!done;
  $('batch-list').innerHTML=batchItems.map((r,index)=>`<div class="batch-row"><span class="batch-index">${String(index+1).padStart(2,'0')}</span><div><strong>${esc(r.name)}</strong><small>${esc(r.error || r.stage || ({waiting:'Waiting to start',skipped:'Not submitted',complete:'Report ready'})[r.status] || statusLabels[r.status] || r.status)}</small></div><progress max="100" value="${r.progress || 0}" aria-label="Progress for ${esc(r.name)}"></progress>${r.status==='complete'?`<button class="secondary" data-open="${esc(r.id)}">View report</button>`:statusPill(r.status)}</div>`).join('');
}
$('batch-download').addEventListener('click',()=>downloadBundle(batchItems.filter(r=>r.status==='complete').map(r=>r.id)));
let serverBatchId = null, batchRefresh = null, batchUploading = false, batchRequestVersion = 0;
async function refreshBatch() {
  if(!$('current-user').textContent)return;
  const version=batchRequestVersion;
  try {
    const result=await api('/api/batches/latest');
    if(version!==batchRequestVersion)return;
    serverBatchId=result.id || null; batchItems=result.items || [];
    batchRunning=result.status==='processing' || result.status==='uploading';
    batchStopRequested=result.status==='cancelled';
    for(const id of ['batch-input','batch-start','batch-clear'])$(id).disabled=batchRunning;
    if(result.error){$('batch-error').textContent=result.error;$('batch-error').hidden=false;}
    else if(result.status==='uploading' && !batchUploading){$('batch-error').textContent='An upload was interrupted. Stop this batch, choose the files again, and resubmit.';$('batch-error').hidden=false;}
    renderBatch();await loadHistory();clearTimeout(batchRefresh);
    if(batchRunning || batchItems.some(i=>['queued','processing'].includes(i.status)))batchRefresh=setTimeout(refreshBatch,2000);
  } catch(error){if(version!==batchRequestVersion)return;$('batch-error').textContent='Could not refresh the queue. Completed reports remain in your library.';$('batch-error').hidden=false;clearTimeout(batchRefresh);batchRefresh=setTimeout(refreshBatch,5000);}
}
$('batch-stop').addEventListener('click',async()=>{
  if(!serverBatchId)return;
  try {await api(`/api/batches/${serverBatchId}/cancel`,{method:'POST'});await refreshBatch();await loadHistory();}
  catch(error){$('batch-error').textContent=error.message;$('batch-error').hidden=false;}
});
$('batch-form').addEventListener('submit',async event=>{
  event.preventDefault();if(batchRunning || batchUploading)return;
  if(busy || !batchFiles.length){$('batch-error').textContent=busy?'Finish your current investigation first.':'Choose files to analyze.';$('batch-error').hidden=false;return;}
  batchUploading=true;batchRunning=true;batchStopRequested=false;$('batch-error').hidden=true;
  for(const id of ['batch-input','batch-start','batch-clear'])$(id).disabled=true;
  try {
    const created=await api('/api/batches',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:batchFiles.map(f=>({name:f.name,size:f.size}))})});
    serverBatchId=created.id;batchItems=created.items;renderBatch();
    for(let index=0;index<batchFiles.length;index++){
      batchItems[index].stage='Uploading to local server';renderBatch();
      await api(`/api/batches/${serverBatchId}/files/${index}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:batchFiles[index]});
      batchItems[index].stage='Uploaded; waiting to start';renderBatch();
    }
    await api(`/api/batches/${serverBatchId}/start`,{method:'POST'});
    $('batch-error').textContent='All files uploaded. You can close this tab; the server will continue processing.';$('batch-error').hidden=false;
    batchFiles=[];$('batch-input').value='';updateBatchSelection();
  } catch(error){$('batch-error').textContent=error.message+' Reopen Batch analysis to check the queue.';$('batch-error').hidden=false;}
  finally {batchUploading=false;await refreshBatch();await loadHistory();}
});
function applyTheme(theme) {
  document.documentElement.dataset.theme=theme;
  $('theme-toggle').setAttribute('aria-pressed',String(theme==='dark'));
  $('theme-toggle').setAttribute('aria-label',theme==='dark'?'Switch to light theme':'Switch to dark theme');
  try{localStorage.setItem('truthlens-theme',theme);}catch{}
}
$('theme-toggle').addEventListener('click',()=>applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
try{applyTheme(localStorage.getItem('truthlens-theme')==='dark'?'dark':'light');}catch{applyTheme('light');}
async function loadValidation() {
  try {
    const result=await api('/api/validation');
    $('validation-message').textContent=result.message;
    $('validation-results').innerHTML=Object.entries(result.variants).map(([name,item])=>`<article class="capability-card"><span>${esc(name.replaceAll('_',' ').toUpperCase())}</span><h3>${(item.coverage*100).toFixed(1)}% coverage</h3>${item.metrics?`<p>Binary accuracy ${(item.metrics.accuracy*100).toFixed(1)}% at threshold 0.5</p><p>${item.metrics.count} detected inputs · ${item.metrics.false_positive} false positives · ${item.metrics.false_negative} false negatives</p><p>Precision ${(item.metrics.precision*100).toFixed(1)}% · Recall ${(item.metrics.recall*100).toFixed(1)}%</p><p>False-positive rate ${(item.metrics.false_positive_rate*100).toFixed(1)}% · ROC AUC ${item.metrics.roc_auc.toFixed(3)}</p><p>Brier score ${item.metrics.brier_score.toFixed(3)} · Calibration error ${item.metrics.expected_calibration_error.toFixed(3)}</p>`:'<p>Both classes are needed among detected inputs.</p>'}</article>`).join('');
    if(result.selective){const t=result.selective;$('validation-results').innerHTML+=`<article class="capability-card"><span>THREE-VIEW DECISION POLICY</span><h3>${(t.accuracy*100).toFixed(1)}% among decided images</h3><p>${t.count} decided · ${t.uncertain} uncertain · ${(t.coverage*100).toFixed(1)}% decision coverage</p><p>Likely-real precision ${(t.real_precision*100).toFixed(1)}%; likely-AI precision ${(t.fake_precision*100).toFixed(1)}%. These are sample measurements, not per-image confidence.</p></article>`;}
    if(Object.keys(result.generators || {}).length)$('validation-results').innerHTML+=`<article class="capability-card"><span>KNOWN WEAKNESSES</span><h3>Results vary by generator</h3><details><summary>See sampled generator results</summary>${Object.entries(result.generators).map(([name,g])=>`<p>${esc(name)} · ${g.count} samples · ${(g.accuracy*100).toFixed(1)}% correct classifications at threshold 0.5</p>`).join('')}</details><p>Small groups are not dependable accuracy estimates. Midjourney was a major weakness in this sample.</p></article>`;
    $('validation-state').textContent=({benchmark_available:'BENCHMARK AVAILABLE',model_mismatch:'MODEL MISMATCH',invalid_benchmark:'CHECK BENCHMARK',awaiting_dataset:'AWAITING DATASET'})[result.status] || 'NOT VALIDATED';
  } catch(error){$('validation-message').textContent='Sign in to view benchmark status.';}
}
function renderCapabilities(health) {
  $('engine-capabilities').innerHTML=[
    [health.onnx_configured?'AVAILABLE':'FORENSIC MODE','Image detector',health.onnx_configured?(health.image_prediction_task==='ai_generation'?'Whole-image real/AI-generation prediction is available. Scores remain uncalibrated.':'Pretrained model configured. Outputs are experimental and uncalibrated.'):'Image measurements work without trained model weights.'],
    [health.video?'AVAILABLE':'UNAVAILABLE','Video inspection',health.video?'Frame inspection is available. No trained audio or temporal model is included.':'Video decoding requires OpenCV in the server environment.'],
    [health.google_factchecks?'GOOGLE CONFIGURED':'PUBLIC FEED MODE','Claim lookup',health.google_factchecks?'The Google adapter is configured. Retrieved claims still require human comparison.':'Recent public fact-check titles are available with opt-in lookup.']
  ].map(([state,title,text])=>`<article class="capability-card"><span>${state}</span><h3>${title}</h3><p>${text}</p></article>`).join('');
}
async function init() {
  setKind('image');renderDashboard();renderLibrary();updateBatchSelection();
  try {
    const health = await api('/api/health');
    renderCapabilities(health);
    registrationAllowed = health.registration_allowed;
    $('auth-toggle').hidden = !registrationAllowed;
    $('server-status').textContent = 'Local engine online'; $('server-status').classList.add('online');
    $('model-status').textContent = health.onnx_configured ? (health.image_prediction_task==='ai_generation'?'Real / AI image predictor available':'Pretrained model configured') : health.feature_model ? 'Local feature model available' : 'Forensic mode · no trained model';
    $('model-description').textContent = health.onnx_configured || health.feature_model ? 'Outputs are uncalibrated. Reports include model source and evaluation limits.' : 'Measurements and language cues are active. Model predictions require trained weights.';
    if (!health.video) $('model-description').textContent += ' Install OpenCV to enable video decoding.';
    try { const session = await api('/api/me'); $('current-user').textContent = session.user.username; $('sign-out').hidden = false; await loadHistory(); await refreshBatch(); } catch { openAuth(); }
  } catch (error) { $('server-status').textContent = 'Engine unavailable'; showError('Could not reach the local analysis engine. Start server.py and reload.'); }
}
init();
