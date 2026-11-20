#!/usr/bin/env python3
"""recordings/R*.wav から、声のクローンに渡す参照音声 refs/<id>.wav と書き起こし refs/<id>.txt を作る。

  python3 make_refs.py            # 原稿どおりに読めていれば、原稿の文を書き起こしとして使う
  python3 make_refs.py --use-asr  # 読み間違いがあっても録り直さず、Whisper の書き起こしを使う

ICL 方式（参照音声＋書き起こし）は、書き起こしが実際の発話とずれていると崩れる。
原稿と Whisper の結果がずれていたら知らせるので、録り直すか --use-asr にする。
"""
import argparse
import tempfile
from pathlib import Path

from common import WORK, cer, duration, script, transcribe, trim

WARN_CER = 0.08   # Whisper 自体の取り違えもあるので、少しのずれは許す


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--use-asr', action='store_true', help='書き起こしに Whisper の結果を使う')
    a = ap.parse_args()
    out = WORK / 'refs'
    out.mkdir(exist_ok=True)
    bad = 0
    for r in script()['refs']:
        src = WORK / 'recordings' / f"{r['id']}.wav"
        if not src.exists():
            print(f"{r['id']}: 録音がない（{src}）")
            bad += 1
            continue
        dst = out / f"{r['id']}.wav"
        trim(src, dst)   # 24kHz モノラル。前後の無音を削る
        with tempfile.TemporaryDirectory() as t:
            small = Path(t) / 'a.wav'
            trim(src, small, rate=16000)
            asr = transcribe(small)
        c = cer(r['text'], asr)
        text = asr if a.use_asr else r['text']
        (out / f"{r['id']}.txt").write_text(text + '\n')
        d = duration(dst)
        mark = ''
        if c > WARN_CER and not a.use_asr:
            mark = '  ⚠ 原稿とずれている。録り直すか --use-asr'
            bad += 1
        print(f"{r['id']}: {d:5.1f} 秒（目安 {r['target_sec']} 秒）  原稿とのずれ {c:.0%}{mark}")
        if c > 0:
            print(f'       Whisper: {asr}')
    if bad:
        raise SystemExit(1)
    print('\n次は python3 generate.py')


if __name__ == '__main__':
    main()
