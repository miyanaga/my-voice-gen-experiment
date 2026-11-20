# CLAUDE.md

自分の声を録音し、Qwen3-TTS Base（audio.cpp）でまねさせて本人と比べる実験。**公開リポジトリ。**
手順・測り方は README.md が正。

## 守ること

- **社内のホスト名・接続先をコードやドキュメントに書かない。** `.env`（コミットしない）にだけ置く。
  既定値は `http://localhost:8080/v1`
- **声を含むものをコミットしない。** `recordings/` `refs/` `generated/` `report/` は .gitignore 済み。
  公開してよいと本人が決めたものだけを `samples/` に置く。`results.md`（数字だけ）はコミットしてよい
- スクリプトは標準ライブラリと ffmpeg だけで動かす。重い依存（torch など）は `compare.py` の
  PEP 723 の宣言に閉じ、`uv run compare.py` で入れる

## はまりどころ

- audio.cpp の `voice_ref` は**サーバ上のパス**。Base64 は通らない。`/v1/ui/upload` に WAV の生のバイト列を
  送ってパスをもらう（multipart で送ると壊れたファイルが置かれ、`invalid WAV RIFF header` になる）
- x-vector 方式は `"options": {"x_vector_only_mode": true}`。トップレベルに置くと無視されて ICL 扱いになり、
  `reference_text` が無いと怒られる
- llama-swap 越しのアップロードは `/upstream/<モデル名>/v1/ui/upload`。モデルが載せ替えられると
  置いたファイルは消えるので、生成の直前に毎回送る（`common.upload()`）
- ICL 方式は書き起こしが実際の発話とずれると崩れる。`make_refs.py` が Whisper で点検する
- 原稿（`script.json`）の参照用と比較用の文は重ねない。重ねると「まね」ではなく丸写しを測ることになる
- `script.md` は `script.json` から作った読み上げ用の原稿。原稿を変えたら両方を直す
