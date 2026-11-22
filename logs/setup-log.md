# セットアップログ

## 2026-11-18 手順1: 事前確認

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `uname -m` | `aarch64` | `aarch64` ✅ |
| `cat /etc/os-release` | Ubuntu 24.04 系 (DGX OS) | Ubuntu 24.04.5 LTS (noble) ✅ |
| `nvidia-smi` | GB10 が見える、CUDA 13 系 | NVIDIA GB10 / Driver 580.178.04 / CUDA 13.0 ✅。Memory-Usage は `Not Supported`（統合メモリのため。GPU メモリは `free` で見る必要あり） |
| `docker --version` | 存在する | Docker 29.6.2 ✅ |
| `docker compose version` | v2 以降 | v5.2.0 ✅ |
| `nvidia-ctk --version` | 存在する | NVIDIA Container Toolkit 1.20.1 ✅ |
| `docker info` / `docker run --rm --gpus all <CUDAベース> nvidia-smi` | GPU がコンテナから見える | ❌ **未実施**: `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock` |
| `df -h /` | 数百GB以上の空き | 3.7T 中 3.4T 空き ✅ |
| `free -g` | 約 128GB | total 121 / used 8 / available 113 / swap 15 ✅ |
| `docker image ls --format '{{.Repository}}:{{.Tag}}'` | ローカルの CUDA ベースイメージ候補を確認できる | ❌ Docker ソケットへのアクセス拒否 |
| `id && stat -c '%A %U %G %n' /var/run/docker.sock && getent group docker` | Docker 権限エラーの原因を特定できる | ユーザーは `docker` グループに未所属。ソケットは `root:docker`、モード `660` |
| `sudo usermod -aG docker saitoshinya` | `saitoshinya` が `docker` グループに追加される | ❌ sudo が対話的なパスワード入力を要求したため、エージェントからは実行不可 |
| ユーザー端末で `sudo usermod -aG docker saitoshinya` | `saitoshinya` が `docker` グループに追加される | `docker:x:988:saitoshinya` を確認 ✅。現在のシェルには未反映のため、以降は一時的に `sg docker -c` を使用 |
| `docker buildx imagetools inspect nvidia/cuda:13.0.0-base-ubuntu24.04` | CUDA 13.0公式イメージが `linux/arm64` に対応している | `linux/arm64` マニフェスト `sha256:c33da1d7a948e960842549e344af32eed7d8be20d1922ff99b52c119b08c72a7` を確認 ✅ |
| `sg docker -c "docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi"` | GPU がコンテナから見える | イメージ取得後、NVIDIA GB10 / Driver 580.178.04 / CUDA 13.0 を確認 ✅。イメージリストdigestは `sha256:6e43a6b02e5f16e4a715953be8b29c40acbe84aed11a61357ef0fc899967fbd9` |

### 補足
- 現ユーザー `saitoshinya` は `docker` グループ（gid 988, メンバー無し）に属していない。ソケットは `root:docker 660`。
- `/etc/docker/daemon.json` は存在しない。`/var/run/cdi/nvidia.yaml` はある（CDI 経由で GPU を渡せる見込み、未確認）。
- GPU 上では Xorg / gnome-shell / firefox 等 GUI プロセスが計 約0.8GB 使用中。

### 失敗と復旧案
- 原因: docker ソケットへの権限なし。
- 復旧案（sudo が必要なためユーザー確認待ち）:
  - A) `sudo usermod -aG docker saitoshinya` → 再ログイン（または `newgrp docker`）。注意: docker グループは実質 root 相当の権限。
  - B) docker コマンドを毎回 `sudo docker ...` で実行。
- ユーザー承認後に A を試行したが、sudo の対話的パスワード入力が必要なため停止。ユーザー端末で同コマンドを実行後、再ログインして再開する。
- ユーザー端末でグループ追加に成功。再ログイン前のシェルでも `sg docker -c` によりGPUコンテナ確認を完了した。

## 2026-11-18 手順2: NVIDIA公式ComfyUI Playbook

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `gh repo view NVIDIA/dgx-spark-playbooks --json ...` | NVIDIA公式リポジトリとデフォルトブランチを確認できる | `NVIDIA/dgx-spark-playbooks`、デフォルトブランチ `main` を確認 ✅ |
| `gh repo clone NVIDIA/dgx-spark-playbooks dgx-spark-playbooks` | 公式Playbookを取得できる | 取得成功 ✅ |
| `git rev-parse HEAD` | 検証対象のコミットを特定できる | `30883150541743d580c9c347700c6ac99fc396d2` |
| README・`assets/Dockerfile`・起動スクリプトの確認 | 現行のビルド方法、パス、起動方法を特定できる | Dockerfileは `nvidia/playbook-comfyui/assets/Dockerfile`、コンテナ内は `/opt/ComfyUI`、既定CMDは `python main.py --listen 0.0.0.0 --port 8188`。READMEのビルドは `docker build -t comfyui -f assets/Dockerfile .` |
| `docker buildx imagetools inspect nvcr.io/nvidia/pytorch:26.02-py3` | ベースイメージがARM64対応である | `linux/arm64` digest `sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9` を確認 ✅ |
| ARM64マニフェストのレイヤーサイズ集計 | 大容量取得前にダウンロード量を把握できる | 圧縮済み合計約9.26 GiB（81レイヤー）。ユーザー承認待ち |

### 現物確認メモ
- ベースイメージ: `nvcr.io/nvidia/pytorch:26.02-py3`（マニフェストリストdigest `sha256:bbc2b67e2533edd63ff1496bb1ed00a00338cdc1478af6c1a0bf9f4b369977e7`）
- ComfyUI: タグ `v0.33.2` に固定。
- カスタムノードは複数導入されるが、各リポジトリのcommitはDockerfile内で固定されていない。無改変検証後の手順4で固定が必要。
- 公式起動例のポート指定は `-p 8188:8188`。無改変検証でもホスト側は必ず `127.0.0.1:8188:8188` に変更して公開範囲を限定する。

### 無改変ビルド・起動検証

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `docker build -t comfyui -f assets/Dockerfile .` | 公式DockerfileがARM64/DGX Spark上で完走する | 約48分17秒で成功 ✅。イメージID `sha256:51ec5be3fd232c187bf7d67f1da3a4c2f4dd307196e8d17c57e7a93d464d832e` |
| `docker image inspect comfyui ...` | CMDとイメージ情報を確認できる | CMDは `python main.py --listen 0.0.0.0 --port 8188`、論理サイズ `11,437,749,774` bytes |
| `docker run -d --name comfyui --gpus all --ipc host --ulimit memlock=-1 -p 127.0.0.1:8188:8188 comfyui` | GPU付きで起動し、ホストのループバックだけに公開される | 起動成功 ✅。`127.0.0.1:8188->8188/tcp` を確認 |
| `docker logs comfyui` | ComfyUIのready行とGB10認識が確認できる | `Device: cuda:0 NVIDIA GB10 : native`、`Total VRAM 124608 MB`、`ComfyUI version: 0.33.2`、`To see the GUI go to: http://0.0.0.0:8188` を確認 ✅ |
| `curl http://127.0.0.1:8188/` | HTTP 200 | `HTTP 200` ✅ |
| Cursor内ブラウザーで `http://127.0.0.1:8188/` を表示 | ComfyUIの操作画面が表示される | 日本語UI、ワークフロー画面、実行ボタン等の表示を確認 ✅ |
| コンテナ内Git commit採取 | 後続のバージョン固定に必要なcommitを特定できる | 下記commit一覧を採取 ✅ |
| `docker stop comfyui` | 検証後にコンテナを停止できる | 正常停止 ✅ |
| `docker system df` / `df -h /` | ビルド後の使用量と残容量を確認できる | Docker Images 36.47GB、Build Cache 35.91GB、ルート空き3.4TB |

### ビルド時点のcommit
- ComfyUI: `7cee3ceb1a35503172e0dfb8dbdbdedee2aba8aa`
- ComfyUI-Frame-Interpolation: `26545cc2dd95bc3d27f056016300673bdeee78f5`
- ComfyUI-KJNodes: `4d6f027c23991aef33698a5c8c57452111df4cdb`
- ComfyUI-Manager: `3a2177fb2484f086e554145add56dd282a7c5ad4`
- ComfyUI-VideoHelperSuite: `4d907bee61e92c2e65af3bd6383a4e4d356126d1`
- ComfyUI_IPAdapter_plus: `a0f451a5113cf9becb0847b92884cb10cbdec0ef`
- comfyui_HiDream-Sampler: `98ad017cac93b782e2af95411e4c10d493ecb841`
- comfyui_controlnet_aux: `c2655d2970d810d8a3d71deed204cdf416b66132`

### 起動時に確認した警告・不具合
- ComfyUI本体とCUDAは正常起動したが、ComfyUI-Managerの事前チェックが `PyTorch is not installed` と誤検知した。その後ComfyUI本体はPyTorch `2.11.0a0+eb65b36914.nv26.02` とCUDAを正常認識。
- `comfyui_controlnet_aux` は `scikit-image` 不足によりDWPose関連2モジュールのロードに失敗。Dockerfileがカスタムノード依存関係のインストール失敗を `|| true` で無視するため、ビルド自体は成功している。
- HiDream Samplerは `diffusers` およびモデル未配置の警告あり。今回のUI起動確認やWan検証には影響しないが、HiDream利用前に修正が必要。
- ComfyUI-Managerは初回起動時に `network_mode: public` で外部キャッシュを更新した。手順4のオフライン化で無効化する。

## 2026-11-18 手順3: Wan 2.2 TI2V-5B（事前調査）

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `gh repo view Comfy-Org/workflow_templates --json ...` | 公式ワークフローリポジトリを確認できる | ❌ `api.github.com` に接続できない |
| `python3 -c "import huggingface_hub; ..."` | Hugging Face Hub APIの利用可否を確認できる | ❌ `huggingface_hub` はホストに未導入 |
| `curl -I https://api.github.com` | GitHubへ接続できる | ❌ `Could not resolve host` |
| `curl -I https://huggingface.co` | Hugging Faceへ接続できる | ❌ `Could not resolve host` |
| `getent hosts api.github.com huggingface.co` | DNS解決できる | ❌ 解決結果なし |
| `ip route && resolvectl status` | デフォルトルートとDNSサーバーが存在する | ❌ デフォルトルートなし。全インターフェースのDNSスコープが `none` |

### 停止理由と復旧
- 現在ホストがネットワークから切断されており、デフォルトルートおよび上流DNSがない。
- 公式ファイル一覧・サイズの確認、公式ワークフロー取得、モデルダウンロードができないため停止。
- 有線またはWi-Fiを再接続後、GitHub/Hugging Faceの名前解決を再確認して再開する。

