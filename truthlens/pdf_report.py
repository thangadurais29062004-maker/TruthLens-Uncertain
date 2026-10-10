from truthlens.summaries import evidence_summary
from truthlens.explorer import build_explorer
import base64
import io
from datetime import datetime, timezone
from html import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, KeepTogether, Table, TableStyle


def export_pdf(report):
    output = io.BytesIO()
    doc = SimpleDocTemplate(output,pagesize=(595,842),rightMargin=42,leftMargin=42,topMargin=48,bottomMargin=42,title='TruthLens evidence report')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='EvidenceBody',fontName='Helvetica',fontSize=10,leading=15,textColor=colors.HexColor('#233344'),spaceAfter=9))
    styles.add(ParagraphStyle(name='EvidenceTitle',fontName='Helvetica-Bold',fontSize=22,leading=27,spaceAfter=16,textColor=colors.HexColor('#122329')))
    styles.add(ParagraphStyle(name='EvidenceSmall',fontName='Helvetica',fontSize=8,leading=11,spaceAfter=6,textColor=colors.HexColor('#4d6275'),splitLongWords=True))
    story=[]
    def text(value,style='EvidenceBody'):
        # Helvetica's encoding has limited coverage; preserve ASCII and common Latin text.
        value=str(value).replace('×','x').replace('–','-').replace('—','-').encode('cp1252','replace').decode('cp1252')
        story.append(Paragraph(escape(value).replace('\n','<br/>'),styles[style]))
    def picture(data,width=300):
        if not data or not data.startswith('data:image/jpeg;base64,'):
            return
        raw=io.BytesIO(base64.b64decode(data.split(',',1)[1]))
        reader=ImageReader(raw); w,h=reader.getSize()
        width=min(width,250*w/h)
        image=Image(raw,width=width,height=width*h/w)
        image.hAlign='LEFT'; story.append(image); story.append(Spacer(1,10))
    text('TRUTHLENS AI / YOUR RESULTS','EvidenceSmall')
    text(report['name'],'EvidenceTitle')
    try:
        created=datetime.fromisoformat(report.get('created_at','')).astimezone(timezone.utc).strftime('%d %b %Y, %H:%M UTC')
    except ValueError:
        created=report.get('created_at','')
    text(f"{report['kind'].capitalize()} report | {created}",'EvidenceSmall')
    prediction=report.get('prediction')
    text(prediction['label'] if prediction else 'More evidence is needed','Heading2')
    if prediction:
        text(prediction['explanation'])
        text('Task: '+prediction['task'])
        text(prediction['notice'])
        text('Synthetic-image model scores (not calibrated confidence):','Heading3')
        for name,value in prediction['scores'].items():
            text(name.replace('_',' ')+': '+(f'{value*100:.1f}%' if value is not None else 'Unavailable'))
        text(prediction['policy'],'EvidenceSmall')
        benchmark=prediction.get('benchmark',{})
        measured=benchmark.get('original',{}).get('metrics') if benchmark.get('original') else None
        if measured:
            text(f"Local benchmark: {measured['count']} sampled images; {measured['accuracy']*100:.1f}% binary accuracy. Dataset results are not confidence in this image.")
            selective=benchmark.get('selective')
            if selective:
                text(f"Three-view policy: {selective['accuracy']*100:.1f}% correct among {selective['count']} decided predictions; {selective['uncertain']} uncertain. Likely-real precision {selective['real_precision']*100:.1f}%; likely-AI precision {selective['fake_precision']*100:.1f}%.")
            text(benchmark.get('message',''),'EvidenceSmall')
    else:
        text('Result: Not verified. These checks cannot confirm whether the content is authentic or misleading.')
    descriptions={
        'image':'We checked the image for editing clues and, where possible, examined faces. Ordinary editing and compression can produce similar clues.',
        'video':'We checked selected video frames for faces and visual changes. Brief edits between the checked frames may be missed.',
        'news':'We picked out claims and wording worth checking. Writing style alone cannot tell us whether an article is true.'}
    text(descriptions.get(report['kind'],report['summary']))
    guide=evidence_summary(report)
    text('What we noticed','Heading2')
    for item in guide['noticed']: text(item)
    text('Why it matters','Heading2'); text(guide['why'])
    text('What to check next','Heading2')
    for index,item in enumerate(guide['next_steps'],1): text(f'{index}. {item}')
    explorer=build_explorer(report)
    text('Analysis coverage','Heading2')
    text(explorer['notice'])
    for check in explorer['coverage']:
        text(check['name']+' - '+check['status'].replace('_',' ').title(),'Heading3')
        text(check['detail'])
    text('Evidence Explorer','Heading2')
    for card in explorer['cards']:
        text(card['title'],'Heading3'); text('Observation: '+card['observation'])
        text('Measurement: '+str(card['measurement'])+' | '+card['measurement_label'] if card['measurement'] is not None else 'Measurement type: '+card['measurement_label'])
        text('Why it matters: '+card['why'])
        text('Alternative explanations: '+'; '.join(card['alternatives']))
        text('Next check: '+card['next_check'])
        text(card['result'],'EvidenceSmall')
    text('What we found','Heading2')
    friendly={
        'File structure':('The image opened successfully','We could read the file. This does not tell us whether it has been edited.'),
        'Editing metadata':('Editing information','Editing labels may come from ordinary edits. Missing labels do not prove that an image is original.'),
        'Recompression residual':('Compression patterns','We compared the image with a newly compressed copy. Texture and normal compression can produce bright areas; these do not prove tampering.'),
        'Fine-detail variation':('Image detail','Blur, sharpening, texture, and compression can change the detail measurement.'),
        'Video structure':('Video frames checked',f"We checked {report.get('metrics',{}).get('frames_sampled','selected')} frames across the video. Edits between these frames could be missed."),
        'Visual changes':('Changes between frames','Camera movement and scene cuts can cause large changes too.'),
        'Detail variation':('Changes in sharpness','Lighting, focus, and compression can change how sharp a frame looks.'),
        'Emotionally loaded language':('Strong or persuasive wording','Some phrases encourage a strong reaction. This wording does not make a claim false.'),
        'Attribution cues':('References to sources','Check that any mentioned experts, studies, or reports exist and support the claim.'),
        'Linked evidence':('Links to supporting evidence','Links in the pasted article have not been opened or checked.'),
        'Emphasis':('Attention-grabbing style','Capital letters and exclamation marks are writing choices, not evidence that an article is false.')}
    for finding in report['findings']:
        title,description=friendly.get(finding['name'],(finding['name'],finding['detail']))
        heading=Paragraph(escape(title),styles['Heading3'])
        body=Paragraph(escape(description),styles['EvidenceBody'])
        story.append(KeepTogether([heading,body]))
    if report.get('visuals'):
        start=len(story)
        text('Visual evidence','Heading2')
        cells=[]
        for key,label in [('original','Your image'),('residual_overlay','Compression comparison')]:
            raw=io.BytesIO(base64.b64decode(report['visuals'][key].split(',',1)[1]))
            w,h=ImageReader(raw).getSize(); width=min(220,240*w/h)
            cells.append([Paragraph(label,styles['EvidenceBody']),Image(raw,width=width,height=width*h/w)])
        table=Table([cells],colWidths=[255,255])
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),10)]))
        story.append(table); story.append(Spacer(1,10))
        text('Brighter areas show differences after compression. They do not mark confirmed edits.','EvidenceSmall')
        story[start:]=[KeepTogether(story[start:])]
    if report.get('comparison'):
        text('Reference comparison','Heading2'); picture(report['comparison']['reference'])
        text(report['comparison']['description'])
        text('Pixel difference from the supplied reference','Heading3'); picture(report['comparison'].get('difference'))
        text('Mean absolute pixel difference: '+str(report['comparison'].get('mean_absolute_difference','Not recorded')))
    if report['kind']=='video':
        text('Sampled video timeline','Heading2')
        for sample in report['metrics'].get('samples',[])[:12]:
            text(f"{sample['time']:.2f}s | detail {sample['detail']} | {len(sample.get('faces',[]))} detected faces | change {sample.get('difference', 'Not recorded')}",'EvidenceSmall')
            picture(sample.get('thumbnail'),180)
        text('PDF shows the first 12 samples; JSON contains the complete sampled timeline.','EvidenceSmall')
    for claim in report.get('claims',[]):
        text('Claim to check','Heading2'); text(claim['text']); text('Related reviews found - compare them with this claim.' if claim.get('sources') else 'Not verified: no supporting review is attached.')
        if claim.get('analyst_review'):
            review=claim['analyst_review']; text(f"Human assessment: {review['assessment']} (reviewer: {review['reviewer']})"); text(review.get('note',''))
        for source in claim.get('sources',[]):
            text(f"{source.get('publisher','')} - {source.get('title','')} - {source.get('rating','')}")
            text('Reviewed claim: '+source.get('claim_reviewed','Not recorded'))
            text('Source date: '+source.get('date','Not recorded'))
            text('Compare wording, date and context with this claim; related reviews are not automatic verification.','EvidenceSmall')
            text(source.get('url',''),'EvidenceSmall')
    if report.get('provenance'):
        text('File history','Heading2')
        status=report['provenance'].get('status','unknown')
        labels={'absent':'No signed history found. Many ordinary files do not have Content Credentials.',
                'trusted':'Signed history verified against the bundled trust list. This does not prove the content is true.',
                'valid_untrusted':'The signature passed integrity checks, but the signer is not trusted by the bundled list.',
                'invalid':'The signed history could not be verified. Check the original file before relying on these credentials.'}
        text(labels.get(status,'The signed history needs further review.'))
        text(report['provenance'].get('detail',''),'EvidenceSmall')
    for title,key in [('Your next steps','next_steps'),('What these checks cannot tell you','limitations')]:
        text(title,'Heading2')
        for item in report.get(key,[]): text('- '+item)
    text('Technical details','Heading2')
    text('The recorded analysis result is '+report['verdict']+'.','EvidenceSmall')
    for finding in report['findings']:
        text(finding['name']+': '+finding['detail']+(f" Measurement: {finding['value']}" if finding.get('value') is not None else ''),'EvidenceSmall')
    if report.get('model'):
        start=len(story)
        model=report['model']; text('Experimental model output','Heading3')
        text(f"{model['name']}: {model['manipulated_probability']*100:.1f}% manipulated-class output. This is not a verified probability that this content is fake.")
        text(model.get('scope','')); text(model.get('evaluation_status',model.get('note','')))
        if model.get('source'): text(model['source'],'EvidenceSmall')
        story[start:]=[KeepTogether(story[start:])]
    text('SHA-256: '+report['sha256'],'EvidenceSmall')
    text('Report ID: '+report.get('id',''),'EvidenceSmall')
    def footer(canvas,document):
        canvas.setStrokeColor(colors.HexColor('#d6e2e7')); canvas.line(42,34,553,34)
        canvas.setFont('Helvetica',8); canvas.setFillColor(colors.HexColor('#4d6275'))
        canvas.drawString(42,22,'TruthLens AI - evidence before certainty'); canvas.drawRightString(553,22,str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
