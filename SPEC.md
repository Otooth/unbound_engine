# DGX Spark ローカルAI動画生成環境 — Claude引継ぎ・実装仕様書

**作成日:** 2026年11月18日  
**目的:** これまでのChatGPTとの相談内容をClaudeに引き継ぎ、DGX Spark上のローカル動画生成環境の構築・検証・改善を依頼する。  
**ステータス:** 設計案。コマンド・パス・互換性・モデル配布状況は実機で未検証。

## 1. Claudeに最初に伝える依頼文

あなたはNVIDIA DGX Spark、ARM64/aarch64、CUDA、Docker、ComfyUI、Wan 2.2、Ollama、FastAPIの実装に詳しいエンジニアです。以下の要件を満たす、再現可能なローカルAI動画生成環境を設計・実装してください。最初に公式リポジトリの現行状態とファイル名・ディレクトリ・DockerイメージのENTRYPOINTを確認し、古い情報や未検証の記述をそのまま実行しないでください。実行する各ステップに確認コマンド・期待結果・失敗時の復旧方法を付け、動作確認は非露骨な一般動画で行ってください。

## 2. 用途・優先事項

- 用途: 成人向け・性的表現を含む映像制作を想定した、ローカル動画生成基盤。
- 最優先: 表現の自由度、クラウド側の一律審査・API依存を避けること、データを自分で管理できること。
- 次点: 品質、人物と背景の一貫性、構築の再現性、処理速度。
- ローカル完結: モデル取得や初回セットアップ後はネット接続なしで推論できる構成。
- 境界: 技術的な動画生成環境の構築と一般的な映像でのテストを対象とする。同意のない実在人物の性的ディープフェイク、未成年の性的描写等を目的としない。
- 注意: 「無検閲LLM」を使っても動画モデルの生成挙動が変わるわけではない。ローカル実行・自前のワークフロー・プロンプト入力経路の管理が重要。

## 3. 所有ハードウェアと前提

- 機材: NVIDIA DGX Spark、GB10、128GB統合メモリ。
- OS: DGX OS / Ubuntu系を想定（実機確認）。CPUアーキテクチャはARM64/aarch64。
- 既存AI環境: OllamaとQwen系ローカルLLMの使用経験あり。
- 追加GPU購入は当面避ける。まずDGX Spark単体で検証する。
- 同時にLLMと動画モデルを動かさず、LLMで脚本・プロンプト生成 → LLMアンロード → 動画生成の逐次実行を基本とする。
- ストレージ: 目安として200GB以上の空き領域を確保。モデル数と出力動画に応じ拡張。

## 4. モデル選定

| 位置付け | モデル | 役割 | 注意点 |
|---|---|---|---|
| 第一段階 | Wan 2.2 TI2V-5B | Text-to-Video / Image-to-Video初期動作検証 | まずFP16、低解像度・短尺でテスト |
| 第二段階 | Wan 2.2 I2V-A14B | 参照画像からの高画質動画 | high-noise / low-noiseの2ファイル、FP8を検討 |
| 追加 | Wan 2.2 T2V-A14B | 高画質テキスト動画 | メモリと速度を検証 |
| 追加候補 | LTX-2.5 | 音声付き動画など | ライセンス、対応GPU/ARM64、ComfyUI連携を事前確認 |
| 補助 | Ollama / Qwen | 台本、ショット分割、プロンプト生成 | 動画推論と逐次実行 |

※これは選定案。2026年11月時点の配布状況、ライセンス、対応バージョンはClaude側で公式情報を再検証すること。

## 5. 推奨アーキテクチャ

1. ローカルWeb UI：ComfyUI（ブラウザ操作、ワークフロー実行）。
2. 動画生成エンジン：Wan 2.2 5B → 14B。一般動画で動作確認。
3. テキスト処理：Ollama / Qwenで脚本・ショットリスト・英文プロンプトを作成。
4. 制御API：必要になった段階でFastAPIを追加し、OllamaとComfyUIのローカルAPIを連携。
5. 動画仕上げ：FFmpegで動画結合、字幕・音声等の追加、MP4書き出し。
6. 保存：入力画像、モデル、ワークフロー、生成動画、実行ログをローカル保存。
7. セキュリティ：ComfyUIポートは127.0.0.1にバインドし、外部PCからはSSHトンネル利用。外部APIノードは無効化・不使用。

## 6. 事前確認コマンド

```bash
uname -m
nvidia-smi
docker --version
docker info | grep -i runtime
df -h /
```