### ネットワーク復旧後の公式情報確認

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `getent hosts ... && curl -I https://huggingface.co` / `ip route` | DNS、HTTPS、デフォルトルートが復旧している | DNS解決、HTTP 200、Wi-Fi経由のデフォルトルートを確認 ✅ |
| `python3 -m venv hf-env && hf-env/bin/pip install huggingface_hub` | 隔離環境で公式Hub APIを利用できる | `huggingface_hub 2.4.0` を導入 ✅ |
| `gh repo clone Comfy-Org/workflow_templates workflow_templates` | 公式ワークフローをcloneできる | ❌ 通信待ちのまま約4分進まず中断。空のGit管理ディレクトリのみ残存 |
| `gh api repos/Comfy-Org/workflow_templates/git/trees/main?...` | 公式5Bテンプレートの実在を確認できる | `templates/video_wan2_2_5B_ti2v.json`、blob `634303feba82471ef3df3356178de4c037ae213e`、15,099 bytesを確認 ✅ |
| `gh api .../commits/main` / 公式テンプレート本文取得 | テンプレートのrevisionとモデル要件を確認できる | workflow_templates commit `85984c2b1ee8c0aa39179bf122a7d01fa1b21c09`。既定値は1280x704、121フレーム、24fps、20 steps ✅ |
| `HfApi().list_repo_files('Comfy-Org/Wan_2.2_ComfyUI_Repackaged')` | 配布元の実在ファイルを列挙できる | 5B diffusion model、FP8 text encoder、Wan 2.2 VAEを確認 ✅ |
| `HfApi().model_info(..., files_metadata=True)` | revision、サイズ、SHA256を取得できる | 下記3ファイルを確認 ✅ |

### 5B公式テンプレートが要求するモデル
- HF revision: `Comfy-Org/Wan_2.2_ComfyUI_Repackaged@ee6f4a40737a995bf5818954cfce6d59443b0f04`
- `split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`
  - 9,999,658,848 bytes
  - SHA256 `456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e`
- `split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
  - 6,735,906,897 bytes
  - SHA256 `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68`
  - テンプレート内URLはWan 2.1リポジトリだが、Wan 2.2リポジトリ内の同名ファイルとサイズ・SHA256が一致することを確認。
- `split_files/vae/wan2.2_vae.safetensors`
  - 1,409,400,960 bytes
  - SHA256 `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156`
- 合計: 18,144,966,705 bytes（約16.90 GiB）。大容量取得のユーザー承認待ち。

### モデル取得・検証

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `python3 scripts/download-workflow.py` | commit固定した公式UIワークフローを取得し、Git blobを検証できる | `workflows/video_wan2_2_5B_ti2v.json` を取得。blob `634303feba82471ef3df3356178de4c037ae213e` 一致 ✅ |
| `hf-env/bin/python scripts/download-models.py`（Xet/CAS） | 3モデルを取得してSHA256を検証できる | ❌ 約16分後、最初のファイルでXet `File reconstruction error`。有効な部分ファイルは残らず |
| `HF_HUB_DISABLE_XET=1` 相当を反映して再実行 | 通常HTTPSで中断を再開し、3モデルを検証できる | 接続切断・タイムアウト時に自動再開し、約94分56秒で3ファイル完了。サイズ・SHA256すべて一致 ✅ |
| `curl /models/diffusion_models` 等 | ComfyUIが3モデルを認識する | diffusion model、text encoder、VAEを認識 ✅ |

### 初回生成（公式テンプレート既定値）

| 項目 | 実測値 |
|---|---|
| プロンプト | 公式テンプレート既定の一般映像（1970年代風の地下鉄駅で演奏するストリートミュージシャン） |
| 解像度 | 1280×704 |
| フレーム数 / FPS | 121 / 24 |
| Steps / CFG / Sampler / Scheduler | 20 / 5 / `uni_pc` / `simple` |
| Seed | `898471028164125` |
| ComfyUI処理時間 | 324.78秒 |
| API投入から完了まで | 326.37秒 |
| ホスト統合メモリ最大使用量 | 65,527,050,240 bytes（約61.03 GiB） |
| ホスト統合メモリ最小Available | 65,133,924,352 bytes（約60.66 GiB） |
| ComfyUI報告の最小VRAM free | 795,258,368 bytes（UMAのため参考値） |
| 出力 | `output/video/wan2_2_5B_default_00001_.mp4` |
| 出力SHA256 | `ada8cc0508c203714e6677060d04659e634301a9944a1e19a78f73fb43bdc786` |

### 出力検証
- ホストに`ffprobe`がなかったため最初の確認は失敗。公式コンテナ内の`ffprobe`で復旧。
- H.264 / yuv420p / 1280×704 / 24fps / 121 frames / 5.041667秒 / 1,223,600 bytesを確認 ✅
- ブラウザーで中央フレームを目視し、一般的な地下鉄駅・ギター演奏者の映像であることを確認 ✅
- 出力MP4には`prompt`タグとしてAPIグラフとプロンプトが埋め込まれていることを確認。外部公開前のメタデータ削除が必須。
- ComfyUIログ上のステージ量: Wan text encoder 6419MB、WAN22 9535MB、Wan VAE 1344MB。
- 完了後に`POST /free`（`unload_models=true`, `free_memory=true`）を実行し、コンテナを停止した。

### 追加した実装
- `scripts/download-models.py`: revision・サイズ・SHA256固定、HTTPS再開対応。
- `scripts/download-workflow.py`: workflow_templates commit・Git blob固定。
- `scripts/test-generate.py`: API投入、完了待機、毎秒メモリ採取、生成ログ保存。
- `workflows/video_wan2_2_5B_ti2v.api.json`: 公式UIテンプレートと同じ生成条件のAPIグラフ。
- `models/MANIFEST.md`: 取得元、revision、サイズ、SHA256。
- 生成詳細: `logs/generations/20261118T071622Z-c779faa3-bf0d-486b-8d8c-5990491f1a86.json`

## 2026-11-19 手順4: バージョン固定・ローカル限定・オフライン化

### 公式設定の確認

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| ComfyUI `v0.33.2`タグをGitHub APIで照合 | タグのcommitを特定できる | `7cee3ceb1a35503172e0dfb8dbdbdedee2aba8aa` ✅ |
| ComfyUI-Manager固定commitのREADME・実装を確認 | 正式なオフライン設定を特定できる | `network_mode = offline` は外部接続を使わずキャッシュを利用する公式設定と確認 ✅ |
| 採取済みカスタムノードcommitをGitHub APIで照合 | 各commitが配布元に実在する | 7リポジトリすべて実在。ただしWan 2.2/LTXの公式ワークフローは標準ノードのみで動くため、固定イメージにはManagerだけを残す方針とした |

### 固定イメージとCompose

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `docker compose config --quiet` | Compose構文が妥当 | 成功 ✅ |
| `docker compose build` | digest・commit固定イメージを構築できる | 約6分20秒で成功 ✅ |
| イメージ内commit・設定確認 | ComfyUI/Managerのcommitとオフライン設定が一致する | ComfyUI `7cee3ceb...`、Manager `3a2177fb...`、`network_mode = offline` を確認 ✅ |
| `scripts/start.sh`（internal network版） | 127.0.0.1からUIへ到達できる | ❌ Docker `internal`ネットワークでは公開ポートもホストから到達不可。ログ上はComfyUI正常起動 |
| internal networkを外して再実行 | 127.0.0.1限定でUIへ到達できる | `HTTP 200`、`127.0.0.1:8188->8188/tcp` ✅ |

固定情報:
- NGC ARM64 base digest: `sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9`
- ローカルイメージ: `unbound-comfyui:0.33.2`
- ローカルイメージdigest: `sha256:6dce758e937019f289543b6727806fe97d87f6787b140ea5641608bd914f9963`
- 論理サイズ: 11,050,153,924 bytes
- 起動ログ: `[ComfyUI-Manager] network_mode: offline`
- Managerによる外部キャッシュ更新ログなし。
- カスタムノードはManagerのみ。公式Playbookで依存導入に失敗していたHiDream・ControlNet補助等の未使用ノードを除外。

### 完全オフライン受入試験

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `docker network disconnect unbound_engine_default unbound-comfyui` | 実行中コンテナの外部通信を物理的に切断 | `.NetworkSettings.Networks` が空 `{}` ✅ |
| コンテナ内から`https://huggingface.co`へ接続 | 接続失敗 | `Temporary failure in name resolution` ✅ |
| 切断状態で公式5B APIワークフローを再投入 | モデル取得なしで動画を再生成できる | 328.33秒で成功 ✅ |
| Dockerネットワークを再接続 | 通常のローカルUIへ復帰 | `HTTP 200` ✅ |

オフライン出力:
- `output/video/wan2_2_5B_default_00002_.mp4`
- H.264 / 1280×704 / 24fps / 121 frames / 5.041667秒
- 1,219,534 bytes
- SHA256 `936e17041d27f8c85f1832e2efa4495d75f60ad5b95dc8231fba2099c09c4fb9`

### メタデータ削除

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `scripts/strip-metadata.sh` 初回 | メタデータ削除後にffprobe検証 | FFmpeg処理は成功したが、実行中コンテナ用のffprobeコマンド配列に`ffprobe`が欠けてexit 127 ❌ |
| コマンド配列修正後、実行中コンテナで再試行 | `prompt` / `workflow`タグがない | 成功 ✅ |
| ComfyUI停止後に別動画で再試行 | 一時コンテナでも削除・検証できる | 成功し、一時コンテナも`--rm`で残存なし ✅ |

検証済み出力:
- `output/video/wan2_2_5B_default_00002_stripped.mp4`
- 保持タグは`major_brand`、`minor_version`、`compatible_brands`、`encoder`のみ。
- `prompt`および`workflow`タグなし。

### 追加・更新した成果物
- `Dockerfile`: NGC ARM64 digest、ComfyUI/Manager commit固定。
- `docker-compose.yml`: `127.0.0.1`限定、GPU、モデルread-only、セキュリティ制約、healthcheck。
- `config/comfyui-manager.ini`: Managerオフライン・取得/インストール無効。
- `versions.lock`: Playbook、ベースイメージ、ComfyUI、Manager、モデル、ワークフローを固定。
- `scripts/preflight.sh`
- `scripts/start.sh`
- `scripts/stop.sh`（`POST /free`後に停止）
- `scripts/collect-logs.sh`
- `scripts/strip-metadata.sh`
- `scripts/test-offline.sh`
- `README.md`
- 採取ログ: `logs/comfyui-20261118T154018Z.log`

