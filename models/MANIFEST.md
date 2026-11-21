# Model Manifest

Verified model assets used by this project. All weights are SafeTensors files.

## Wan 2.2 TI2V-5B

Source: `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`  
Revision: `ee6f4a40737a995bf5818954cfce6d59443b0f04`

| Local path | Source path | Size (bytes) | SHA256 |
|---|---|---:|---|
| `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors` | `split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors` | 9,999,658,848 | `456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e` |
| `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6,735,906,897 | `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68` |
| `vae/wan2.2_vae.safetensors` | `split_files/vae/wan2.2_vae.safetensors` | 1,409,400,960 | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |

Total: 18,144,966,705 bytes.

The official workflow currently links the text encoder to
`Comfy-Org/Wan_2.1_ComfyUI_repackaged`. The file in that repository has the
same size and SHA256 as the copy pinned above in the Wan 2.2 repository.

## Wan 2.2 I2V-14B (FP8 scaled + LightX2V 4-step LoRA)

Source: `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`  
Revision: `ee6f4a40737a995bf5818954cfce6d59443b0f04`

| Local path | Source path | Size (bytes) | SHA256 |
|---|---|---:|---|
| `diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | `split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | 14,294,742,832 | `6122e79d55e0f235698d11d657f3b196c5273c830da00b2b013c5a048d5e6a42` |
| `diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | 14,294,742,832 | `5471a457b6ac404202a5fbe6c11595a3d5641fc766b00f38763f72303fffc21e` |
| `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` | `split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` | 1,226,977,424 | `d176c808d6fc461999b68e321efcb7501b20b8c3797523ed0df14f7d1deff11e` |
| `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` | `split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` | 1,226,977,424 | `024f21de095bc8fad9809ded3e9e49a2e170dcf27075da8145ba7d60d8aab7f9` |
| `vae/wan_2.1_vae.safetensors` | `split_files/vae/wan_2.1_vae.safetensors` | 253,815,318 | `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |

The 14B workflow reuses the 5B profile's
`text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`.

Total excluding the shared text encoder: 31,297,255,830 bytes.

## Wan Animate 2 (Distilled INT8 ConvRot)

Source: `Comfy-Org/Wan-Animate-2`  
Revision: `e7181fe1896b7f2ce34120d1a3663413548240d7`

| Local path | Source path | Size (bytes) | SHA256 |
|---|---|---:|---|
| `diffusion_models/wan_animate_2_distill_int8_convrot.safetensors` | `diffusion_models/wan_animate_2_distill_int8_convrot.safetensors` | 16,653,175,528 | `d2e566ecac3f164cb1a6b63dc7d868d6678c9affa49fbceb3abe4f9458f89c8a` |
| `clip_vision/clip_vision_h.safetensors` | `clip_vision/clip_vision_h.safetensors` | 1,264,219,396 | `64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161` |
| `vae/Wan2_1_VAE_bf16.safetensors` | `vae/Wan2_1_VAE_bf16.safetensors` | 253,806,278 | `1ab9a32cc2c740f6e39d80d367ce5dcc28db8c71b79b28670546b8973e9d75f9` |

The workflow reuses
`text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` from the Wan 2.2
profile. Total new storage: 18,171,201,202 bytes.

## LTX-2.5 I2V (INT8 ConvRot)

Source: `Lightricks/LTX-2.5`  
Revision: `49b093a39c19169a8a81721dcc541e07e945232f`

| Local path | Source path | Size (bytes) | SHA256 |
|---|---|---:|---|
| `diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` | `diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` | 21,504,034,224 | `c4279eeff115cbeaca494bd2183e7d768c38fe85a184dc6afbb7159157c44334` |
| `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | 15,372,969,374 | `6ce688a0aa98a5fa36a9f1e6c3f42152a498cc2b53ee8c15674c64244f91487f` |
| `vae/ltx-2.5-video-vae-bf16.safetensors` | `vae/ltx-2.5-video-vae-bf16.safetensors` | 1,472,223,346 | `847e14ca7f3355debca0cea4eaa24ac0fbcdf0061da054ac89ca638a869ddba3` |
| `vae/ltx-2.5-audio-vae-bf16.safetensors` | `vae/ltx-2.5-audio-vae-bf16.safetensors` | 364,866,540 | `c52733d37f6a7fb7949c3dc0fb468c6cb2169e4d836983a73babb9f0d54837a5` |
| `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | `latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors` | 995,778,752 | `eb5a71fe4068ee87ccdb1c3aa635e547ca76bd2d30ae20ae889f2c325c0677e8` |

Total: 39,709,872,236 bytes.
