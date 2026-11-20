"""各スクリプトが使う共通部品（Python の標準ライブラリと ffmpeg だけ）。

接続先は環境変数か .env で指定する（.env はコミットしない。書き方は .env.example）。
"""
import json
import os
import re
import subprocess
import unicodedata
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_env():
    p = HERE / '.env'
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


_load_env()
API_BASE = os.environ.get('VOICE_API_BASE', 'http://localhost:8080/v1').rstrip('/')
ASR_BASE = os.environ.get('ASR_API_BASE', API_BASE).rstrip('/')
TTS_MODEL = os.environ.get('TTS_MODEL', 'qwen3-tts-base')
ASR_MODEL = os.environ.get('ASR_MODEL', 'whisper-large-v3-turbo')
# 比較用の「別人の声」（Qwen3-TTS CustomVoice のプリセット話者）。空なら作らない
PRESET_MODEL = os.environ.get('PRESET_MODEL', 'qwen3-tts')
PRESET_VOICE = os.environ.get('PRESET_VOICE', 'Ono_Anna')
# 参照音声を置く口。audio.cpp は voice_ref に「サーバ上のパス」を取るので、先に WebUI 用の
# /v1/ui/upload へ送ってパスをもらう。llama-swap 越しなら /upstream/<モデル名> を付ける
UPLOAD_URL = os.environ.get('UPLOAD_URL', API_BASE.removesuffix('/v1') + '/v1/ui/upload')
# 録音・生成物の置き場（既定はこのフォルダ。模擬データで試すときに差し替える）
WORK = Path(os.environ.get('WORK_DIR', HERE)).expanduser()


def script():
    return json.loads((HERE / 'script.json').read_text())


def trim(src, dst, rate=24000):
    """前後の無音を削り、0.1 秒の余白を付けてモノラル・rate Hz の WAV にする。"""
    sr = 'silenceremove=start_periods=1:start_silence=0.1:start_threshold=-45dB:detection=peak'
    af = f'{sr},areverse,{sr},areverse,apad=pad_dur=0.1'
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-af', af,
                    '-ac', '1', '-ar', str(rate), '-c:a', 'pcm_s16le', str(dst)], check=True)


def duration(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def post_json(path, body, base=None, timeout=900):
    req = urllib.request.Request((base or API_BASE) + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{e.code} {path}: {e.read().decode(errors="replace")[:500]}')


def transcribe(path):
    """OpenAI 互換の /audio/transcriptions で日本語の文字起こし。"""
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in [('model', ASR_MODEL), ('language', 'ja'), ('response_format', 'json')]]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + Path(path).read_bytes() + b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    req = urllib.request.Request(ASR_BASE + '/audio/transcriptions', data=b''.join(parts),
                                 headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())['text'].strip()


def upload(path):
    """参照音声をサーバへ置き、voice_ref に渡すパスを返す。

    置いたファイルはサーバのプロセスの一時フォルダにある。llama-swap がモデルを載せ替えると
    消えるので、生成の直前に毎回置き直す。"""
    req = urllib.request.Request(UPLOAD_URL, data=Path(path).read_bytes(), headers={'Content-Type': 'audio/wav'})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())['path']


def norm(s):
    """比較用。NFKC にして句読点・記号・空白を落とす。"""
    s = unicodedata.normalize('NFKC', s)
    return re.sub(r'[\s、。,.!?！？「」『』・…ー\-]', '', s)


def cer(ref, hyp):
    """文字誤り率（編集距離 ÷ 正解の文字数）。"""
    a, b = norm(ref), norm(hyp)
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / len(a)
