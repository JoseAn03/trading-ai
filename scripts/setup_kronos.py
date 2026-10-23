"""Prepara el experimento KRONOS-01 sin guardar pesos en el repositorio.

Uso:
    python scripts/setup_kronos.py --model small
    python scripts/setup_kronos.py --model all

El código se clona en ``.cache/kronos/src`` y se fija al commit
``67b630e...``. Los modelos se descargan a ``.cache/kronos/models`` con las
revisiones exactas de Hugging Face. Ambos directorios están ignorados por git.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.kronos_adapter import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_CODE_DIR,
    KRONOS_CODE_COMMIT,
    KRONOS_CODE_REPO,
    KRONOS_MODELS,
    KRONOS_TOKENIZER,
)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def ensure_source(code_dir: Path) -> None:
    if not (code_dir / ".git").exists():
        code_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", KRONOS_CODE_REPO, str(code_dir)])
    _run(["git", "-C", str(code_dir), "fetch", "--quiet", "origin"])
    _run(["git", "-C", str(code_dir), "checkout", "--quiet", KRONOS_CODE_COMMIT])
    head = subprocess.check_output(
        ["git", "-C", str(code_dir), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != KRONOS_CODE_COMMIT:
        raise RuntimeError(f"checkout incorrecto: {head} != {KRONOS_CODE_COMMIT}")
    print(f"código Kronos: {code_dir} @ {head}")


def ensure_weights(model_names: list[str], cache_dir: Path) -> None:
    tokenizer_dir = cache_dir / "models" / "Kronos-Tokenizer-base"
    print(f"tokenizer: {KRONOS_TOKENIZER['repo']} @ {KRONOS_TOKENIZER['revision']}")
    snapshot_download(
        repo_id=KRONOS_TOKENIZER["repo"],
        revision=KRONOS_TOKENIZER["revision"],
        local_dir=str(tokenizer_dir),
    )
    for model_name in model_names:
        spec = KRONOS_MODELS[model_name]
        target = cache_dir / "models" / model_name.replace("kronos-", "Kronos-")
        print(f"{model_name}: {spec['repo']} @ {spec['revision']}")
        snapshot_download(
            repo_id=spec["repo"],
            revision=spec["revision"],
            local_dir=str(target),
        )
        if not (target / "model.safetensors").exists():
            raise RuntimeError(f"descarga incompleta: {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepara Kronos pineado para KRONOS-01")
    parser.add_argument("--model", choices=["small", "base", "all"], default="small")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--code-dir", type=Path, default=DEFAULT_CODE_DIR)
    parser.add_argument("--skip-code", action="store_true")
    parser.add_argument("--skip-weights", action="store_true")
    args = parser.parse_args()

    models = ["kronos-small", "kronos-base"] if args.model == "all" else [f"kronos-{args.model}"]
    if not args.skip_code:
        ensure_source(args.code_dir)
    if not args.skip_weights:
        ensure_weights(models, args.cache_dir)
    print("Listo. Los pesos están en caché local; no se commitean.")


if __name__ == "__main__":
    main()
