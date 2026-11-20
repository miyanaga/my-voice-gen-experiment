# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["torch", "transformers", "numpy"]
# ///
"""本人の録音と生成した音声を比べ、数字と聴き比べのページを作る。

  uv run compare.py           # report/index.html と results.md を作る

測るもの:
  話者の近さ   WavLM（microsoft/wavlm-base-plus-sv）の話者埋め込みのコサイン類似度。
               同じ原稿を読んだ本人の録音と比べる。目盛りは「本人どうし（別の原稿）」が上限の目安、
               「preset（別人）」が下限の目安
  読みの正確さ Whisper で書き起こし、原稿との文字誤り率（CER）。本人の録音も同じように測る
  話す速さ     前後の無音を除いた長さの、本人に対する比（1.00 なら同じ速さ）

話者埋め込みはこの Mac の上で計算する（モデルの重みだけを Hugging Face から取る）。
"""
import html
import json
import random
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import torch
from transformers import AutoFeatureExtractor, WavLMForXVector

from common import WORK, cer, duration, script, transcribe, trim

SV_MODEL = 'microsoft/wavlm-base-plus-sv'
REPORT = WORK / 'report'


def load16k(path):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(path), '-ac', '1', '-ar', '16000',
                        '-f', 'f32le', '-'], capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


class Embedder:
    def __init__(self):
        self.fe = AutoFeatureExtractor.from_pretrained(SV_MODEL)
        self.m = WavLMForXVector.from_pretrained(SV_MODEL).eval()

    def __call__(self, wav):
        x = self.fe(wav, sampling_rate=16000, return_tensors='pt')
        with torch.no_grad():
            e = self.m(**x).embeddings[0]
        return torch.nn.functional.normalize(e, dim=-1).numpy()


