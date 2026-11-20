#!/usr/bin/env python3
"""ボイスメモ等で録った音声を、原稿ごとの recordings/<id>.wav に切り分ける。

  python3 import_recordings.py ~/Downloads/新規録音.m4a [さらにファイル …]
  python3 import_recordings.py 録音.m4a --gap 2.0      # 切れ目とみなす無音の長さ（既定 2.0 秒）

項目の間の長い無音で切り、Whisper で文字起こしして、どの原稿に一番近いかで振り分ける。
同じ原稿が複数あれば後に読んだほうを採る（言い直しを想定）。
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from common import WORK, cer, duration, script, transcribe, trim

OUT = WORK / 'recordings'
MATCH_MAX_CER = 0.5   # これより遠いものは「どの原稿でもない」（雑談・咳払いなど）


def silences(path, gap, thresh):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(path), '-af',
                        f'silencedetect=noise={thresh}dB:d={gap}', '-f', 'null', '-'],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r'silence_start: ([\d.]+)', r.stderr)]
    ends = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', r.stderr)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def chunks(path, gap, thresh):
    total = duration(path)
    t, out = 0.0, []
    for s, e in silences(path, gap, thresh):
        if s - t > 1.0:
            out.append((t, s))
        t = e if e is not None else total
    if total - t > 1.0:
        out.append((t, total))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--gap', type=float, default=2.0, help='切れ目とみなす無音の秒数')
    ap.add_argument('--thresh', type=float, default=-40, help='無音とみなす音量（dB）')
    a = ap.parse_args()
    s = script()
    items = s['refs'] + s['tests']
    got = {}
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        n = 0
        for f in a.files:
            f = Path(f).expanduser()
            for st, en in chunks(f, a.gap, a.thresh):
                n += 1
                raw = tmp / f'c{n:03d}.wav'
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(f),
                                '-ss', f'{max(0, st - 0.2):.2f}', '-to', f'{en + 0.2:.2f}',
                                '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(raw)], check=True)
                small = tmp / f'c{n:03d}-16k.wav'
                trim(raw, small, rate=16000)
                text = transcribe(small)
                best = min(items, key=lambda i: cer(i['text'], text))
                c = cer(best['text'], text)
                tag = best['id'] if c <= MATCH_MAX_CER else '—'
                print(f'{f.name} {st:7.1f}〜{en:7.1f}秒 → {tag:4} (誤り {c:.0%}) {text[:40].replace(chr(10), " ")}')
                if tag != '—':
                    got[tag] = raw.read_bytes()   # 後に読んだほうで上書き
    for k, v in got.items():
        (OUT / f'{k}.wav').write_bytes(v)
    missing = [i['id'] for i in items if not (OUT / f"{i['id']}.wav").exists()]
    print(f'\n取り込んだ: {", ".join(sorted(got)) or "なし"}')
    if missing:
        print(f'足りない: {", ".join(missing)}（その項目だけ録って、このスクリプトにもう一度渡せばよい）')
        sys.exit(1)
    print('全部そろった。次は python3 make_refs.py')


if __name__ == '__main__':
    main()