## 2026-11-19 手順5: Wan 14B I2V / LTX-2.5 比較（事前調査）

### Wan 2.2 I2V-A14B

- 公式テンプレート: `templates/video_wan2_2_14B_i2v.json`
- workflow_templates commit:
  `85984c2b1ee8c0aa39179bf122a7d01fa1b21c09`
- 既定値: 640×640相当、5秒、16fps。
- 通常設定: 20 steps、split 10、CFG 3.5/1。
- 4-step LoRA設定: 4 steps、split 2。公式テンプレートでは初期状態OFF。
- 追加で必要なファイル:
  - high-noise FP8: 14,294,742,832 bytes
  - low-noise FP8: 14,294,742,832 bytes
  - high-noise 4-step LoRA: 1,226,977,424 bytes
  - low-noise 4-step LoRA: 1,226,977,424 bytes
  - Wan 2.1 VAE: 253,815,318 bytes
- text encoderは5Bで取得済み。
- 追加合計: 31,297,255,830 bytes（約29.15 GiB）。

### LTX-2.5

- 公式配布元: `Lightricks/LTX-2.5`
- revision: `49b093a39c19169a8a81721dcc541e07e945232f`
- 公式ローカルI2Vテンプレート:
  `templates/video_ltx2_5_i2v.json`
- 既定値: 1280×720、5秒、24fps、prompt enhancerはOFF。
- 必要ファイル:
  - distilled transformer INT8: 21,504,034,224 bytes
  - Gemma 4 12B text encoder INT8: 15,372,969,374 bytes
  - video VAE BF16: 1,472,223,346 bytes
  - audio VAE BF16: 364,866,540 bytes
  - spatial latent upscaler: 995,778,752 bytes
- 合計: 39,709,872,236 bytes（約36.98 GiB）。
- gated repository。現在のホストはHugging Faceへ未ログイン。
- 固定ComfyUI 0.33.2とのノード互換性を`/object_info`で照合し、
  `MarkdownNote`以外の全ランタイムノードが存在することを確認。

### ライセンス確認

- LTX-2.x Community License Agreement原文（2026-10-29改定）を確認。
- 年間売上1,000万米ドル以上のEntityは、非商用目的を除く利用に有償契約が必要。
- 2026-11-03版Acceptable Use Policy原文も確認。
- AUPは性的に露骨なコンテンツ、性的行為、性的フェティッシュ・
  ファンタジーの生成を明示的に禁止する。
- コンテンツ来歴情報や電子透かし等の除去・妨害も禁止。
- 詳細を`docs/licenses.md`へ記録。
- LTX-2.5は一般映像の比較に限定し、本プロジェクトが当初想定する
  性的に露骨な用途には使用できない。

### ダウンロード前の状態

- Wan 14BとLTX-2.5の追加合計:
  71,007,128,066 bytes（約66.13 GiB）。
- LTX-2.5はHugging Face上で利用条件への同意と`hf auth login`が必要。
- 大容量取得およびLTXライセンス同意についてユーザー確認待ち。

## 2026-11-19 手順5: Wan 14B I2V 4-step LoRA

### モデル・公式ワークフロー取得

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `hf-env/bin/python scripts/download-models.py wan14` | Wan 14B FP8 high/low、4-step LoRA high/low、VAEを固定revisionから取得し検証 | 約7,162秒で5ファイル取得、サイズ・SHA256一致 ✅ |
| `hf-env/bin/python scripts/download-workflow.py wan14 ltx25` | 固定commitから公式ワークフローと入力画像を取得 | Wan/LTXのJSON・入力画像計4ファイルでGit blob一致 ✅ |
| Wan 14B 5ファイルを`sha256sum`で再検証 | downloader記載値と一致 | 5ファイルすべて一致 ✅ |
| `python3 scripts/prepare-comparison-workflows.py` | 両モデルを同じ入力・プロンプト・640×640・5秒・16fps・seedに固定 | `compare_wan14_4step_i2v.json`と`compare_ltx2_5_i2v.json`を作成 ✅ |
| `hf-env/bin/hf auth whoami` | LTX取得に必要なログインを確認 | `Not logged in`。LTX-2.5取得は認証待ち |

Wan 14B追加資産:
- high/low FP8 scaled: 各14,294,742,832 bytes
- high/low LightX2V 4-step LoRA: 各1,226,977,424 bytes
- Wan 2.1 VAE: 253,815,318 bytes
- 追加合計: 31,297,255,830 bytes
- 全サイズ・SHA256を`models/MANIFEST.md`へ記録。

### 生成時の復旧記録

1. 最初のキュー投入では4-stepフラグの配列位置を1つ誤り、通常20-stepの
   ままだった。約47秒で`POST /interrupt`し、出力前に中断。
2. 正しいフラグ（上位subgraph node 129の`widgets_values[11]`）で再投入。
   生成自体は102.738秒で成功したが、計測スクリプトが相対パスに
   `Path.relative_to()`を直接適用して終了時に失敗。パスを`resolve()`して修正。
3. `POST /free`でモデルを解放後、同じ設定を再実行して生成・計測とも成功。
4. 中断ステータスで計測が終了しない問題も、`status_str == error`を終端として
   扱うよう修正。

### Wan 14B 4-step実測

| 項目 | 実測値 |
|---|---|
| 入力 | 公式`video_wan2_2_14B_i2v_input_image.jpg` |
| プロンプト | 公式テンプレート既定の一般映像（白いドラゴン戦士） |
| 解像度 | 640×640 |
| フレーム数 / FPS / 長さ | 81 / 16 / 5.0625秒 |
| Steps / split | 4 / 2（high/low LightX2V LoRA） |
| Seed | `264244520398999` |
| ComfyUI execution時間 | 100.352秒 |
| 計測開始から完了まで | 98.157秒（キュー投入後に計測開始） |
| ホスト統合メモリ最大使用量 | 92,026,261,504 bytes（約85.71 GiB） |
| ホスト統合メモリ最小Available | 38,634,713,088 bytes（約35.98 GiB） |
| ComfyUI報告の最小VRAM free | 5,642,219,520 bytes（UMAのため参考値） |
| 出力 | `output/video/compare_wan14_4step_00002_.mp4` |
| 出力SHA256 | `9edc13e7ea82b5952cd4f73b17506b2998b0d9185fd3732c5520477794240e5d` |

出力検証:
- H.264 / 640×640 / 16fps / 81 frames / 5.0625秒 / 718,617 bytes ✅
- ブラウザーで目視し、白いドラゴン戦士の一般映像であることを確認 ✅
- 生成ログ:
  `logs/generations/20261118T175342Z-5db23a7d-4f12-48b8-b490-0b0bb4c66a9f.json`

## 2026-11-19 手順5: LTX-2.5 I2Vと同条件比較

### 認証・モデル取得

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `hf auth login --force`＋ブラウザーDevice Authorization | CLIをHugging Faceへ安全に認証 | ユーザー`otooth`として認証成功 ✅ |
| LTX-2.5の`Agree and Access` | gated repositoryへのアクセスを有効化 | 情報共有・利用条件に同意しアクセス成功 ✅ |
| `hf auth whoami`と`HfApi.model_info()` | CLI認証と固定revisionへのアクセスを確認 | `otooth`、revision `49b093a...`、gated=`auto`を確認 ✅ |
| `hf-env/bin/python scripts/download-models.py ltx25` | 5ファイルを取得しサイズ・SHA256検証 | 8,764秒（約2時間26分）で成功、5ファイルすべて一致 ✅ |

ダウンロード中、21.5GB Transformerと15.4GB Text Encoderで
接続切断・read timeoutが計10回発生した。HTTPS Rangeによる自動再開が機能し、
既取得部分を保持して完走した。取得合計は39,709,872,236 bytes。

### ワークフロー復旧記録

1. 公式テンプレートはPrompt EnhancerがOFFでも、未取得の任意Enhancerモデル名を
   combo入力として検証し、`Value not in list`で投入を拒否した。
   未使用値を取得済みLTX Text Encoder名へ変更し、EnhancerはOFFのまま維持。
2. 初回生成は成功したが1280×704だった。上位subgraphの640×640値より、
   接続された`ResolutionSelector`（16:9・0.9MP）が優先されていたため。
3. `ResolutionSelector`を`1:1 (Square)`・0.4MP・multiple 32へ変更し、
   実出力640×640を確認して比較対象とした。

### LTX-2.5実測

| 項目 | 実測値 |
|---|---|
| 入力・プロンプト・Seed | Wan 14B比較と同一 |
| 解像度 | 640×640 |
| フレーム数 / FPS / 長さ | 81 / 16 / 5.0625秒 |
| Prompt Enhancer | OFF |
| ComfyUI execution時間 | 45.686秒 |
| 計測開始から完了まで | 57.746秒 |
| ホスト統合メモリ最大使用量 | 99,614,146,560 bytes（約92.77 GiB） |
| ホスト統合メモリ最小Available | 31,046,828,032 bytes（約28.91 GiB） |
| ComfyUI報告の最小VRAM free | 2,538,950,656 bytes（UMAのため参考値） |
| 出力 | `output/video/compare_ltx2_5_i2v_00002_.mp4` |
| 出力SHA256 | `0cc39706e65efde13c02b5cfa8db9de3db9895c67a4fc6b233ee5d2da19736eb` |

出力検証:
- H.264 / 640×640 / 16fps / 81 frames / 5.0625秒 / 680,252 bytes ✅
- AAC / 48kHz / stereo / 5.05秒の同期音声を含む ✅
- ブラウザーで白いドラゴン戦士の一般映像を目視確認 ✅
- LTXの来歴情報を保護するため、メタデータ削除は実行していない。
- 生成ログ:
  `logs/generations/20261118T203607Z-6aa44173-85f8-425d-a345-c5eda160120d.json`

### 同条件比較結果

- LTX-2.5: 45.686秒、Wan 14B 4-step: 100.352秒。
- LTX-2.5はComfyUI execution時間で約2.20倍高速。
- ピーク統合メモリはLTX-2.5が約7.07 GiB多い。
- LTX-2.5のみ同期音声を生成。
- 両方とも同じ入力・プロンプト・Seed・640×640・81 frames・16fpsで成功。
- 単一サンプルのため、画質優劣の一般化は行わない。
- 完了後に`POST /free`でモデルを解放し、ComfyUIを停止。
- 採取ログ: `logs/comfyui-20261118T203828Z.log`

## 2026-11-19 手順6: Ollama → ComfyUI → FFmpeg自動化

