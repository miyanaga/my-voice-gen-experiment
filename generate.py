#!/usr/bin/env python3
"""参照音声から、比較用の原稿（tests）を読み上げた音声を作る。

  python3 generate.py              # すべての条件 × すべての原稿（作ってあるものは飛ばす）
  python3 generate.py --force      # 作り直す
  python3 generate.py --only R10-icl preset

条件:
  <参照>-icl   参照音声と書き起こしを渡す（Qwen3-TTS の既定。声に加えて話し方も引き継ぐ）
  <参照>-xvec  参照音声から話者の特徴（x-vector）だけを取る。書き起こしは要らない
  preset       別人の声（CustomVoice のプリセット話者）。「似ている」の目盛りを合わせるための下限

出力は generated/<条件>/<原稿>.wav と generated/timing.json（生成にかかった秒数）。
"""
import argparse
import json
import time

from common import PRESET_MODEL, PRESET_VOICE, TTS_MODEL, WORK, duration, post_json, script, upload


def conditions():
    out = []
    for r in script()['refs']:
        ref = WORK / 'refs' / f"{r['id']}.wav"
        if not ref.exists():
            continue
        text = (WORK / 'refs' / f"{r['id']}.txt").read_text().strip()
        out.append((f"{r['id']}-icl", ref, {'model': TTS_MODEL, 'reference_text': text}))
        out.append((f"{r['id']}-xvec", ref, {'model': TTS_MODEL, 'options': {'x_vector_only_mode': True}}))
    if PRESET_MODEL:
        out.append(('preset', None, {'model': PRESET_MODEL, 'voice': PRESET_VOICE}))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--only', nargs='*', help='この条件だけ')
    a = ap.parse_args()
    tests = script()['tests']
    conds = [c for c in conditions() if not a.only or c[0] in a.only]
    if not conds:
        raise SystemExit('参照音声がない。先に python3 make_refs.py')
    tpath = WORK / 'generated' / 'timing.json'
    timing = json.loads(tpath.read_text()) if tpath.exists() else {}
    for name, ref, body in conds:
        d = WORK / 'generated' / name
        d.mkdir(parents=True, exist_ok=True)
        for t in tests:
            dst = d / f"{t['id']}.wav"
            if dst.exists() and not a.force:
                continue
            req = {**body, 'input': t['text'], 'response_format': 'wav'}
            if ref:
                req['voice_ref'] = upload(ref)
            st = time.time()
            dst.write_bytes(post_json('/audio/speech', req))
            el = time.time() - st
            timing[f'{name}/{t["id"]}'] = round(el, 2)
            print(f'{name:9} {t["id"]}  {duration(dst):5.1f} 秒の音声を {el:5.1f} 秒で生成')
            tpath.write_text(json.dumps(timing, ensure_ascii=False, indent=1))
    print('\n次は uv run compare.py')


if __name__ == '__main__':
    main()
