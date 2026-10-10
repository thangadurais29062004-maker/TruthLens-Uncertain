"""One-job decoder/model subprocess. Parent enforces cancellation and a wall-time limit."""
import json
import sys
from pathlib import Path

from truthlens.analysis import image_report, video_report, news_report


def main():
    directory = Path(sys.argv[1])
    task = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
    def progress(value, stage):
        target = directory / 'progress.json'
        temporary = directory / 'progress.next'
        temporary.write_text(json.dumps({'progress':value,'stage':stage}),encoding='utf-8')
        try:
            temporary.replace(target)
        except PermissionError:
            # Windows may briefly lock the progress file while the parent reads it.
            # A missed progress update must not fail the analysis itself.
            pass
    try:
        if task['kind'] == 'image':
            reference = (directory / 'reference.bin').read_bytes() if (directory / 'reference.bin').is_file() else None
            report = image_report((directory / 'input.bin').read_bytes(),task['name'],progress,reference)
        elif task['kind'] == 'video':
            report = video_report(directory / task['filename'],task['name'],task['digest'],progress)
        else:
            report = news_report(task['text'],task['name'],task.get('source_url',''),task.get('online',False),progress)
        result = {'ok':True,'report':report}
    except Exception as exc:
        from truthlens.analysis import AnalysisError
        result = {'ok':False,'error':str(exc) if isinstance(exc,AnalysisError) else 'Analysis failed. Check model configuration and server diagnostics.'}
        if not isinstance(exc,AnalysisError):
            import traceback
            traceback.print_exc()
    (directory / 'result.json').write_text(json.dumps(result,allow_nan=False),encoding='utf-8')


if __name__ == '__main__':
    main()
