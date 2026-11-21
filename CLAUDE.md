# DGX Spark ローカル動画生成環境 — Claude Code 作業指示

このディレクトリ（`~/ai-video`）で、DGX Spark 上にローカル完結の動画生成環境を構築する。
元仕様書は `SPEC.md`（ChatGPTとの相談の体系化、未検証の設計案）。以下は2026-11-18時点のレビュー結果で、**SPEC.md と食い違う場合はこちらを優先**する。

## 作業ルール
- 各ステップで「実行コマンド → 期待結果 → 実際の結果」を `logs/setup-log.md` に追記する。失敗時は原因と復旧手順も残す。
- 公式リポジトリ・公式ドキュメントの現物を確認してから書く。記憶や SPEC.md の記述を未確認で実行しない。
- 動作確認は非露骨な一般映像のプロンプトで行う。
- 大きなダウンロード（数GB以上）、`docker system prune` 等の削除系、sudo が必要な操作は、実行前にユーザーへ確認する。
- ComfyUI のポートは必ず `127.0.0.1` にバインドする（Docker は UFW を素通りするため）。

## SPEC.md からの修正点（確認済み事項）
1. **HFリポジトリ名**: `Comfy-Org/Wan-2.2` は誤り。正しくは `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`、ファイルは `split_files/` 以下（例: `split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`）。
2. **ファイル一覧の取得**: `hf repo-files` に頼らず `python -c "from huggingface_hub import HfApi; print(*HfApi().list_repo_files('Comfy-Org/Wan_2.2_ComfyUI_Repackaged'), sep='\n')"` を使う。
3. **Playbook**: 現行ドキュメントでは `dgx-spark-playbooks/nvidia/playbook-comfyui` の `assets/Dockerfile`。過去に `nvidia/comfy-ui` だった時期があるので、clone 後に実在を確認。コンテナ内パスは `/opt/ComfyUI`、公式の起動ログは `To see the GUI go to: http://0.0.0.0:8188`。
4. **メモリ**: ComfyUI on Spark で safetensors 読み込み時に二重常駐し、実質64GB程度しか使えないという報告あり（ComfyUI issue #10896）。128GB前提で見積もらず、ピークメモリを毎回実測・記録する。
5. **5Bのテスト条件**: TI2V-5B は 720p前後・24fps が想定（14B系が16fps）。最初は公式テンプレートのデフォルト値で1本生成し、そこから解像度・フレームを下げて比較する。フレーム数は 4n+1。
6. **LTX-2.5**: 2026年8月にオープンウェイト公開、ComfyUI ネイティブ対応、DGX Spark 向け最適化あり。Wan 2.2 と並行比較の対象に格上げ。ライセンスは LTX-2 Community License（売上1,000万ドル未満は無償）— Acceptable Use 条項を原文で確認し `docs/licenses.md` に要点を記録。

## 追加要件
- **再現性**: ComfyUI 本体・カスタムノード・ベースイメージを commit hash / digest で固定し、`versions.lock` に記録。
- **メタデータ削除**: ComfyUI は出力にワークフロー/プロンプトを埋め込む。外部へ出す前に `ffmpeg -i in.mp4 -map_metadata -1 -c copy out.mp4` 相当で除去するスクリプトを用意。
- **オフライン**: 初回取得後は `HF_HUB_OFFLINE=1`、ComfyUI-Manager の自動更新/取得を無効化。受入時に実際にネットワークを切って再生成を確認。
- **逐次実行**: Ollama は `keep_alive: 0` でアンロード、ComfyUI は `POST /free`（`{"unload_models": true, "free_memory": true}`）で解放してから次工程へ。
- **速度**: 14B は lightx2v 4ステップ LoRA（同HFリポジトリの `split_files/loras/`）を早期に検証。
- **モデル資産**: safetensors のみ。取得時にサイズと SHA256 を `models/MANIFEST.md` に記録。

## 進め方（この順で、各段階でユーザーに報告して区切る）
1. 事前確認: `uname -m`, `/etc/os-release`, `nvidia-smi`, `docker --version`, `docker compose version`, `docker run --rm --gpus all <CUDAベース> nvidia-smi`, `df -h`, `free -g`
2. 公式 Playbook を clone し、README/Dockerfile の現物を確認 → 無改変でビルド・起動して UI 表示を確認
3. 5B 一式をダウンロード → 公式テンプレート（Comfy-Org/workflow_templates の `video_wan2_2_5B_ti2v.json`）で一般映像を1本生成。時間・ピークメモリ・設定を記録
4. 127.0.0.1 バインド、バージョン固定、オフライン化を反映した `Dockerfile` / `docker-compose.yml` / スクリプト群に置き換え
5. 14B I2V（fp8_scaled + 4ステップ LoRA）と LTX-2.5 を同条件で比較
6. （後続）FastAPI で Ollama → ComfyUI → FFmpeg の自動化

## 最終成果物
README.md、Dockerfile、docker-compose.yml、scripts/（preflight / download-models / start / test-generate / collect-logs / stop / strip-metadata）、workflows/（実際にインポートできるJSON）、versions.lock、models/MANIFEST.md、docs/troubleshooting.md、docs/licenses.md、logs/setup-log.md。未確認事項は推測で断定せず「未確認・確認方法」を明記。