期待値: ARM64/aarch64、GPUの認識、NVIDIA Container ToolkitとDockerの正常動作、十分なストレージ。docker composeの有無も確認。

## 7. ComfyUIの取得・ビルド候補

NVIDIA公式DGX Spark Playbooksを出発点とする。

```bash
mkdir -p ~/ai-video
cd ~/ai-video
git clone https://github.com/NVIDIA/dgx-spark-playbooks.git
cd dgx-spark-playbooks/nvidia/playbook-comfyui
# 以下は候補。公式READMEとDockerfileの実在・パス・ビルドコンテキストを必ず確認
# docker build -t dgx-comfyui:wan22 -f assets/Dockerfile .
```

**重要な未検証点:** Dockerfileや作業ディレクトリ、コンテナ内のComfyUIパス、ENTRYPOINT/CMD、起動時の引数、拡張のサポートはリポジトリ更新で変わりうる。公式READMEに沿って確認・修正する。ARM64対応PyTorch/CUDAを維持する。

## 8. モデルファイル構成（配置案）

ComfyUIの`models`以下に、配布元で確認した正確なファイル名で格納する。

```text
models/
├── diffusion_models/
│   ├── wan2.2_ti2v_5B_fp16.safetensors
│   ├── wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors
│   └── wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors
├── text_encoders/
│   └── umt5_xxl_fp8_e4m3fn_scaled.safetensors
└── vae/
    ├── wan2.2_vae.safetensors
    └── wan_2.1_vae.safetensors
```

これらは以前の相談で挙げたファイル名であり、**ダウンロード前に配布元の実在ファイルを確認**すること。5Bと14BでVAEやワークフローが異なり、安易に混用しない。

公式配布元候補:
- https://github.com/Wan-Video/Wan2.2
- https://huggingface.co/Comfy-Org/Wan-2.2
- https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B
- https://github.com/Comfy-Org/workflow_templates
- https://github.com/Lightricks/ComfyUI-LTXVideo

## 9. Hugging Faceダウンロード手順（テンプレート）

```bash
python3 -m venv ~/ai-video/hf-env
source ~/ai-video/hf-env/bin/activate
pip install -U huggingface_hub

# hf downloadの前に、repo内の正確なファイルパスを確認する
hf repo-files Comfy-Org/Wan-2.2 | grep -E 'ti2v|umt5|vae|i2v'

# 以下は、該当パスが実在する場合のみ実行する例
hf download Comfy-Org/Wan-2.2 \
  split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors \
  --local-dir ./hf-download
```

上記CLIの`repo-files`サブコマンドの可用性もインストール済バージョンで確認し、存在しなければ`hf repo-files`の代替やHugging FaceのFiles画面/APIを使う。ファイルを`models/`へコピーする際はダウンロード元の実在パスを確認する。

## 10. Docker Compose原案（実機未検証）

```yaml
services:
  comfyui:
    image: dgx-comfyui:wan22
    container_name: dgx-comfyui
    restart: unless-stopped
    gpus: all
    ipc: host
    ports:
      - "127.0.0.1:8188:8188"
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      # コンテナ内ComfyUIの実パスを先に確認して変更
      - ./models:/opt/ComfyUI/models
      - ./input:/opt/ComfyUI/input
      - ./output:/opt/ComfyUI/output
      - ./workflows:/opt/ComfyUI/user/default/workflows
      - ${HOME}/.cache/huggingface:/root/.cache/huggingface
```

**重要:** 以前の原案はコンテナ内ComfyUIパスとLISTEN設定が未検証。`ports`だけではコンテナ側が0.0.0.0:8188をlistenする保証はない。公式イメージの起動コマンドを調べて`--listen 0.0.0.0 --port 8188`等を適切に指定する。モデルのmount先も実パスに合わせる。

起動・ログ確認候補:

```bash
mkdir -p models input output workflows
docker compose up -d
docker compose logs -f comfyui
```

DGX本体のローカルブラウザから http://localhost:8188 。外部PCからは以下のSSHトンネルで接続:

```bash
ssh -L 8188:127.0.0.1:8188 USER@DGX_SPARK_IP
```

## 11. ComfyUIワークフロー案

### 11.1 5B初期検証
- 公式テンプレートを使用。独自ノードをいきなり追加しない。
- 構成概念: diffusion model loader → text encoder/positive-negative prompt → video latent → sampler → VAE decode → Video Combine/Save。
- ワークフロー内の実際のノード名・接続・必要なプラグインは公式JSONが正とする。
- テストプロンプト（一般映像）: `Cinematic shot of an adult woman walking through a softly illuminated modern apartment, realistic movement, natural lighting, smooth camera tracking, detailed textures, 35mm film look.`