### 公式APIとローカル環境確認

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| Ollama公式`/api/generate`仕様確認 | structured outputと`keep_alive: 0`の現行仕様を確認 | JSON Schema指定と即時アンロードが公式仕様に存在 ✅ |
| ComfyUI公式Server API仕様確認 | `/prompt`、`/history/{id}`、`/free`の現行仕様を確認 | 3ルートすべて公式資料に存在 ✅ |
| `ollama list` / `/api/version` / `/api/ps` | 利用可能なQwenと初期アンロード状態を確認 | Ollama 0.40.3、`qwen3.6:35b-a3b-q8_0`、loaded modelsなし ✅ |
| FastAPI専用venv構築 | ARM64上で固定依存を導入 | FastAPI 0.165.0、Uvicorn 0.54.0ほか全依存を固定 ✅ |

### 実装

- `automation/app.py`
  - loopback限定FastAPIから`POST /jobs`を受付。
  - 単一worker queueでジョブを逐次実行。
  - QwenへJSON Schema付きでショット設計を依頼。
  - Ollamaを`keep_alive: 0`でアンロードしてからComfyUIを実行。
  - ComfyUI `/free`を工程間と終了時に実行。
  - SQLite `data/jobs.sqlite3`へジョブ、計画、イベント、失敗を記録。
  - WanはFFmpegでメタデータを除去。LTXはAUP上の来歴情報保護のため
    remuxせず原本を保持し、1ショットだけ許可。
- 公式UI workflowを固定frontendでAPI JSONへ変換:
  - `workflows/compare_wan14_4step_i2v.api.json`
  - `workflows/compare_ltx2_5_i2v.api.json`
- `scripts/start-api.sh` / `scripts/stop-api.sh`
- `requirements-automation.txt`
- `docs/troubleshooting.md`

### 受入試験と復旧

| コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `scripts/start-api.sh` | ComfyUIとAPIをloopback限定で起動 | `127.0.0.1:8188` / `127.0.0.1:8001`で正常起動 ✅ |
| 一般映像のWan 14Bジョブを`POST /jobs` | Qwen計画→アンロード→動画生成→FFmpeg→履歴保存 | 初回は動画計算後、SaveVideoでpermission denied ❌ |
| ジョブディレクトリへ通常DAC書込権限を付与して再試験 | Capabilityを全削除したコンテナでも保存可能 | 全工程115.98秒で成功 ✅ |
| `/api/ps` | 動画生成後にOllamaモデルが残らない | `{"models":[]}` ✅ |
| LTX 2ショット要求 | 来歴情報を壊す結合を拒否 | HTTP 422で拒否 ✅ |

初回失敗原因:
- FastAPIがホスト所有775でジョブディレクトリを作成した。
- コンテナは`cap_drop: ALL`のため、UID 0でも`CAP_DAC_OVERRIDE`がなく書込不能。
- ジョブ専用ディレクトリだけを0777にし、モデル・設定・他ディレクトリの
  read-only制約は維持した。

成功ジョブ:
- Job ID: `6ab3dcac-56f7-43f6-b5dd-298fec6fb9da`
- Qwen計画: 約11.33秒
- 最終出力:
  `output/jobs/6ab3dcac-56f7-43f6-b5dd-298fec6fb9da/final.mp4`
- H.264 / 640×640 / 16fps / 81 frames / 5.0625秒 / 615,810 bytes
- SHA256:
  `28e720538899502560d2fe24d95416be05a7aac1fd9ccb19c2bfae203af1f613`
- `prompt` / `workflow`タグなし。一般的な白いドラゴン戦士の映像を目視確認 ✅
- 構文、IDE lint、`pip check`に問題なし。8001/8188がともに
  `127.0.0.1`限定であることを`ss`で確認 ✅
- 受入完了後、APIを終了し、ComfyUIを`POST /free`後に停止。

## 2026-11-19 日本語Web UI

- `http://127.0.0.1:8001/`に通常利用者向けの1画面UIを追加。
- 日本語プロンプト、Wan/LTX選択、ショット数、既存画像選択、
  20MB以下のPNG/JPEG/WebPアップロード、Seed指定に対応。
- ジョブ進捗、工程イベント、失敗理由、最近の履歴、完成MP4再生を表示。
- アップロードは拡張子、容量、ファイルシグネチャを検証し、
  ランダム化した安全なファイル名で`input/`へ保存。
- 出力配信は`output/`配下のMP4だけに制限し、Range request（HTTP 206）を確認。
- LTX選択時は1ショットに固定し、ライセンス/AUP確認欄を表示。
- 過去の成功ジョブをUIから開き、動画再生と全工程表示を目視確認 ✅
- 追加依存`python-multipart==0.0.34`を固定し、`pip check`成功。

## 2026-11-19 Unbound Engineロゴ

- コンセプト: 開いた軌道＝制約からの解放、中央の発光コア＝生成エンジン、
  紫からシアンのグラデーション＝既存UIと動画生成の動き。
- 32pxでも識別できる文字なしのベクターマークとして
  `automation/static/logo.svg`を作成。
- Web UIのヘッダーロゴ、ブランド名、SVG faviconへ設定。
- 透明背景、ダークUI、レスポンシブ表示をブラウザーで確認 ✅

## 2026-11-19 プロンプト追従不良の原因調査と修正

対象入力:

`An old woman is trading blows with a muscular man.`

### 原因

1. UIが既存の`neon_cyborg_portrait.png`を開始画像として自動選択していた。
2. 選択されたWan 14BはI2Vモデルであり、文章より開始画像の人物、
   サイバーパンクの服装、背景を強く維持した。
3. Qwenによる補強が`trading blows`の相互・反復動作を単発攻撃へ
   弱める場合があった。
4. Wan 5Bへ短い原文だけを渡した試験では画像由来の要素は消えたが、
   複数人物の同時表示と相互動作が不安定だった。

### 修正

- 文章から生成するWan 2.2 5Bを自動化APIへ追加し、UIの既定モデルにした。
- Wan 14B/LTX-2.5の開始画像を必須かつ未選択状態に変更し、
  人物・服装・背景が強く引き継がれる旨を表示した。
- プロンプト補強を既定ONにし、人物数、年齢、性別、体格、主要動作を
  変更しないようQwenのsystem promptを厳格化した。
- 複数人物ではmedium-wide/wide shotで全員を同時表示し、
  `trading blows`は双方が繰り返しパンチと防御を行い、
  単発攻撃や組技へ変更しない制約を追加した。
- 補強をOFFにした場合は入力文を改変せず動画モデルへ渡すようにした。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `POST /jobs`（Wan 5B、補強OFF、対象入力） | 画像の影響なしで文章から生成 | サイバーパンク要素は消えたが、女性が隠れ、組技に近い構図になった |
| `POST /jobs`（Wan 5B、厳格補強ON、対象入力） | 高齢女性と筋肉質男性を同時表示し、相互の打撃を生成 | Job `e359db17-0bb3-434f-91ce-251841400ec3`が約93秒で成功 ✅ |
| ブラウザーで1.0秒、2.5秒、4.5秒を目視確認 | 指定人物、非サイバーパンク、打撃姿勢を維持 | 白髪の高齢女性と筋肉質男性が全時点で同時に映り、互いに構えて打ち合う動作を確認 ✅ |

最終出力:

`output/jobs/e359db17-0bb3-434f-91ce-251841400ec3/final.mp4`

改善は確認できたが、生成モデルであるため、複雑な二人の接触動作や
拳・腕の形状をフレーム単位で完全に保証するものではない。

## 2026-11-19 Wan Animate 2: 三面図キャラクター動画

### 公式仕様と固定

- 実行コマンド: ComfyUI公式ドキュメント
  `tutorials/video/wan/wan-animate-2`と公式workflow templateを確認。
- 期待結果: 参照キャラクター画像と動作元動画を直接入力できる公式構成を確認。
- 実際の結果: `WanAnimate2ToVideo`が外見、表情、身体・手の動きを転写し、
  ComfyUI 0.36.0以上を必要とすることを確認 ✅
- workflow_templates commit:
  `85984c2b1ee8c0aa39179bf122a7d01fa1b21c09`
- UI workflow blob:
  `b2771d09110f00a5ab9690df23b118dc695f129b`
- ComfyUIを0.45.0 / commit
  `989d93d0d968e704c589ce0146bb18c77f6b50cc`へ更新。
- 公式frontendの`app.graphToPrompt()`でAPI形式へ変換し、
  比較表示だけの枝を除いた固定workflowを
  `workflows/video_wan_animate2_distilled.api.json`へ保存。
- API workflow SHA256:
  `8238e977ee851040a7d97cb5aab94e8c69ea0672b6b6aaedfe8677c98bb8c046`

### モデル取得

- 実行コマンド:
  `hf-env/bin/python -u scripts/download-models.py animate2`
- 期待結果: revision固定した必要モデルだけを取得し、サイズとSHA256を検証。
- 実際の結果: 約61分56秒で18,171,201,202 bytesを取得し、3ファイル
  すべて検証成功 ✅
- Diffusion:
  `d2e566ecac3f164cb1a6b63dc7d868d6678c9affa49fbceb3abe4f9458f89c8a`
- CLIP Vision:
  `64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161`
- VAE:
  `1ab9a32cc2c740f6e39d80d367ce5dcc28db8c71b79b28670546b8973e9d75f9`
- text encoderは既存Wan 2.2の固定ファイルを再利用。

### 実装

- 日本語UIへ`Wan Animate 2 — 三面図のキャラを動かす`を追加。
- 横並び三面図について、正面図の位置を左・中央・右から選択し、
  ブラウザーのCanvasで該当する3分の1だけをPNGへ切り出す。
- 画像が最初から1人の場合は「切り出さない」に対応。
- MP4動作元動画を250MBまでアップロード可能。拡張子、容量、
  MP4 `ftyp`シグネチャを検証し、ランダム名で保存。
- 動作動画を省略した場合はWan 5Bで81フレームの動作下書きを生成し、
  `/free`で解放してからWan Animate 2へ逐次切替。
- 参照画像、動作動画、文章prompt、seedを固定API workflowへ設定。
- Wan出力としてFFmpegでメタデータを再エンコードなしで除去。

### 構築時の失敗と復旧

- 初回Docker buildはGitHub clone中のHTTP/2切断で失敗。
- 全履歴cloneをcommit指定の`git fetch --depth 1`へ変更し、再構築成功。
- NGCベースの`datasets`とComfyUIのTransformersが要求する
  Hugging Face Hub範囲を`1.33.0`へ固定。
- 未使用の`spin` CLIが新しいClickと競合したためコンテナから除去。
- 最終`pip check`: ホスト自動化環境・コンテナとも問題なし ✅
- 最終イメージ:
  `unbound-comfyui:0.45.0`
