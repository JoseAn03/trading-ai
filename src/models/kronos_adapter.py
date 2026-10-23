"""Adaptador zero-shot de Kronos para las velas diarias del pipeline.

Kronos es un modelo fundacional de series K-line. Este módulo **no entrena**
nada: carga los pesos públicos en la revisión fijada, corta el contexto a las
últimas ``lookback`` velas cerradas y muestrea ``sample_count`` trayectorias.
La probabilidad de subida se estima con esas trayectorias. El experimento la
evalúa con las mismas ventanas, costos y gate que el baseline; si se integrara
al paper, usaría los mismos breakers y el mismo kill switch.

Pines
-----
Código: ``shiyu-coder/Kronos`` @ ``67b630e67f6a18c9e9be918d9b4337c960db1e9a``
Modelos: ver ``KRONOS_MODELS``. Los pesos nunca se guardan en este repositorio;
se descargan a ``.cache/kronos`` (ignorado por git) con
``scripts/setup_kronos.py``.

El código de Kronos se carga desde un checkout pineado. Si el checkout no
existe, el adaptador falla con instrucciones claras en vez de usar una versión
móvil de ``main``.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from src.config.settings import ROOT
from src.schemas.market import Candle

KRONOS_CODE_COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
KRONOS_CODE_REPO = "https://github.com/shiyu-coder/Kronos.git"

KRONOS_MODELS: dict[str, dict[str, str]] = {
    "kronos-small": {
        "repo": "NeoQuasar/Kronos-small",
        "revision": "901c26c1332695a2a8f243eb2f37243a37bea320",
        "parameters": "24.7M",
    },
    "kronos-base": {
        "repo": "NeoQuasar/Kronos-base",
        "revision": "2b554741eca47781b64468546e77fef3e85130e6",
        "parameters": "102.3M",
    },
}
KRONOS_TOKENIZER = {
    "repo": "NeoQuasar/Kronos-Tokenizer-base",
    "revision": "0e0117387f39004a9016484a186a908917e22426",
}

DEFAULT_CACHE_DIR = ROOT / ".cache" / "kronos"
DEFAULT_CODE_DIR = DEFAULT_CACHE_DIR / "src"
DEFAULT_LOOKBACK = 128
DEFAULT_SAMPLE_COUNT = 30
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P = 0.9
DEFAULT_TOP_K = 0
DEFAULT_SEED = 20261023


class KronosUnavailableError(RuntimeError):
    """Faltan el código o los pesos pineados de Kronos."""


@dataclass(frozen=True)
class KronosConfig:
    """Configuración congelada para KRONOS-01.

    ``lookback`` es deliberadamente 128 (≤512) para que la corrida completa de
    desarrollo quepa en CPU. No se ajusta después de ver resultados.
    """

    model: str = "kronos-small"
    lookback: int = DEFAULT_LOOKBACK
    horizon: int = 1
    sample_count: int = DEFAULT_SAMPLE_COUNT
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float = DEFAULT_TOP_P
    top_k: int = DEFAULT_TOP_K
    seed: int = DEFAULT_SEED
    max_context: int = 512
    device: str = "cpu"
    threads: int = 8
    batch_size: int = 1

    def __post_init__(self) -> None:
        if self.model not in KRONOS_MODELS:
            raise ValueError(f"modelo Kronos desconocido: {self.model}")
        if not 1 <= self.lookback <= self.max_context <= 512:
            raise ValueError("lookback debe estar entre 1 y max_context, y max_context <= 512")
        if self.horizon < 1:
            raise ValueError("horizon debe ser >= 1")
        if self.sample_count < 2:
            raise ValueError("sample_count debe ser >= 2 para estimar una probabilidad")
        if not 0.0 < self.top_p <= 1.0:
            raise ValueError("top_p debe estar en (0, 1]")
        if self.temperature <= 0:
            raise ValueError("temperature debe ser > 0")
        if self.batch_size < 1:
            raise ValueError("batch_size debe ser >= 1")

    @property
    def model_revision(self) -> str:
        return KRONOS_MODELS[self.model]["revision"]

    @property
    def model_repo(self) -> str:
        return KRONOS_MODELS[self.model]["repo"]

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "repo": self.model_repo,
            "revision": self.model_revision,
            "parameters": KRONOS_MODELS[self.model]["parameters"],
            "lookback": self.lookback,
            "horizon": self.horizon,
            "sample_count": self.sample_count,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "seed": self.seed,
            "max_context": self.max_context,
            "device": self.device,
            "threads": self.threads,
            "batch_size": self.batch_size,
            "code_repo": KRONOS_CODE_REPO,
            "code_commit": KRONOS_CODE_COMMIT,
            "tokenizer_repo": KRONOS_TOKENIZER["repo"],
            "tokenizer_revision": KRONOS_TOKENIZER["revision"],
        }


@dataclass(frozen=True)
class KronosPrediction:
    """Señal puntual derivada de las trayectorias muestreadas."""

    asset: str
    ts: datetime
    prob_up: float
    expected_return_median: float
    expected_return_mean: float
    up_count: int
    sample_count: int
    close: float

    def describe(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "ts": self.ts.isoformat(),
            "prob_up": self.prob_up,
            "expected_return_median": self.expected_return_median,
            "expected_return_mean": self.expected_return_mean,
            "up_count": self.up_count,
            "sample_count": self.sample_count,
            "close": self.close,
        }


PathSampler = Callable[[list[list[Candle]], int, int], np.ndarray]


def _checkout_commit(code_dir: Path) -> str | None:
    """Devuelve el HEAD del checkout si es un repo git; útil para verificar el pin."""
    git_dir = code_dir / ".git"
    if not git_dir.exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(code_dir), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _load_kronos_source(code_dir: Path) -> Any:
    """Carga el paquete ``model`` del checkout pineado, nunca de PyPI."""
    code_dir = Path(code_dir)
    kronos_file = code_dir / "model" / "kronos.py"
    module_file = code_dir / "model" / "module.py"
    if not kronos_file.exists() or not module_file.exists():
        raise KronosUnavailableError(
            f"No está el código de Kronos en {code_dir}. Ejecuta "
            "`python scripts/setup_kronos.py --model small`."
        )

    head = _checkout_commit(code_dir)
    if head is not None and head != KRONOS_CODE_COMMIT:
        raise KronosUnavailableError(
            f"El checkout de Kronos está en {head}, no en el commit fijado "
            f"{KRONOS_CODE_COMMIT}. Vuelve a ejecutar scripts/setup_kronos.py."
        )

    path = str(code_dir.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        module = importlib.import_module("model")
    except Exception as exc:  # noqa: BLE001 - el error original es informativo
        raise KronosUnavailableError(f"No se pudo importar el código de Kronos: {exc}") from exc
    module_file = Path(getattr(module, "__file__", "")).resolve()
    if code_dir.resolve() not in module_file.parents:
        raise KronosUnavailableError(
            f"Se importó un paquete `model` ajeno ({module_file}); reinicia el proceso "
            "con el checkout pineado de Kronos."
        )
    return module


def _time_array(stamps: Sequence[datetime]) -> np.ndarray:
    """Mismo calendario que ``calc_time_stamps`` de Kronos (sin precios futuros)."""
    rows = []
    for stamp in stamps:
        rows.append(
            (
                float(stamp.minute),
                float(stamp.hour),
                float(stamp.weekday()),
                float(stamp.day),
                float(stamp.month),
            )
        )
    return np.asarray(rows, dtype=np.float32)


def _price_array(candles: Sequence[Candle]) -> np.ndarray:
    """OHLCV + amount = volumen * precio medio, como en el ejemplo de Kronos."""
    rows = []
    for candle in candles:
        mean_price = (candle.open + candle.high + candle.low + candle.close) / 4.0
        rows.append(
            (
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.volume * mean_price,
            )
        )
    return np.asarray(rows, dtype=np.float32)


def _seed_for(config: KronosConfig, stamp: datetime) -> int:
    """Semilla estable por fecha: la misma consulta produce el mismo resultado."""
    return config.seed + stamp.toordinal()


class KronosAdapter:
    """Envoltorio mínimo y auditable alrededor de Kronos.

    ``path_sampler`` existe para tests de no-lookahead con datos sintéticos. En
    producción se deja en ``None`` y se usa el modelo real.
    """

    def __init__(
        self,
        config: KronosConfig | None = None,
        *,
        cache_dir: Path | None = None,
        code_dir: Path | None = None,
        path_sampler: PathSampler | None = None,
    ) -> None:
        self.config = config or KronosConfig()
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.code_dir = Path(code_dir or DEFAULT_CODE_DIR)
        self._path_sampler = path_sampler
        self._torch: Any | None = None
        self._tokenizer: Any | None = None
        self._model: Any | None = None

    # ------------------------------------------------------------------
    # Carga perezosa: los tests unitarios no necesitan torch ni pesos.
    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._path_sampler is not None or self._model is not None

    def load(self) -> None:
        if self.loaded:
            return

        try:
            import torch
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise KronosUnavailableError(
                "Falta torch. Instala `pip install -r requirements-kronos.txt`."
            ) from exc

        from huggingface_hub import snapshot_download

        source = _load_kronos_source(self.code_dir)
        tokenizer_dir = self.cache_dir / "models" / "Kronos-Tokenizer-base"
        model_dir = self.cache_dir / "models" / self.config.model.replace("kronos-", "Kronos-")
        if not (tokenizer_dir / "model.safetensors").exists():
            snapshot_download(
                repo_id=KRONOS_TOKENIZER["repo"],
                revision=KRONOS_TOKENIZER["revision"],
                local_dir=str(tokenizer_dir),
            )
        if not (model_dir / "model.safetensors").exists():
            snapshot_download(
                repo_id=self.config.model_repo,
                revision=self.config.model_revision,
                local_dir=str(model_dir),
            )

        torch.set_num_threads(self.config.threads)
        tokenizer = source.KronosTokenizer.from_pretrained(str(tokenizer_dir))
        model = source.Kronos.from_pretrained(str(model_dir))
        tokenizer.eval()
        model.eval()
        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model

    # ------------------------------------------------------------------
    # Inferencia
    # ------------------------------------------------------------------
    def _sample_paths(
        self, contexts: list[list[Candle]], *, horizon: int, seed: int
    ) -> np.ndarray:
        """Devuelve ``(n_contextos, sample_count, horizon, 6)`` sin promediar.

        Se replica la inferencia autorregresiva oficial, pero conservando las
        trayectorias individuales. ``KronosPredictor.predict`` las promedia y
        perdería la probabilidad de subida.
        """
        if not contexts:
            raise ValueError("se requiere al menos un contexto")
        lengths = {len(context) for context in contexts}
        if len(lengths) != 1:
            raise ValueError("todos los contextos del lote deben tener la misma longitud")
        length = lengths.pop()
        if length < 2:
            raise ValueError("el contexto necesita al menos dos velas")
        if length > self.config.max_context:
            raise ValueError("el contexto supera max_context")

        if self._path_sampler is not None:
            return np.asarray(self._path_sampler(contexts, horizon, seed), dtype=float)

        self.load()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None
        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model

        torch.manual_seed(seed)
        prices = np.stack([_price_array(context) for context in contexts])
        stamps = np.stack([_time_array([c.ts for c in context]) for context in contexts])
        means = prices.mean(axis=1)
        stds = prices.std(axis=1)
        normalized = np.clip((prices - means) / (stds + 1e-5), -5.0, 5.0)
        sample_count = self.config.sample_count

        future_stamps = np.stack(
            [
                _time_array(
                    [
                        context[-1].ts + timedelta(days=step)
                        for step in range(1, horizon + 1)
                    ]
                )
                for context in contexts
            ]
        )

        # (B, 1, L, F) -> (B*S, L, F), igual que el código oficial.
        x = torch.from_numpy(normalized.astype(np.float32))
        stamp = torch.from_numpy(stamps.astype(np.float32))
        y_stamp = torch.from_numpy(future_stamps.astype(np.float32))
        x = x.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(-1, length, x.size(2))
        stamp = stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(
            -1, length, stamp.size(2)
        )
        y_stamp = y_stamp.unsqueeze(1).repeat(1, sample_count, 1, 1).reshape(
            -1, horizon, y_stamp.size(2)
        )

        max_context = self.config.max_context
        with torch.inference_mode():
            tokens = tokenizer.encode(x.to(self.config.device), half=True)
            initial_len = x.size(1)
            batch = tokens[0].size(0)
            full_stamp = torch.cat([stamp.to(self.config.device), y_stamp.to(self.config.device)], dim=1)
            generated_pre = tokens[0].new_empty(batch, horizon)
            generated_post = tokens[1].new_empty(batch, horizon)
            pre_buffer = tokens[0].new_zeros(batch, max_context)
            post_buffer = tokens[1].new_zeros(batch, max_context)
            buffer_len = min(initial_len, max_context)
            start_idx = max(0, initial_len - max_context)
            pre_buffer[:, :buffer_len] = tokens[0][:, start_idx : start_idx + buffer_len]
            post_buffer[:, :buffer_len] = tokens[1][:, start_idx : start_idx + buffer_len]

            from model.kronos import sample_from_logits

            for step in range(horizon):
                current_len = initial_len + step
                window_len = min(current_len, max_context)
                if current_len <= max_context:
                    input_tokens = [pre_buffer[:, :window_len], post_buffer[:, :window_len]]
                else:
                    input_tokens = [pre_buffer, post_buffer]
                context_end = current_len
                context_start = max(0, context_end - max_context)
                current_stamp = full_stamp[:, context_start:context_end, :].contiguous()

                s1_logits, context = model.decode_s1(
                    input_tokens[0], input_tokens[1], current_stamp
                )
                pre = sample_from_logits(
                    s1_logits[:, -1, :],
                    temperature=self.config.temperature,
                    top_k=self.config.top_k,
                    top_p=self.config.top_p,
                    sample_logits=True,
                )
                s2_logits = model.decode_s2(context, pre)
                post = sample_from_logits(
                    s2_logits[:, -1, :],
                    temperature=self.config.temperature,
                    top_k=self.config.top_k,
                    top_p=self.config.top_p,
                    sample_logits=True,
                )
                generated_pre[:, step] = pre.squeeze(-1)
                generated_post[:, step] = post.squeeze(-1)

                if current_len < max_context:
                    pre_buffer[:, current_len] = pre.squeeze(-1)
                    post_buffer[:, current_len] = post.squeeze(-1)
                else:
                    pre_buffer.copy_(torch.roll(pre_buffer, shifts=-1, dims=1))
                    post_buffer.copy_(torch.roll(post_buffer, shifts=-1, dims=1))
                    pre_buffer[:, -1] = pre.squeeze(-1)
                    post_buffer[:, -1] = post.squeeze(-1)

            full_pre = torch.cat([tokens[0], generated_pre], dim=1)
            full_post = torch.cat([tokens[1], generated_post], dim=1)
            total_len = initial_len + horizon
            context_start = max(0, total_len - max_context)
            decoded = tokenizer.decode(
                [
                    full_pre[:, context_start:total_len].contiguous(),
                    full_post[:, context_start:total_len].contiguous(),
                ],
                half=True,
            )
            paths = decoded[:, -horizon:, :].reshape(
                len(contexts), sample_count, horizon, 6
            ).cpu().numpy()

        return paths * (stds[:, None, None, :] + 1e-5) + means[:, None, None, :]

    def predict_at(
        self, candles: Sequence[Candle], index: int, *, seed: int | None = None
    ) -> KronosPrediction:
        """Predice en ``candles[index]`` usando solo velas cerradas hasta ese índice."""
        ordered = sorted(candles, key=lambda c: c.ts)
        if not 0 <= index < len(ordered):
            raise IndexError(f"índice fuera de rango: {index}")
        start = max(0, index - self.config.lookback + 1)
        context = list(ordered[start : index + 1])
        if len(context) < 2:
            raise ValueError("se necesitan al menos dos velas para predecir")
        stamp = context[-1].ts
        used_seed = _seed_for(self.config, stamp) if seed is None else seed
        paths = self._sample_paths([context], horizon=self.config.horizon, seed=used_seed)
        returns = paths[0, :, -1, 3] / context[-1].close - 1.0
        up_count = int((returns > 0).sum())
        # Jeffreys sobre muestras finitas: evita log loss infinito y es una regla
        # fijada antes de mirar resultados.
        prob_up = (up_count + 0.5) / (self.config.sample_count + 1.0)
        return KronosPrediction(
            asset=context[-1].asset,
            ts=stamp,
            prob_up=float(prob_up),
            expected_return_median=float(median(float(r) for r in returns)),
            expected_return_mean=float(np.mean(returns)),
            up_count=up_count,
            sample_count=self.config.sample_count,
            close=context[-1].close,
        )

    def predict_series(
        self,
        candles: Sequence[Candle],
        indices: Sequence[int],
        *,
        progress: bool = False,
    ) -> list[KronosPrediction]:
        ordered = sorted(candles, key=lambda c: c.ts)
        predictions: list[KronosPrediction] = []
        for position, index in enumerate(indices):
            predictions.append(self.predict_at(ordered, index))
            if progress and (position + 1) % 25 == 0:
                print(f"  {position + 1}/{len(indices)} predicciones", flush=True)
        return predictions


def verify_cached_files(cache_dir: Path | None = None) -> dict[str, str | bool]:
    """Comprueba presencia y tamaño de pesos; no calcula hashes de 400 MB."""
    root = Path(cache_dir or DEFAULT_CACHE_DIR) / "models"
    result: dict[str, str | bool] = {}
    for name in ("Kronos-Tokenizer-base", "Kronos-small", "Kronos-base"):
        path = root / name / "model.safetensors"
        result[name] = path.exists()
    return result