### 11.2 テストパラメータ
| 項目 | 最初 | 拡張 |
|---|---|---|
| 解像度 | 512×288 | 640×360、832×480 |
| フレーム | 49 | 81、121 |
| Steps | 20 | 12、30で画質比較 |
| FPS | 16 | 用途に応じ変更 |
| バッチ | 1 | 安定稼働後に再検討 |

フレーム数/16fpsにより約3.1秒、5.1秒、7.6秒の動画に相当する（実際の出力設定に依存）。

### 11.3 14Bへの拡張
- I2Vのhigh-noise / low-noiseモデルと適合VAEを導入。
- 5Bワークフローにモデルだけを差し替えず、14B専用の公式テンプレートを使う。
- まず低解像度、短尺、1ジョブでテスト。

## 12. 自動化の第2フェーズ

- FastAPIが`/jobs`で実行リクエストを受け、Ollamaにシーン設計を依頼。
- ショットごとのプロンプトと参照素材を保存。
- LLMの利用終了後にモデルをアンロードし、GPUメモリを開放。
- ComfyUIのローカル`/prompt`等へワークフローを送信（現行API仕様を確認）。
- 完了通知・ログ収集・失敗リトライ・生成履歴をローカルDBで管理。
- FFmpegでシーン結合しMP4を保存。
- 長尺生成は3〜5秒程度のショットを複数作成して編集する。人物と衣装・背景の連続性には参照画像・固定シード等を検証。

## 13. セキュリティ・プライバシー・合法性

- クラウド推論APIを使用しない。ローカル拡張のネット通信を監査する。
- 8188をインターネットに直接公開しない。認証やVPN/SSHの設計を確認。
- 入力素材、人物画像、生成履歴、出力動画を暗号化ストレージに保存する。
- 出所不明のカスタムノードやpickle形式モデルの実行を避ける。
- 使用するモデルのライセンス、商用利用条件、地域制限を配布元で確認。
- 実在する人物を利用する場合は成人・肖像利用の同意と権利を確認。違法なコンテンツは扱わない。

## 14. 検証・受入基準

1. `uname -m`、`nvidia-smi`、Docker GPU可用性が確認できる。
2. ComfyUIがARM64用コンテナから起動し、ローカル限定のURLで画面が開く。
3. 正式な配布元からモデルファイルが取得され、チェックサムとサイズが記録される。
4. Wan 2.2 5Bの公式ワークフローで一般映像のMP4を1本生成できる。
5. 生成に要した時間、ピークメモリ、設定値、エラーログを記録する。
6. 14B I2Vで参照画像から一般映像を1本生成できる。
7. オフライン状態でも再度生成可能で、クラウド側へのプロンプト送信がないことを確認する。
8. 結果をREADME、Docker Compose、依存ライブラリ固定情報、ワークフローJSON、トラブルシューティングにまとめる。

## 15. Claudeに求める最終成果物

1. 2026年11月時点の公式情報で検証済みの推奨構成と依存関係表。
2. DGX Spark/ARM64向けの再現可能なDockerfileとdocker-compose.yml。
3. モデル自動取得スクリプト（URL・ファイル名・配置先・チェックサム確認）。
4. ComfyUIの実際にインポートできる5Bと14BのワークフローJSON。
5. インストール、起動、テスト生成、ログ採取、停止のシェルスクリプト。
6. エラー対処（OOM、CUDA、ARM64 wheel、依存関係、ネットワーク・パス違い）。
7. ローカルLLM → ComfyUI → FFmpegの後続自動化案。
8. 未確認事項を推測で断定せず、確認方法を明示した報告。

## 16. 参考URL

- NVIDIA DGX Spark Playbooks: https://github.com/NVIDIA/dgx-spark-playbooks
- Wan 2.2公式: https://github.com/Wan-Video/Wan2.2
- Comfy-Org Wan 2.2配布: https://huggingface.co/Comfy-Org/Wan-2.2
- Wan TI2V 5B: https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B
- ComfyUI公式workflow templates: https://github.com/Comfy-Org/workflow_templates
- LTXVideo ComfyUI: https://github.com/Lightricks/ComfyUI-LTXVideo

---
**引継ぎメモ:** 本文は過去の会話で提案した構成を体系化したもので、実機実行済みの施工記録ではない。とくにDockerのmountパス、ComfyUI起動コマンド、Hugging Faceのファイルパス、モデル互換性をClaudeが確認してから実装すること。