- digest:
  `sha256:cf7891cfd2d3e048551d7598e11a5ee573cdd28f596056bfcb64b1bbcae9b078`

### 受入試験

動作動画アップロード経路:

- Job ID: `21cf6cc0-ca5c-4897-8425-436da058ea56`
- 入力: 公式の一般的なピンク髪キャラクター画像と短いダンス動画。
- 期待結果: 参照キャラクターの外見を維持して動作を転写。
- 実際の結果: 約341.6秒で成功。ピンク髪、黒と金の衣装を維持し、
  ダンス姿勢へ変化することを中間フレームで目視確認 ✅
- 出力: H.264 / 480×832 / 24fps / 81 frames / 3.375秒。

文章からの自動動作経路:

- 初回は動作下書き保存先を作成する順序が遅く、
  capability削除済みコンテナがディレクトリを作れず失敗。
- ジョブ開始時に0777の専用出力ディレクトリを作成するよう修正。
- Job ID: `71f38878-10f9-46f5-b6bc-e64a94af2b50`
- 期待結果: Wan 5B動作下書き→解放→Animate 2を自動実行。
- 実際の結果: 動作下書き約74.3秒、全工程約411.6秒で成功 ✅
- 出力: H.264 / 480×832 / 16fps / 81 frames / 5.0625秒。
- 中間フレームでピンク髪、黒と金の衣装、機械腕を維持し、
  指定した片手を振る姿勢を目視確認 ✅

UI検証:

- 4584×2736の模擬横三面図から中央を選択。
- 期待結果: 1面だけを適切な上限サイズへ切り出す。
- 実際の結果: 858×1536 PNGへ切り出し成功 ✅
- 動作MP4のmultipart upload、履歴表示、進捗`motion`段階を確認 ✅
- API/ComfyUIは引き続き`127.0.0.1`限定。

## 2026-11-19 動画追従不良の再調査と調整

### 原因

- Job `468a3703-3eda-4db5-a0c6-c2a55a874649`の入力は
  256×1024の細長いキャラクター切り出しだった。
- Animate workflowの480×832中央クロップにより上下が大きく欠け、
  完成動画は顔の接写になって全身動作が見えなかった。
- ひとつ前の入力には別ビュー、詳細図、文字が混在し、単一の
  キャラクター参照として成立していなかった。
- 自動動作動画にも完成映像用promptをそのまま渡していたため、
  動作だけでなく別キャラクターと背景まで生成していた。
- 全ジョブで同一Seedが初期入力され、失敗構図を再生成しやすかった。

### 修正

- 参照画像をモデルと同じ480:832比率のキャンバスへ`contain`配置し、
  全身を切らずに余白で補正するようにした。
- 元画像の上端背景色を余白色に使い、切り出し境界を目立ちにくくした。
- 切り出し結果のプレビュー、横位置、切り出し幅の調整を追加した。
- 完成映像promptと動作promptを分離。動作側は外見、動物種、服装、
  背景を除外した全身の中立的performer、固定カメラとして生成する。
- Animate本体のpromptへ全身、固定カメラ、no zoom/no close-upを追加。
- Seedは空欄を既定とし、生成時に毎回ランダム化するようにした。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `POST /jobs`（Wan Animate 2、白いうさぎ、動作動画なし） | 全身参照を維持し、専用動作ガイドから両腕を振るダンスを転写 | Job `665cb2e4-1b57-4761-8084-a8832d82346a`が約420.6秒で成功 ✅ |
| 0.5秒、2.5秒、4.5秒をブラウザーで目視 | 顔の接写ではなく全身と動作変化を表示 | 全身、両脚、左右の腕の位置変化を確認。従来の接写問題は解消 ✅ |
| `python3 -m py_compile automation/app.py` | Python構文エラーなし | 成功 ✅ |
| `automation-env/bin/python -m pip check` | 依存関係エラーなし | `No broken requirements found.` ✅ |
| `ss -ltn` | API/ComfyUIがloopback限定 | `127.0.0.1:8001`、`127.0.0.1:8188`のみ ✅ |

今回のうさぎ元画像自体は、頭の左右と腕が切れた細い画像であるため、
終盤に境界と手足の変形が残った。これはpromptでは復元できない。
元の三面図を再アップロードし、プレビューで頭・両腕・両脚が欠けない
幅へ広げるか、正面の単一全身画像を使う必要がある。公式
`WanAnimate2ToVideo`は参照画像の先頭1枚だけを使うため、三面図3枚の
同時参照、非人型キャラクター、複雑な接触動作には引き続き限界がある。

## 2026-11-19 LTX-2.5送信エラーの修正

- 実行コマンド: 直近ジョブ、APIログ、同じ`POST /jobs`入力を確認。
- 期待結果: LTX生成処理または入力検証のどちらで失敗したかを特定。
- 実際の結果: 直近3回はすべて動画処理前のHTTP 422で、
  `ltx_license_acknowledged=false`が原因だった。LTXモデルの実行エラー
  ではないことを確認。
- 修正: LTX選択時の確認欄を「必須」と明示し、未確認なら画像アップロード
  やAPI送信より前に日本語で案内して停止するようにした。
- ブラウザー試験: 未チェックで「動画を生成」を押す。
- 期待結果: 不要な画像アップロードやHTTP 422を発生させず、操作方法を表示。
- 実際の結果:
  `LTX-2.5を使用するには、下の必須ライセンス確認欄をチェックしてください。`
  と表示し、API送信前に停止 ✅

## 2026-11-19 三面図3ファイル入力

- 実行コマンド: Wan Animate 2画面を開き、入力欄とAPI schemaを確認。
- 期待結果: 正面・側面・背面を個別登録し、生成に使う面を選択できる。
- 実際の結果: 3つの選択・アップロード欄と
  「今回の生成に使用する面」を追加し、画面表示を確認 ✅
- 実装: 3画像を480:832へ個別に余白補正して保存し、
  `reference_front`、`reference_side`、`reference_back`としてジョブへ記録。
  選択した1面をWan Animate 2の`reference_image`へ渡す。
- DB migration: 既存ジョブを維持したまま3列を追加し、API応答で確認 ✅
- 構文・依存: `python3 -m py_compile automation/app.py`、
  `automation-env/bin/python -m pip check`ともに成功 ✅

公式`WanAnimate2ToVideo`は`reference_image[:1]`だけを使用するため、
3枚を同時合成・同時参照はしない。横に結合すると3人として誤認するので、
動作開始時の向きに合う1面を選ぶ方式とし、この制約をUIへ明記した。

## 2026-11-19 画質優先・可逆保存モード

- UIへ「標準・高速」「画質優先」の切替ボタンを追加。
- 画質優先ではモデル別に生成解像度を上げ、`CreateVideo`を8-bit
  sRGB / 4:4:4 / tensor維持、`SaveVideo`をH.264 CRF 0へ設定。
- 完全なraw無圧縮ではなく、生成画素を劣化させない可逆圧縮MP4とした。
  ブラウザー再生互換性を保ちながら容量を抑えるため。
- Wan 5B: 1280×704 / 121 frames / 24fps / 20 steps。
- Wan 14B: 704×704、LTX-2.5: 0.5MP、Wan Animate 2: 576×992。
- `quality_mode`をDBとAPIへ追加し、履歴カードにも表示。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| UIの「画質優先」から一般的な風車映像をWan 5Bで生成 | 高解像度・可逆保存で完了 | Job `e2d8b23d-d101-4407-a37a-2a1c876c6c71`が約321秒で成功 ✅ |
| コンテナ内`ffprobe` | 1280×704、24fps、121 frames、4:4:4 H.264 | High 4:4:4 Predictive / `yuv444p` / 39,250,228 bytes ✅ |
| ブラウザー動画要素を確認 | CRF 0 MP4をエラーなく再生 | duration 5.041667秒、readyState 4、errorなし ✅ |
| `python3 -m py_compile automation/app.py` / `pip check` | 構文・依存エラーなし | 成功 ✅ |

画質優先は標準モードより生成時間、統合メモリ、保存容量を多く使う。
元画像や生成モデル自体にない細部を復元する超解像処理ではない。

## 2026-11-20 10秒・15秒動画の連続生成

### 原因

従来の`shots=2/3`は、約5秒の動画を独立したSeedと初期状態で複数生成し、
最後にMP4を単純連結していた。そのため各境界で構図と動作がリセットされた。

### 修正

- Wan 5B / Wan 14Bで、各区間の終端フレームをFFmpegで抽出。
- 抽出画像を次区間の`start_image`へ接続し、同じ状態から続きを生成。
- Qwen計画へ「1つの連続テイク」「リセット、カット、瞬間移動、
  開始ポーズ反復、被写体・背景・照明・カメラ変更なし」を追加。
- UIの「ショット数」を「動画の長さ」へ変更し、約10秒・約15秒を
  「連続生成」と明記。
- 内部継続フレームは一般の参照画像一覧から除外。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| Wan 5B / 2区間で一般的な青いボールの動画を生成 | 第1区間の終端を第2区間へ引き継ぐ | Job `9b287c05-e77f-4f4a-892c-3543dc24c26c`が約158秒で成功 ✅ |
| ジョブイベントを確認 | 区間1→終端抽出→区間2の順に処理 | `continuing: Passing segment 1 end frame to segment 2`を確認 ✅ |
| 完成MP4をブラウザーで確認 | 約10秒の単一動画として再生 | duration 10.125秒、readyState 4 ✅ |
| 5.00秒と5.15秒を目視比較 | 境界で初期場面へ戻らない | 同じ白い床、カメラ、青いボール位置を維持 ✅ |
| 6.00秒を目視確認 | 接続後も動作を継続 | 同じ場面内でボールの移動を確認 ✅ |

開始画像の引継ぎでハードリセットは解消するが、生成モデルの性質上、
長時間では形状や細部が徐々に変化する可能性は残る。

## 2026-11-20 動画生成キャンセル

### 実装

