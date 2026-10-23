# Comparacion de features — 2026-10-22

**Fuente:** `live` · **Dias:** 1500 · **Activos:** BTC, ETH · **Horizonte:** 1d · **Ventanas:** train 365 / test 60 / step 60 · **Umbral:** 0.55 · **Costo:** 0.20% · **Historia minima:** 201

> Mismas muestras, mismas ventanas, mismo modelo. Solo cambia el set de features.

## Resumen

| Set | Features | Muestras | Log loss | Brier | ECE | Trades | Retorno neto | Media/trade | Max DD | Ventanas + | Activos + |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `v0.1` | 5 | 1800 | 0.6995 | 0.2529 | 0.0312 | 226 | +6.04% | +0.07% | 22.4% | 53% | 2/2 |
| `v0.2` | 9 | 1800 | 0.7061 | 0.2560 | 0.0511 | 483 | -65.69% | -0.17% | 80.5% | 37% | 0/2 |

## Diferencia contra el set base

| Set | Delta log loss | Delta retorno neto | Delta ECE |
| --- | --- | --- | --- |
| `v0.2` | +0.0065 | -71.73% | +0.0199 |

## Gates

| Set | Supera baseline | Calibrado | Rentable | Robusto |
| --- | --- | --- | --- | --- |
| `v0.1` | ❌ | ✅ | ✅ | ❌ |
| `v0.2` | ❌ | ❌ | ❌ | ❌ |

## Por activo

| Set | Activo | Log loss | ECE | Trades | Retorno neto | Ventanas + |
| --- | --- | --- | --- | --- | --- | --- |
| `v0.1` | BTC | 0.6965 | 0.0255 | 163 | +0.51% | 60% |
| `v0.1` | ETH | 0.7025 | 0.0370 | 63 | +5.49% | 47% |
| `v0.2` | BTC | 0.7028 | 0.0519 | 261 | -36.93% | 47% |
| `v0.2` | ETH | 0.7094 | 0.0503 | 222 | -45.60% | 27% |

## Lectura

- Mejor log loss: `v0.1` (0.6995 vs baseline 0.6931).
- Sets que pasan los cuatro gates: ninguno.

**Criterio:** un set nuevo solo se adopta si mejora el log loss del set base **y** pasa calibracion, rentabilidad y robustez. Mejorar solo el retorno puede ser suerte; mejorar solo el log loss puede no cubrir costos.

## Reproducir

```bash
python -m backtests.compare_features --source live --days 1500 --assets BTC,ETH --feature-sets v0.1,v0.2 --min-history 201 --horizon 1 --threshold 0.55
```
