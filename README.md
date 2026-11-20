# my-voice-gen-experiment

自分の声を録音して、ローカルの音声合成モデルにまねさせ、本人の声と聴き比べる実験の一式。

- 声のクローン: [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 1.7B Base（Apache 2.0）を
  [audio.cpp](https://huggingface.co/audio-cpp/audio.cpp-gguf) の GGUF（Q8_0）で動かす
- 書き起こし: Whisper large-v3-turbo
- 話者の近さ: WavLM の話者埋め込み（[microsoft/wavlm-base-plus-sv](https://huggingface.co/microsoft/wavlm-base-plus-sv)）

**録音も生成した声も、手元とローカルのサーバから出ない。** クラウドの音声 API は使わない。

結果と聴き比べは記事にまとめた: [ローカルLLMに自分の声をまねさせてみた（Qwen3-TTS）](https://notes.ideamans.com/posts/2026/clone-my-voice-qwen3-tts.html)。
記事に載せた音声（本人と生成を 2 組）は `samples/` にある。

> 自分の声か、本人の同意を得た声だけで試してください。生成した声で他人になりすますことはしないでください。

## 何を比べるか

参照音声（声をまねさせる素材）の **長さ** と **渡し方** を変え、同じ原稿を読ませる。

| 条件 | 中身 |
|---|---|
| `R03-icl` / `R10-icl` / `R30-icl` | 約 3・10・30 秒の参照音声と、その書き起こしを渡す（ICL。話し方まで引き継ぐ） |
| `R03-xvec` / `R10-xvec` / `R30-xvec` | 参照音声から話者の特徴（x-vector）だけを取る。書き起こしは要らない |
| `preset` | 別人の声（Qwen3-TTS CustomVoice のプリセット話者）。「似ている」の下限の目安 |

比較用の原稿は 1 本 15〜20 秒の 6 本（挨拶・数字・疑問・カタカナ語・感情・締め）。`script.md`

| 測るもの | 方法 |
|---|---|
| 話者の近さ | 同じ原稿を読んだ本人の録音との、WavLM の話者埋め込みのコサイン類似度。「本人どうし（別の原稿）」を上限、`preset` を下限の目安にする |
| 読みの正確さ | Whisper で書き起こし、原稿との文字誤り率（CER）。本人の録音も同じに測る |
| 話す速さ | 前後の無音を除いた長さの、本人に対する比 |
| 生成の速さ | 生成にかかった時間 ÷ 音声の長さ（1 未満なら実時間より速い） |

数字のほかに、本人と生成を並べて聴けるページ（どちらが本人かを当てる目隠しテスト付き）を作る。

## 用意するもの

- macOS か Linux。`ffmpeg`、Python 3.11 以上、[uv](https://docs.astral.sh/uv/)（比較の計算だけで使う）
- OpenAI 互換の音声 API を持つサーバ（GPU があれば速い）
  - 音声合成: audio.cpp の `audiocpp_server`。設定例は `server/audiocpp.json`、モデルは
    `audio-cpp/audio.cpp-gguf` の `Qwen3-TTS-12Hz-1.7B-Base-GGUF/qwen3-tts-12hz-1.7b-base-q8_0_v2.gguf`
    （比較用の別人の声を作るなら `Qwen3-TTS-12Hz-1.7B-CustomVoice-GGUF` も）
  - 書き起こし: `/v1/audio/transcriptions` を持つもの（whisper.cpp の `whisper-server` など）
- 接続先を `.env` に書く（`env.example` をコピー）

```bash
cp env.example .env   # VOICE_API_BASE などを書き換える
```

### audio.cpp の参照音声の渡し方（はまったところ）

audio.cpp の `/v1/audio/speech` は、参照音声を **サーバ上のファイルのパス**（`voice_ref`）で受け取る。
Base64 で中身を送っても通らない。手元からは次の 2 段で渡す（`common.py` の `upload()`）。

1. WebUI 用のアップロード口 `POST /v1/ui/upload` に **WAV の生のバイト列**を送る（multipart ではない）。
   返ってくる `{"path": "/tmp/audiocpp-ui-…/1-audio.wav"}` がサーバ上のパス
2. `/v1/audio/speech` に `"voice_ref": <そのパス>` を付ける。ICL なら `"reference_text"`、
   x-vector なら `"options": {"x_vector_only_mode": true}`

[llama-swap](https://github.com/mostlygeek/llama-swap) 越しなら、アップロード口は
`/upstream/<モデル名>/v1/ui/upload`（`UPLOAD_URL` で指定）。モデルが載せ替えられると置いたファイルは
消えるので、生成の直前に毎回送り直している。

## 手順

### 1. 録音する

`script.md` の原稿を、ボイスメモなどのアプリで **1 本に通して** 読む。

- 項目の間は 3 秒ほど黙る（そこで切り分ける）
- 読み間違えたら、3 秒あけてその項目を頭から読み直す（後に読んだほうを採る）
- iPhone のボイスメモなら、音質を「ロスレス」にしておく

### 2. 取り込む

```bash
python3 import_recordings.py ~/Downloads/新規録音.m4a
```

無音で切り分け、Whisper の書き起こしでどの原稿かを当てて `recordings/<ID>.wav` に置く。
足りない項目は、その項目だけ録って同じように渡せば足される。

### 3. 参照音声を作る

```bash
python3 make_refs.py
```

`recordings/R*.wav` の前後の無音を削って `refs/` に置く。原稿どおりに読めているかを Whisper で点検する。
ずれていたら、録り直すか `--use-asr`（書き起こしを使う）。

### 4. 生成する

```bash
python3 generate.py                 # 7 条件 × 6 原稿 = 42 本
python3 generate.py --only R10-icl  # 条件を絞る
```

### 5. 比べる

```bash
uv run compare.py
open report/index.html
```

数字は `results.md`（声を含まないのでコミットしてよい）、原稿ごとの値は `report/results.json`。

## 公開しないもの

`recordings/`・`refs/`・`generated/`・`report/` と `.env` は `.gitignore` で外している。
録音は本人の声（生体情報）で、生成物はその声をまねたものだから。
記事などで聴かせたいものは、選んで `samples/` に置く。

## 動かした環境

- サーバ: GTX 1080 Ti（11GB・Pascal）、audio.cpp（2026-11-04 版）、llama-swap
- Qwen3-TTS 1.7B Base Q8_0 は、15〜20 秒の音声をおよそ 8 秒で作る（実時間の半分ほど）。
  ICL は参照が長いほど遅く、35 秒の参照では 4.6 秒の音声に 20 秒かかった

## ライセンス

MIT（コード）。モデルはそれぞれのライセンスに従う（Qwen3-TTS: Apache 2.0）。