- 実行中ジョブ画面へ「生成をキャンセル」ボタンを追加。
- `POST /jobs/{job_id}/cancel`を追加し、ジョブ状態を`cancelled`へ更新。
- 実行中の対象ジョブだけComfyUI `/interrupt`を呼び出す。
- 待機中ジョブのキャンセルでは、別の実行中ジョブを停止しない。
- 計画、動作下書き、各連続区間、仕上げの境界でもキャンセル状態を確認。
- キャンセル後はOllamaとComfyUIモデルを解放し、部分出力を完成扱いにしない。
- 履歴へ「キャンセル済み」を表示し、次の生成ボタンを再度有効化。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 実行中の一般的な紙飛行機ジョブへ`POST /cancel` | ComfyUIを中断して`cancelled`になる | Job `12931d49-2ab6-447e-bc08-94d6efb60f03`を中断、outputなし ✅ |
| キャンセル後に`/queue`とOllama `/api/ps`を確認 | キューとモデルが残らない | `queue_running: []`、`models: []` ✅ |
| UIから一般的な雲の生成をキャンセル | ボタンが「キャンセル中…」を経て履歴へ反映 | Job `0d5da44e-b5ad-48b7-bead-7dcf29420394`が「キャンセル済み」になり再生成ボタン復帰 ✅ |
| 実行中Aの後ろに待機Bを投入し、Bだけキャンセル | Aを中断しない | B `df39c21f-610d-4c18-9dd5-87e4929dd590`のみ取消、Aは引き続き`generating` ✅ |
| 続けてテスト用Aもキャンセル | 全テスト終了後に資源解放 | ComfyUIキュー空、Ollamaモデルなし ✅ |

切替時に古いAPIプロセスがPIDファイルと不一致で残っていることも検出した。
既存ユーザー生成の完了を待ってから重複プロセスを停止し、キャンセルAPIを
含む単一プロセス（PID 3103980）へ統一した。

## 2026-11-20 履歴（動画ログ）削除機能

- `DELETE /jobs/{job_id}` を追加。完了・失敗・キャンセル済みのみ削除可能（実行中・待機中は409）。
- 削除対象: `jobs`/`events` 行、`output/jobs/<job_id>/`、`input/continuation-<job_id>-*.png`、`input/motion-guide-<job_id>.mp4`。アップロード済み参照画像は残す。
- UI: 履歴カードと結果表示に「削除」ボタン（確認ダイアログ付き）。
- APIを単一プロセス（PID 3160200）で再起動（ComfyUIキュー空を確認後）。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `py_compile` / `pip check` / ReadLints | エラーなし | すべて問題なし ✅ |
| 一般的な紙の舟ジョブ（wan5b, 1ショット）実行中に`DELETE` | 409 | `Cancel the job before deleting it` HTTP409 ✅ |
| 不正ID・存在しないUUIDへ`DELETE` | 404 | 両方 HTTP404 ✅ |
| 完了後に`DELETE /jobs/b70df0fd-49d2-4799-a666-39dd72f4dffb` | DB行と出力が消える | HTTP200、`output/jobs/<id>`消滅、jobs 0件・events 0件、GETは404 ✅ |
| 既存ジョブ一覧の確認 | ユーザーの既存ジョブは残る | 直近3件（succeeded）そのまま ✅ |

## 2026-11-20 指示追従の改善（工程保持・区間割当・14B精度優先）

- プランナーを「scene（開始状態）＋順序付き steps」に変更。依頼文を句点・`→`・`その後`・`then` 等で節に分割し、節が2つ以上なら Qwen に同数・同順の steps を返させる（工程の欠落・結果状態からの開始を防止）。
- Qwen の2回目呼び出しで「欠落・順序・scene が後の結果を示していないか」を監査し、不合格なら1回再生成。未解決なら進捗に警告。
- steps を区間へ順に割当て、各区間プロンプトに「完了済みの工程」と「この区間の工程のみ」を記述。
- 開始画像ありのエンジンでは外見・構図を Qwen に書かせず固定文を使い、ネガティブにカット・転場・急ズーム・顔の変化を追加。
- Wan 14B「動作精度優先」: 公式テンプレートのスイッチ `129:131=false`（20ステップ・CFG 3.5・LightX2V LoRAなし）。追加ダウンロードなし。
- UI: 14B選択時のみ精度優先チェック、進捗に `工程:` 行、履歴に「精度優先」。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 旧方式プランナーで竜の戦士3工程（14B精度優先・2区間, Job `9529bf2b-…`） | 3工程が残る | ❌ 工程1「剣を掲げる」が scene に移され欠落、監査も scene 不合格。構図指定の衝突で脚のアップへのカット、終盤に顔が人間化。1,478秒、ピーク 63,429 MiB |
| 節固定方式で同じ依頼を `create_plan` 単体実行 | 3節→3工程 | 工程1〜3がすべて順に残り、監査合格 ✅ |
| 猫3節（T2V）を単体実行 | 監査の誤検出なし | 初回は静止姿勢を「結果状態」と誤判定 → 監査基準を修正後は合格 ✅ |
| 竜の戦士 14B精度優先・2区間（Job `0571a204-19d9-41d0-9cc5-87d6681586a2`） | 掲げる→突き立てる→片膝の順、カット・顔変化なし | 掲げる・下ろす・片膝の順に出現、竜の外見を維持、脚アップのカットなし ✅。各区間約12分 |
| バラ（状態変化あり）Wan 5B・2区間（Job `d57ed266-2b0b-4951-b467-920382668a78`） | 抜く→胸に抱える→窓を開けるの順 | 工程3件が順に表示・映像も順序どおり ✅。約2分40秒 |
| 2本連続実行中のピークメモリ | 128GB 未満 | 61,838 MiB ✅ |

## 2026-11-20 人物の正面・背面融合を修正

前節のバラ動画について、工程順の抜き出し画像だけで合格とした評価を訂正する。
全体を5フレーム間隔で再確認すると、女性の顔・肩・胴体の向きが一致せず、
正面と背面が融合したような破綻があった。工程順が正しいだけでは受入不可。

- 次区間へ「完了済みの動作」を再記述せず、前区間の可視終了状態だけを渡す。
- Qwen計画へ各工程の終了状態、固定カメラ方向、手・保持物の物理整合性を追加。
- 正面・左右横・背面のいずれか1方向へ固定し、頭・肩・胴・腰・膝・足の
  向きを揃える。
- ネガティブへ前後融合、胴体反転、頭部180度反転、関節ねじれ、重複肢を追加。
- Animate 2は選択した1面と同じ方向へ動作ガイドを固定。公式ノードは参照画像を
  1枚しか使えないため、正面から背面へ回転する動作はUIで非対応と明記。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 旧バラ動画 `d57ed266-…` を5フレーム間隔・33枚で確認 | 身体方向が一貫 | ❌ 顔・肩・胴体の方向不一致を確認 |
| 状態付きプランナーを同じ依頼で単体実行 | 工程・終了状態・身体方向・手の使用が整合 | 3工程、各終了状態、物理整合性監査が全項目true ✅ |
| 同じ依頼・同じSeed 11・Wan 5B 2区間で再生成（Job `939b8f47-d7a7-4750-841a-37b0d41d2297`） | 前後融合なし、固定構図、工程継続 | 166秒で成功。5フレーム間隔の全体確認で自然な横向き姿勢を維持し、前後融合・胴体反転なし ✅ |
| Animate用動作プロンプトをfront面で単体生成 | 全身正面を固定 | `face, chest, hips, knees, and feet pointing toward the camera` を確認 ✅ |
| 生成中ピークメモリ | 128GB未満 | 63,340 MiB ✅ |

## 2026-11-20 完成動画からの続き生成

- DBへ`continuation_of`を追加し、完成したWan 5B / Wan 14Bジョブを
  親として指定可能にした。
- 受付時に親動画の正確な最終フレームをPNGへ抽出。親を後から削除しても
  開始画像は失われない。
- モデル、画質優先、Wan 14B精度優先を親から継承する。
- プランナーへ親の最終可視状態を渡し、過去の動作を繰り返さず新しい指示
  だけを実行する。
- 続き部分の生成後、親動画と`-c copy`で連結して累積`extended.mp4`を作成。
  延長済み動画をさらに延長できる。
- 続きが待機中・実行中の間は親ジョブの削除を409で拒否する。
- UIの完成動画画面へ「この動画の続きを作る」を追加。モデルを固定し、
  追加する長さだけ選択可能。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| ブラウザーで青いボール完成ジョブから「この動画の続きを作る」 | 続きモード、同じモデル、親と同じ追加長を表示 | 見出し・説明・無効化したWan 5B・約10秒・解除ボタンを確認 ✅ |
| Wan 5B親をWan 14Bで継続するAPI要求 | 同一エンジン制約で拒否 | HTTP409 `A continuation must use the same video engine` ✅ |
| 親`9b287c05-…`から続き`7bcf521c-…`を受付 | 最終フレームを子ジョブへ保存 | output/inputの両PNGを作成、親の真の最終フレーム（frame 161）とSHA256完全一致 ✅ |
| 続き実行中に親へ`DELETE` | 親を保護 | HTTP409 `Wait for or cancel the active continuation...` ✅ |
| Wan 5B 10.125秒へ5.0625秒を追加 | 1本の約15秒動画 | 86秒で成功、`extended.mp4` 15.1875秒・243フレーム ✅ |
| 親最終フレームと続き先頭を比較 | 接続で場面リセットしない | SSIM 0.991411、PSNR 40.540629 dB。境界前後8フレームで同じ床・壁・青いボールを維持 ✅ |
| 実行中ピークメモリ | 128GB未満 | 61,403 MiB ✅ |

## 2026-11-21 品質優先マルチバックエンドからの完全復元

11/21の品質ゲート／LTX worker 改修を取り消し、11/20 完成動画からの続き生成まで
のコードと実行構成へ戻した。ジョブ動画・`data/jobs.sqlite3` は削除していない。
Git 履歴（`936987e`〜`cb72aa6`）は残しており、作業ツリーだけを復元した。

削除した品質優先専用パス: `automation/backends/`、`automation/quality/`、
`workers/ltx/`、`tests/`、`benchmarks/`、`Dockerfile.ltx`、
`docker-compose.offline.yml`、品質ゲート／LTX worker 用スクリプト、
同日の LTX smoke / 受入ログ。`.dockerignore` から LTX worker 用許可を外した。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 15ファイルの作業ツリーが 11/20 相当であること | `continuation_of` あり、品質ゲート import なし | `automation/app.py` 1999行、`continuation_of` あり、backends/quality 参照なし ✅ |
| 品質優先専用ファイルを削除 | 混在ファイルが残らない | 対象パス削除済み。残件スキャン 0件 ✅ |
| `docker stop/rm unbound-ltx-worker` | ComfyUI のみ残る | `unbound-comfyui` のみ Up / healthy。LTX worker コンテナ削除 ✅ |
| `python3 -m py_compile automation/app.py` | 構文エラーなし | `COMPILE_OK` ✅ |
| 品質優先 API (PID 1228186) を停止し `scripts/start-api.sh` | 127.0.0.1:8001 で旧API | PID 1233113。`/health` は `service`/`ollama`/`comfyui` のみ（`ltx` なし）。UI 200 ✅ |
| ComfyUI キュー | 空 | `queue_running`/`queue_pending` とも空 ✅ |

