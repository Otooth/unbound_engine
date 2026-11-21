FROM nvcr.io/nvidia/pytorch@sha256:8014e4ffc674ae9cc9aecad04b64b29c3d311ed738e54ac3042d2d45690d19e9

ARG COMFYUI_COMMIT=989d93d0d968e704c589ce0146bb18c77f6b50cc
ARG COMFYUI_MANAGER_COMMIT=8a75836a7b0b9ce0bd81801fd39d5737a6d7121b

WORKDIR /opt/ComfyUI

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN git init . \
    && git remote add origin https://github.com/comfyanonymous/ComfyUI.git \
    && git fetch --depth 1 origin "${COMFYUI_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${COMFYUI_COMMIT}"

# Preserve NVIDIA's optimized PyTorch build from the NGC base image.
RUN sed -i '/^torch\b/d; /^torchvision\b/d; /^torchaudio\b/d; /^nvidia/d' requirements.txt \
    && pip install --no-cache-dir -r requirements.txt

# ComfyUI imports torchaudio on optional audio paths. PyPI torchaudio is ABI
# incompatible with this NGC PyTorch build, so provide an import-only stub.
RUN SITE="$(python -c 'import site; print(site.getsitepackages()[0])')" \
    && mkdir -p "${SITE}/torchaudio" \
    && printf '%s\n' \
        '# Audio stub for the NGC PyTorch image.' \
        '__version__ = "0.0.0+ngc-stub"' \
        'from . import functional, transforms  # noqa: F401' \
        > "${SITE}/torchaudio/__init__.py" \
    && for module in functional transforms; do \
        printf '%s\n' \
            '# Audio stub submodule for the NGC PyTorch image.' \
            'def __getattr__(name):' \
            '    raise RuntimeError("torchaudio is stubbed; audio workflows are unsupported")' \
            > "${SITE}/torchaudio/${module}.py"; \
    done \
    && python -c "import torchaudio, torchaudio.functional, torchaudio.transforms; assert torchaudio.__version__.endswith('ngc-stub')"

# Keep only the Manager from the playbook's custom-node set. Wan 2.2 and LTX
# native workflows use core nodes; unused nodes added dependency failures and
# unnecessary network/update surfaces.
RUN git init custom_nodes/ComfyUI-Manager \
    && git -C custom_nodes/ComfyUI-Manager remote add origin https://github.com/Comfy-Org/ComfyUI-Manager.git \
    && git -C custom_nodes/ComfyUI-Manager fetch --depth 1 origin "${COMFYUI_MANAGER_COMMIT}" \
    && git -C custom_nodes/ComfyUI-Manager checkout --detach FETCH_HEAD \
    && test "$(git -C custom_nodes/ComfyUI-Manager rev-parse HEAD)" = "${COMFYUI_MANAGER_COMMIT}" \
    && pip install --no-cache-dir -r custom_nodes/ComfyUI-Manager/requirements.txt

# The NGC base ships datasets and an unused spin developer CLI. ComfyUI's
# current Transformers stack and datasets share this Hub version range, while
# spin conflicts with the Click version required by the Hub client.
RUN pip uninstall -y spin \
    && pip install --no-cache-dir "huggingface-hub==1.33.0" \
    && pip check

COPY config/comfyui-manager.ini /opt/ComfyUI/user/__manager/config.ini

ENV HF_HUB_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DO_NOT_TRACK=1

EXPOSE 8188

CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
