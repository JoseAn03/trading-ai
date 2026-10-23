# Barrido de horizontes — 2026-10-22

**Fuente:** `live` · **Días:** 1000 · **Activos:** BTC, ETH · **Ventanas:** train 365 / test 60 / step 60 · **Umbral:** 0.55 · **Costo:** 0.20%

> Se cambia solo el horizonte de evaluación. Features, modelo, costos y protocolo quedan fijos.

| Horizonte | Muestras | Log loss | Baseline | Brier | ECE | Trades | Cobertura | Retorno neto | Media/trade | Max DD | Ventanas + | Activos + |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1d | 1200 | 0.6978 | 0.6931 | 0.2523 | 0.0318 | 103 | 8.6% | +7.20% | +0.12% | 23.9% | 50% | 1/2 |
| 2d | 1200 | 0.7001 | 0.6931 | 0.2532 | 0.0450 | 120 | 20.0% | -24.51% | -0.14% | 43.8% | 50% | 0/2 |
| 3d | 1200 | 0.7051 | 0.6931 | 0.2553 | 0.0531 | 105 | 26.2% | -39.05% | -0.36% | 50.7% | 35% | 0/2 |
| 5d | 1080 | 0.7206 | 0.6931 | 0.2633 | 0.1204 | 64 | 29.6% | -56.91% | -1.18% | 57.9% | 17% | 0/2 |
| 7d | 1080 | 0.7253 | 0.6931 | 0.2659 | 0.1496 | 49 | 30.2% | -52.07% | -1.35% | 59.8% | 22% | 0/2 |

## Lectura

- Mejor log loss: **1d** (0.6978 vs baseline 0.6931).
- Ningún horizonte se declara bueno por ganar en una sola métrica: el gate exige superar baseline, estar calibrado, ser rentable tras costos y ser robusto.

| Horizonte | ¿Supera baseline? | ¿Calibrado? | ¿Rentable? | ¿Robusto? |
| --- | --- | --- | --- | --- |
| 1d | ❌ | ✅ | ✅ | ❌ |
| 2d | ❌ | ✅ | ❌ | ❌ |
| 3d | ❌ | ❌ | ❌ | ❌ |
| 5d | ❌ | ❌ | ❌ | ❌ |
| 7d | ❌ | ❌ | ❌ | ❌ |

## Por activo

| Horizonte | Activo | Log loss | ECE | Trades | Retorno neto |
| --- | --- | --- | --- | --- | --- |
| 1d | BTC | 0.6973 | 0.0384 | 70 | -8.82% |
| 1d | ETH | 0.6984 | 0.0265 | 33 | +17.57% |
| 2d | BTC | 0.7012 | 0.0456 | 65 | -20.12% |
| 2d | ETH | 0.6991 | 0.0462 | 55 | -5.49% |
| 3d | BTC | 0.7021 | 0.0482 | 69 | -33.03% |
| 3d | ETH | 0.7082 | 0.0579 | 36 | -8.99% |
| 5d | BTC | 0.7199 | 0.1015 | 41 | -35.90% |
| 5d | ETH | 0.7214 | 0.1406 | 23 | -32.77% |
| 7d | BTC | 0.7195 | 0.1422 | 32 | -15.13% |
| 7d | ETH | 0.7311 | 0.1580 | 17 | -43.52% |

## Reproducir

```bash
python -m backtests.sweep_horizon --source live --days 1000 --assets BTC,ETH --horizons 1,2,3,5,7 --train 365 --test 60 --step 60 --threshold 0.55
```