## 2026-11-22 続き生成の計画段階 JSONDecodeError 対策

- 症状: 続きジョブ `a93ff964-…`（Wan 14B、親 `b4b7fbe1-…`）が計画段階で
  `JSONDecodeError: Unterminated string starting at: line 3 column 5 (char 21)` で失敗。
- 原因: プラン監査（`plan_problems`、`num_predict=384`）の出力 `{"missing": ["…` の
  1要素目が上限まで伸び、閉じていない JSON のまま返った。Qwen の出力ゆらぎで
  時々起き、同じ入力の再実行3回ではすべて正常だった。
- 対策: `ollama_structured` で `done_reason == "length"` と不正 JSON を検出し、
  `num_predict` を倍増・温度を少し上げて最大3回再試行。`missing` に
  `maxItems` を設定。監査が3回とも壊れた場合は、検証済みのプランで続行し
  `check.audit_error` に記録する（ジョブ全体は失敗させない）。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 失敗ジョブと同じ入力で `create_plan` を実 Qwen で3回 | 再現確認 | 3回とも `done_reason=stop`、成功（非決定的な失敗） |
| 打ち切り→不正JSON→正常 を模擬 | 3回目で成功 | `num_predict` 384→768→1536 で再試行し成功 ✅ |
| 3回とも壊れた応答を模擬 | 元のエラー文を含む RuntimeError | 同一の `Unterminated string … (char 21)` を再現 ✅ |
| 監査だけ3回壊れる状況で `create_plan` | プランは返る | `check.audit_error` 付きで成功 ✅ |
| API 再起動後、失敗ジョブの入力で実 Qwen 計画 | 成功 | PID 1993493、計画成功 ✅ |

## 2026-11-22 続き生成の計画段階 Ollama CUDA error 対策

- 症状: 続きジョブ2件（03:42, 03:45 JST、ユーザー削除済み）が計画段階で
  `HTTP 500 … CUDA error: an internal operation failed` で失敗。
- 原因: Ollama のログで、モデル読み込み直後の prefill 中に
  `ggml_cuda_mul_mat_cublas_impl` で異常終了していた。CUDA 空きは約 112GB、
  ComfyUI はアイドルだった。GB10 (sm_121) の MoE モデルで、特定のプロンプト
  形状のときに cuBLAS が落ちる既知の不具合（llama.cpp #28377）。
  プロンプト依存なので、同じ依頼は何度やっても落ちる。
- 対策: Qwen 呼び出しへ `options.num_batch=256` を付け、llama-server を
  `-b 256 -ub 256` で起動させる（環境変数 `OLLAMA_NUM_BATCH` で変更可）。
  HTTP 500 の CUDA エラーも既存の3回再試行に含める。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `journalctl -u ollama` | 失敗位置の特定 | 2回とも prefill の cuBLAS GEMM で abort ✅ |
| `num_batch:256` 付きで `/api/generate` | ランナー引数が変わる | `-b 256 -ub 256` で起動 ✅ |
| 模擬 CUDA 500 → 実 Qwen で `create_plan`（続き・Wan14 親） | 再試行で成功 | 11.7秒で成功、CUDA error 0件 ✅ |
| API 再起動 | 127.0.0.1:8001 | 起動・待受確認 ✅ |

未確認: 失敗した2件の依頼文は削除済みで、元のプロンプトそのものでの再現・回避確認はしていない。

## 2026-11-22 画像生成（Z-Image Turbo）と「この画像から動画を作る」

- ユーザー選択: Z-Image Turbo bf16、画像生成＋動画への連携まで。
- モデル: `Comfy-Org/z_image_turbo` @ `6fc90a3b…`、Apache-2.0（配布元・元モデルとも）。
  3ファイル 20,690,152,836 bytes。サイズと SHA256 は HF tree API の LFS oid。
- ワークフロー: 公式 `image_z_image_turbo.json`（templates-json 0.1.131）を
  `workflows/` へ保存し、同じ構成の API 版 `image_z_image_turbo.api.json` を作成
  （8ステップ、cfg 1、res_multistep / simple、AuraFlow shift 3）。
- 実装: エンジン `zimage`、DB 列 `image_size`（square 1024², landscape 1280×720,
  portrait 720×1280）、Qwen による静止画プロンプト変換、PNG のメタデータ除去、
  `input/generated-JOB_ID.png` への登録、UI の画像表示と「この画像から動画を作る」、
  削除時の生成画像の片付けと使用中ジョブがある場合の 409。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `getent hosts huggingface.co` / `curl https://1.1.1.1` | 外部接続 | ❌ ホストがオフライン。モデルは未ダウンロード |
| API版ワークフローを ComfyUI `/prompt` で検証 | モデル未取得以外のエラーなし | エラーは unet/clip/vae の `value_not_in_list` の3件のみ ✅ |
| tEXt（prompt/workflow）付き PNG を `ffmpeg -map_metadata -1` | テキストチャンク除去 | `IHDR,tEXt,tEXt,IDAT,IEND` → `IHDR,pHYs,IDAT,IEND`、prompt 文字列なし ✅ |
| `create_image_prompt` を実 Qwen で2件 | 依頼にない要素を足さない英語プロンプト | 9.2秒・2.6秒で成功 ✅ |
| `POST /jobs` に zimage + shots=2 / precision / 不正サイズ | 422 | 3件とも 422 ✅ |
| ブラウザーで Z-Image を選択 | 縦横比表示、動画用項目非表示、「画像を生成」 | 確認 ✅ |
| 一時的な完成画像ジョブで「この画像から動画を作る」 | Wan 14B、開始画像に generated-… を選択 | `engine=wan14`、`existingImage=generated-c831b9ff-….png` ✅ |
| 待機中の動画が使用中の生成画像を `DELETE` | 409 | 409 ✅ |
| 一時ジョブを `DELETE` | DB・出力・input コピーが消える | 行・イベント・`output/jobs/…`・`input/generated-…` とも削除 ✅ |

未確認: 実モデルでの画像生成（所要時間・ピークメモリ・画質）と、生成画像からの
動画生成の通し確認。確認方法: ネットワーク接続後に
`hf-env/bin/python scripts/download-models.py zimage` を実行し、UI から1枚生成して
Wan 14B で動画化する。

## 2026-11-22 Z-Image Turbo 取得と画像→動画の通し確認

- ネットワーク: Wi-Fi `Pixel_3494`（ユーザーが接続）。有線 `enP7s7` は Mac 直結のみ。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `hf-env/bin/python scripts/download-models.py zimage` | 3ファイル取得、サイズ・SHA256一致 | 05:34〜06:49（75分）。途中で接続切断・タイムアウトが数回あったが自動再開。3ファイルとも `OK`（MANIFEST の SHA256 と一致） ✅ |
| ComfyUI `object_info` | 新モデルを認識 | `z_image_turbo_bf16.safetensors`、`qwen_3_4b.safetensors` を認識 ✅ |
| UI/API で画像ジョブ（公園のベンチに座る赤いジャケットの男性、正方形、Seed 20261122）。Job `df332b49-…` | 1枚の PNG、メタデータなし | 22.0秒で成功（Qwen 11秒、生成10秒）。依頼どおりの全身像。`image.png` と `input/generated-…png` は `IHDR,pHYs,IDAT,IEND` のみ ✅ |
| 画像ジョブ中のメモリ（`free -m` 1秒間隔） | 128GB未満 | 待機 12.9GB → ピーク 57.2GB ✅ |
| 生成画像を開始画像に Wan 14B 4ステップ（立ち上がって手を振る）。Job `cca83380-…` | 5秒動画、人物維持 | 114.1秒で成功。640×640、16fps、81フレーム、5.06秒。座る→立つ→手を振るの順、顔・服装・背景を維持 ✅ |
| 動画ジョブ中のメモリ | 128GB未満 | 待機 13.8GB → ピーク 56.7GB ✅ |

補足: ComfyUI が直接書き出す中間 PNG（`image_zimage_00001_.png`）にはプロンプトの
`tEXt` が残る。外部へ出すのは除去済みの `image.png` を使う。

## 2026-11-22 服装変更（FLUX.2 [klein] 4B）の実装

- 目的: 人物画像の服装を、指定した服の画像に差し替えた参照画像を作り、
  Wan Animate 2 で動作元 MP4 どおりに動かす。動画内の部分置換ではない。
- モデル選定: FLUX.2 [klein] 4B 蒸留 FP8（追加約4.4GB、Apache-2.0、2画像参照の
  公式テンプレートあり、テキストエンコーダは Z-Image と共通）を採用。
  Qwen-Image-Edit-2511（追加約31GB）は見送り（ユーザー選択）。

### 実行コマンド → 期待結果 → 実際の結果

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| コンテナ内 `comfyui-workflow-templates-json` 0.1.131 の編集テンプレート一覧 | 2画像参照の編集テンプレート | `image_flux2_klein_image_edit_4b_distilled.json`（2画像版サブグラフあり）を確認、`workflows/` に複製（sha256 `c5e2438f…`） ✅ |
| HF API（WebFetch。ホストはオフライン） | サイズ・SHA256・ライセンス | 本体 4,070,624,520 B、VAE 336,213,556 B。本体 `LICENSE.md` 原文は Apache 2.0。VAE は公式 GitHub で Apache 2.0 と明記 ✅ |
| 2画像サブグラフを平坦化した `workflows/image_flux2_klein_outfit_edit.api.json` を `/object_info` と照合 | 必須入力の欠落・余分なし | 問題なし ✅ |
| `python3 -m py_compile automation/app.py scripts/download-models.py` | 構文エラーなし | 成功 ✅ |
| API 再起動後、`garment_image` なし / 存在しないファイル / 動画エンジンで短い concept | 422 / 404 / 422 | 422 / 404 / 422 ✅。DB に `garment_image` 列が追加された ✅ |
| Z-Image で検証用の服画像（赤いカーディガン、人物なし・ロゴなし）と人物画像（AIオリジナル男性の全身正面）を生成 | 検証素材 | `generated-b26be098-….png`、`generated-0966c196-….png` ✅ |
| `nmcli connection up Pixel_3494` | Wi-Fi 接続 | `Not authorized to control networking`。ホストはオフラインのため、モデル取得はユーザーの Wi-Fi 接続待ち |

## 2026-11-23 FLUX.2 [klein] 4B 取得と服装変更→Animate 2 の通し確認