def listen_copy(src, dst):
    """聴き比べ用。音量をそろえて（-20 LUFS）m4a にする。数字の計算には使わない。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src),
                    '-af', 'loudnorm=I=-20:TP=-2:LRA=11', '-ar', '48000', '-ac', '1', '-c:a', 'aac', '-b:a', '128k',
                    str(dst)], check=True)


def main():
    s = script()
    tests = s['tests']
    conds = sorted(p.name for p in (WORK / 'generated').iterdir() if p.is_dir())
    conds = [c for c in conds if c != 'preset'] + (['preset'] if 'preset' in conds else [])
    emb = Embedder()
    tmp = Path(tempfile.mkdtemp())
    rows = {}   # (cond, test) -> dict

    def measure(cond, tid, src, text):
        small = tmp / f'{cond}-{tid}.wav'
        trim(src, small, rate=16000)
        rel = Path('audio') / cond / f'{tid}.m4a'
        listen_copy(small, REPORT / rel)
        hyp = transcribe(small)
        return {'emb': emb(load16k(small)), 'dur': duration(small), 'cer': cer(text, hyp), 'asr': hyp,
                'audio': str(rel)}

    for t in tests:
        src = WORK / 'recordings' / f"{t['id']}.wav"
        if not src.exists():
            raise SystemExit(f'本人の録音がない: {src}')
        rows[('real', t['id'])] = measure('real', t['id'], src, t['text'])
        print('real', t['id'])
    for c in conds:
        for t in tests:
            src = WORK / 'generated' / c / f"{t['id']}.wav"
            if src.exists():
                rows[(c, t['id'])] = measure(c, t['id'], src, t['text'])
                print(c, t['id'])
    for (c, tid), r in rows.items():
        real = rows[('real', tid)]
        r['sim'] = float(np.dot(r['emb'], real['emb'])) if c != 'real' else None
        r['speed'] = real['dur'] / r['dur'] if c != 'real' else 1.0
    ids = [t['id'] for t in tests]
    pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]
    ceiling = float(np.mean([np.dot(rows[('real', a)]['emb'], rows[('real', b)]['emb']) for a, b in pairs]))

    timing = {}
    tp = WORK / 'generated' / 'timing.json'
    if tp.exists():
        timing = json.loads(tp.read_text())

    def avg(c, k):
        v = [rows[(c, t)][k] for t in ids if (c, t) in rows and rows[(c, t)][k] is not None]
        return sum(v) / len(v) if v else float('nan')

    summary = []
    for c in ['real'] + conds:
        gen = [timing[f'{c}/{t}'] for t in ids if f'{c}/{t}' in timing]
        aud = [rows[(c, t)]['dur'] for t in ids if (c, t) in rows]
        rtf = sum(gen) / sum(aud) if gen and aud else None
        summary.append({'cond': c, 'sim': ceiling if c == 'real' else avg(c, 'sim'), 'cer': avg(c, 'cer'),
                        'speed': avg(c, 'speed'), 'rtf': rtf})

    label = {'real': '本人（録音）', 'preset': '別人（プリセット話者）'}
    for r in s['refs']:
        label[f"{r['id']}-icl"] = f"参照 {r['target_sec']} 秒・ICL"
        label[f"{r['id']}-xvec"] = f"参照 {r['target_sec']} 秒・x-vector"

    # results.md（数字だけ。声は含まないのでコミットしてよい）
    md = ['# 結果', '', '| 条件 | 話者の近さ | 文字誤り率 | 話す速さ（本人=1.00） | 生成時間 ÷ 音声の長さ |',
          '|---|---|---|---|---|']
    for x in summary:
        sim = f"{x['sim']:.3f}" + ('（本人どうし・別の原稿）' if x['cond'] == 'real' else '')
        rtf = f"{x['rtf']:.2f}" if x['rtf'] else '—'
        md.append(f"| {label.get(x['cond'], x['cond'])} | {sim} | {x['cer']:.1%} | {x['speed']:.2f} | {rtf} |")
    md += ['', '話者の近さは同じ原稿を読んだ本人の録音とのコサイン類似度（WavLM）。原稿ごとの値は report/results.json。', '']
    (WORK / 'results.md').write_text('\n'.join(md))

    out = {'ceiling': ceiling, 'summary': summary,
           'rows': [{'cond': c, 'test': t, **{k: v for k, v in r.items() if k != 'emb'}} for (c, t), r in rows.items()]}
    REPORT.mkdir(exist_ok=True)
    (REPORT / 'results.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))

    # report/index.html
    best = max((x for x in summary if x['cond'] not in ('real', 'preset')), key=lambda x: x['sim'])['cond']
    e = html.escape
    h = ['<!doctype html><meta charset="utf-8"><title>声の再現の比較</title>',
         '<style>body{font:15px/1.7 system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em}'
         'table{border-collapse:collapse;margin:1em 0}td,th{border:1px solid #ccc;padding:4px 8px;vertical-align:top}'
         'audio{height:32px;width:240px}.q{display:flex;gap:2em;align-items:center}.ans{display:none;font-weight:bold}'
         '.small{color:#666;font-size:13px}</style>',
         '<h1>声の再現の比較</h1>', '<h2>まとめ</h2>', '<table><tr><th>条件<th>話者の近さ<th>文字誤り率<th>話す速さ<th>生成時間 ÷ 長さ']
    for x in summary:
        rtf = f"{x['rtf']:.2f}" if x['rtf'] else '—'
        h.append(f"<tr><td>{e(label.get(x['cond'], x['cond']))}<td>{x['sim']:.3f}<td>{x['cer']:.1%}"
                 f"<td>{x['speed']:.2f}<td>{rtf}")
    h.append('</table><p class="small">話者の近さ: 本人の行は「本人どうし（別の原稿）」の平均で、上限の目安。'
             '別人の行が下限の目安。</p>')
    h.append(f'<h2>目隠しテスト（{e(label.get(best, best))}）</h2><p>どちらが本人か当ててください。</p>')
    rnd = random.Random(0)
    for t in tests:
        if (best, t['id']) not in rows:
            continue
        pair = [('本人', rows[('real', t['id'])]['audio']), ('生成', rows[(best, t['id'])]['audio'])]
        rnd.shuffle(pair)
        h.append(f"<p>{e(t['id'])}　{e(t['text'][:30])}…</p><div class=q>"
                 f"A <audio controls preload=none src='{pair[0][1]}'></audio>"
                 f"B <audio controls preload=none src='{pair[1][1]}'></audio>"
                 f"<button onclick=\"this.nextElementSibling.style.display='inline'\">答え</button>"
                 f"<span class=ans>A = {pair[0][0]} / B = {pair[1][0]}</span></div>")
    h.append('<h2>原稿ごと</h2>')
    for t in tests:
        h.append(f"<h3>{e(t['id'])}（{e(t['kind'])}）</h3><p>{e(t['text'])}</p>"
                 '<table><tr><th>条件<th>音声<th>話者の近さ<th>誤り率<th>速さ<th>Whisper の書き起こし')
        for c in ['real'] + conds:
            r = rows.get((c, t['id']))
            if not r:
                continue
            sim = '—' if r['sim'] is None else f"{r['sim']:.3f}"
            h.append(f"<tr><td>{e(label.get(c, c))}<td><audio controls preload=none src='{r['audio']}'></audio>"
                     f"<td>{sim}<td>{r['cer']:.1%}<td>{r['speed']:.2f}<td class=small>{e(r['asr'])}")
        h.append('</table>')
    (REPORT / 'index.html').write_text('\n'.join(h))
    print('\n' + '\n'.join(md))
    print(f'\n聴き比べ: open {REPORT / "index.html"}')


if __name__ == '__main__':
    main()
