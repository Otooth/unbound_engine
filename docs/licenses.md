# モデルライセンス確認

確認日: 2026-11-19

これは実装時に確認した原文の要約であり、法的助言ではない。利用時には
必ずリンク先の最新版を再確認すること。

## Wan 2.2

- 配布元: `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`
- Hugging Faceモデルカード上のライセンス: Apache-2.0
- モデルカード:
  <https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged>
- 元モデルやLoRAを再配布・商用利用する場合は、それぞれの配布元に付随する
  条件も個別に再確認すること。

## Wan Animate 2

- 配布元: `Comfy-Org/Wan-Animate-2`
- 固定revision: `e7181fe1896b7f2ce34120d1a3663413548240d7`
- Hugging Faceモデルカード上のライセンス: Apache-2.0
- モデルカード: <https://huggingface.co/Comfy-Org/Wan-Animate-2>
- 動作元動画に第三者を使用する場合は、モデルライセンスとは別に、
  本人の同意、肖像権、著作権を確認すること。

## Z-Image Turbo（2026-11-22 確認）

- 配布元: `Comfy-Org/z_image_turbo`（固定revision:
  `6fc90a3b1b653e935a0d175e260736de25b84df5`）
- 元モデル: `Tongyi-MAI/Z-Image-Turbo`
- Hugging Faceモデルカード上のライセンス: 配布元・元モデルとも Apache-2.0
- モデルカード: <https://huggingface.co/Comfy-Org/z_image_turbo>、
  <https://huggingface.co/Tongyi-MAI/Z-Image-Turbo>
- 未確認: Apache-2.0 以外の追加利用規約（Acceptable Use 等）の有無。
  確認方法: 元モデルの README・LICENSE 原文を取得して確認する。
- 実在人物・第三者の作品に似た画像を生成して動画化する場合は、モデルの
  ライセンスとは別に、本人の同意・肖像権・著作権を確認すること。

## FLUX.2 [klein] 4B（服装変更、2026-11-22 確認）

- 本体: `black-forest-labs/FLUX.2-klein-4b-fp8`（固定revision:
  `5b4408e59397a4a37ccb46afe426d8ed86379441`）
- ライセンス: Apache-2.0。リポジトリ内 `LICENSE.md` の原文が Apache License
  2.0 本文であることを確認した。公式 GitHub
  <https://github.com/black-forest-labs/flux2> の表でも 4B 系は apache-2.0、
  9B 系と FLUX.2 [dev] は FLUX Non-Commercial License。
- VAE: `Comfy-Org/flux2-dev` の `split_files/vae/flux2-vae.safetensors`
  （固定revision: `9630c1b4b6e690497ae0c19bb6b1b35d4ebedcb7`）。このリポジトリ
  自体のタグは非商用ライセンスだが、公式 GitHub に「The autoencoder is
  released under Apache 2.0」と明記されている。同リポジトリの FLUX.2 [dev]
  本体・Mistral テキストエンコーダは非商用のため取得しない。
- テキストエンコーダ: Z-Image と共通の `qwen_3_4b.safetensors`（Apache-2.0）。
- 9B 版（非商用）は使用しない。
- 人物画像は本人・同意を得た人物・AIオリジナルの人物に限る。服の画像に
  ブランドロゴや第三者のデザインが含まれる場合は、商標・意匠・著作権を別途
  確認すること。

## LTX-2.5

- 配布元: `Lightricks/LTX-2.5`
- ライセンス: LTX-2.x Community License Agreement
- 原文:
  <https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x>
- Acceptable Use Policy:
  <https://static.lightricks.com/legal/ltx-acceptable-use-policy.pdf>
- 確認したライセンス日: 2026-08-11、改定日: 2026-10-29
- 確認したAUP更新日: 2026-11-03

主な条件:

- 年間売上が1,000万米ドル未満のEntityには、条件遵守を前提に限定的な
  ロイヤリティフリーライセンスが付与される。
- 年間売上が1,000万米ドル以上のEntityは、非商用目的だけの利用を除き、
  商用利用前にLightricksとの有償契約が必要。
- 重みや派生モデルを第三者へ配布する場合、ライセンス全文・利用制限の通知、
  変更表示、著作権・帰属表示の保持などが必要。
- 出力、モデル、派生物にはAttachment Aおよび随時更新されるAUPが適用される。
- AI生成物であることを明示する義務がある。
- 同意のない人物のなりすまし・ディープフェイク、児童の搾取、暴力・差別・
  詐欺などは禁止。
- AUPは性的に露骨なコンテンツ、性的行為、性的フェティッシュや
  ファンタジーの生成を禁止している。
- コンテンツ来歴情報、電子透かし、その他のprovenance signalを除去・妨害する
  こともAUPで禁止されている。

したがって、LTX-2.5は本プロジェクトで一般映像の性能比較には使用できるが、
当初想定されている性的に露骨な用途にはライセンス上使用できない。
また、LTX出力に来歴情報が含まれる場合、`strip-metadata.sh`を適用してよいかは
原文と具体的なメタデータの性質を再確認するまで未確認とする。

## Hugging Faceアクセス

`Lightricks/LTX-2.5`はgated repositoryである。取得前にHugging Face上で
ライセンスとプライバシー条件を確認して「Agree and Access」を実行し、
ローカル端末で`hf auth login`を完了する必要がある。トークンをログや
チャットへ貼り付けないこと。