- ネットワーク: Wi-Fi `Pixel_3494`（ユーザーが接続）。

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `hf-env/bin/python scripts/download-models.py klein` | 本体・VAE を取得、qwen_3_4b は既存検証 | 00:11〜00:29（17分）。本体取得中に切断・タイムアウト3回、自動再開。3ファイルとも `OK`（MANIFEST の SHA256 と一致） ✅ |
| `outfit` ジョブ（人物 `generated-0966c196-…`、服 `generated-b26be098-…`、指示なし、Seed 20261123）。Job `9bbb8f5b-…` | 服だけ赤いカーディガンになり、他は維持 | 12秒で成功（編集10秒）。576×1024。紺Tシャツ→赤カーディガン（白ボタン・Vネック再現、中に白T）。顔・髪・チノパン・靴・ポーズ・背景を維持 ✅ |
| 同ジョブ中のメモリ（`free -m` 1秒間隔） | 128GB未満 | 待機 13.0GB → ピーク 27.5GB ✅ |
| 出力 PNG のチャンク確認 | プロンプトなし | `image.png`・`input/generated-…png` とも `IHDR,pHYs,IDAT,IEND`、指示文なし ✅ |
| 編集画像＋`drive_doll_street_dance.mp4`（5.2秒・24fps のストリートダンス）で `animate2`、Qwen 有効。Job `4347cd00-…` | 動作元どおりに踊り、服装を維持 | 372秒で成功（Qwen 25秒）。480×832、24fps、81フレーム、3.4秒。腕を広げる・膝を曲げる・蹴り上げる動きを転写し、全フレームでカーディガンを維持 ✅ |
| 同ジョブ中のメモリ | 128GB未満 | 待機 13.0GB → ピーク 57.2GB ✅ |
| ブラウザーで服装変更ジョブを開き「この画像を動作元動画どおりに動かす（Wan Animate 2）」 | Animate 2 に切替、編集画像を選択 | `engine=animate2`、`existingImage=generated-9bbb8f5b-….png`、`characterView=whole`、参照プレビュー表示 ✅ |

補足: Animate 2 の背景は参照画像（スタジオ）になり、動作元動画の街並みは使われない。
動作元動画の中の人物だけを着せ替える部分置換は、現行ノードでは未対応。

## 2026-11-23 Animate 2 の動作元動画の開始位置指定

- 背景: YouTube リンク取り込みは YouTube 利用規約（公式機能以外でのダウンロード禁止）と
  権利面から実装しない。代わりに、保存済み MP4 から使う区間を選べるようにした（ユーザー選択）。
- Animate 2 の使用長は 81 フレーム固定のため、指定するのは開始位置のみ。

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| `POST /jobs` に wan14 + `motion_start` | 422 | 422 ✅ |
| `trim_driving_video('trimtest', drive_doll_street_dance.mp4, 1.5)` | 1.5秒以降を再エンコード | 88フレーム・3.67秒・24fps ✅ |
| 同 3.0 秒開始 | 81フレーム未満でエラー | `only 52 frames … needs 81` ✅ |
| コンテナから `input/` へ直接書き込み | — | cap_drop ALL のため不可（Permission denied）。ジョブフォルダへ書き出してホストが `input/motion-trim-…mp4` へコピーする方式にした |
| 服装変更画像 + 同動画 `motion_start=1.5` で animate2。Job `e691e2f8-…` | 1.5秒以降の動きを転写 | 354秒で成功（切り出し1秒）。結果の0/1/2秒が元動画の1.5/2.5/3.5秒の姿勢と一致 ✅ |
| ブラウザーで MP4 選択 → 1.2秒で「今の再生位置から使う」/ 3.0秒 / 9秒 | プレビュー表示、開始=1.2、残り不足の注意、範囲外の案内 | すべて表示どおり ✅ |

## 2026-11-23 動作元 MP4 指定時の指示文を任意化

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| animate2・動作元なし・指示文空 / wan14・指示文空 | 422 | 422 / 422 ✅ |
| 服装変更画像 + `drive_doll_street_dance.mp4`（1.5秒から）、指示文空で animate2。Job `42db484e-…` | Qwen を使わず固定プロンプトでトレース | 337秒で成功。`Tracing the motion video without a prompt`、Qwen 工程なし。結果0/1/2秒が元動画1.5/2.5/3.5秒の姿勢と一致し、指示文ありの結果（Job `e691e2f8-…`）と同等 ✅ |
| ブラウザーで Animate 2 を選択 → MP4 を指定 → Wan 5B に戻す | 必須 → 任意 → 必須 | `required` true → false（「場面や雰囲気の補足（任意）」）→ true ✅ |

## 2026-11-23 側面・背面画像が未選択のとき Animate 2 が 422 になる不具合

- 症状: UI から Wan Animate 2 を実行すると `String should match pattern '^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$'`。
- 原因: 側面・背面画像が未選択のとき、UI が `null` ではなく空文字 `""` を送り、ファイル名パターンで拒否されていた（初期スナップショット `936987e` からの既存不具合）。
- 修正: API で空文字のファイル名を未指定（None）として扱う `mode="before"` バリデータを追加。UI も未選択時は `null` を送る。

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| 修正前: `reference_side: ""` / `garment_image: ""` で `POST /jobs` | 再現 | 両方ともパターン違反の 422 ✅（再現） |
| 修正後: `reference_side: ""`, `reference_back: ""` で `POST /jobs`（存在しない動作元を指定） | 受付後、動作元なしで失敗 | `queued` → `Driving video does not exist`、`reference_side` は `''` で保存 ✅。テストジョブは削除 |

## 2026-11-23 すべての動画モデルで「続きを作る」に対応

- 対象を Wan 5B / 14B から LTX-2.5 と Wan Animate 2 にも拡大。
- LTX-2.5: 親の最終フレームを開始画像にして生成し、親と `-c copy` で連結（音声も連結）。ライセンスの来歴情報除去禁止に配慮し、`extended.mp4` は親の埋め込みメタデータを `-map_metadata 1 -movflags use_metadata_tags` で引き継ぐ。
- Wan Animate 2: 親のキャラクター画像を再利用し、最終フレームを `continue_motion`、`video_frame_offset` に親の終了位置を渡す。ノード出力4（trim_image）で重複1フレームを除いてから連結。動作元は「空欄なら親の動作元MP4の続き」「新しいMP4を指定するとその開始位置から（親のfpsへ変換）」。動作ガイド由来の親では次の動作の指示文が必要。
- UI: 完成動画すべてに「この動画の続きを作る」を表示。Animate 2 の続きでは動作元MP4欄だけを表示し、指示文は任意。

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| LTX 続き・ライセンス確認なしで `POST /jobs` | 422 | 422 ✅ |
| Animate 2 親を wan14 で続ける | 409 | `A continuation must use the same video engine` ✅ |
| LTX 新規（服装変更画像・手を振る）Job `cdb2f0a7-…` | 成功 | 51秒・ピーク約59.4GB・640×640・16fps・81フレーム＋音声 ✅ |
| 上記の続き（一歩踏み出して腰に手）Job `182928fc-…` | 連結動画 | 43秒・ピーク約59.0GB。`extended.mp4` 10.125秒・162フレーム・音声10.13秒 ✅ |
| 同 `extended.mp4` のメタデータ | 親の `prompt` タグを保持 | 初回は concat demuxer で消失 → 親を2入力目にして `-map_metadata 1 -movflags use_metadata_tags` に修正し再連結、保持を確認 ✅ |
| Animate 2 親 `4347cd00-…` の続き・動作元空欄・指示文空。Job `f6e26278-…` | 元MP4の81フレーム目から継続 | 343秒・ピーク約54.8GB。`Continuing the source motion video from frame 81`、`extended.mp4` 161フレーム（81＋80）・24fps ✅ |
| 同親の続き・新しいMP4（`drive_doll_street_dance.mp4` 0秒から）。Job `c558f699-…` | 指定MP4で継続 | 334秒・ピーク約53.9GB。`Using the motion video from 0.0 s at 24/1 fps`、161フレーム ✅ |
| 3本の境界前後（76〜87フレーム）を一覧化 | 人物・背景・照明が連続 | 3本とも同一人物・同一背景で接続。Animate 2 は元動画の振り付けの続き／新MP4の開始姿勢へ自然に移行 ✅ |
| ブラウザーで Animate 2 完成ジョブの「続きを作る」→解除 | 動作元欄のみ表示、指示文任意、解除で元に戻る | 開始画像・側面画像欄は非表示、`required=false`、解除後は通常表示 ✅ |

- 既知の制約: Wan 5B / 14B / LTX は境界フレームが両方に残る（1フレーム重複、従来どおり）。Animate 2 で元MP4の残りが81フレーム未満だと最後の姿勢で静止する（今回の素材は残り44フレーム）。

## 2026-11-23 Wan Animate 2 で動作元動画を長くトレース

- 動作元 MP4 があるとき、「動画の長さ」で最大6区間（約3/7/10/13/17/20秒、API `shots` 1〜6）を選べるようにした。他エンジンは従来どおり3区間まで、LTX は1区間。
- 2区間目以降は、前区間の最終フレームを `continue_motion`、動作元の位置を `video_frame_offset` に渡す（続き機能と同じ仕組み）。重複1フレームを除くので、合計は 81＋80×(区間数−1) フレーム。
- 動作元が足りない場合は区間数を自動で減らす。新しい動きが24フレーム未満の最終区間は作らない。
- 動作元 MP4 なし（文章から動作ガイド）の場合、2区間以上は 422。
- テスト素材: `drive_doll_street_dance.mp4`（124フレーム）を順再生＋逆再生でつないだ `input/drive_doll_pingpong.mp4`（248フレーム・24fps）。

| 実行コマンド | 期待結果 | 実際の結果 |
|---|---|---|
| animate2・動作元なし・`shots: 3` | 422 | `Longer Wan Animate 2 videos need a motion MP4 to trace` ✅ |
| wan5b・`shots: 4` / animate2・`shots: 7` | 422 | 422 / 422 ✅ |
| 服装変更画像 + `drive_doll_pingpong.mp4`、指示文空、`shots: 6`。Job `b30c9d66-…` | 3区間に自動短縮、241フレーム | 986秒（1区間約5.5分）・ピーク約54.8GB。`generating 3 segment(s)`、480×832・24fps・241フレーム（10.04秒） ✅ |
| 動作元と生成結果の同じフレーム番号（20〜240、境界79〜81・159〜161）を並べて比較 | 全区間で姿勢一致、境界で途切れない | 2・3区間目も元動画の姿勢と一致し、境界も連続 ✅ |
| ブラウザーで Animate 2（動作元なし→MP4指定）→ Wan 14B | 無効→最大約20秒→3択に戻る | 表示どおり。範囲の案内も選んだ長さ（481フレーム）に追従 ✅ |
