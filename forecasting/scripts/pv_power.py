"""
GHI + ambient temperature -> PV AC power, for the Stellenbosch plant.

    python pv_power.py --source ../stellies_mod.csv --out ../powerfactory_export

MODEL CHAIN
-----------
PVWatts (NREL), which is the standard for exactly this input set -
irradiance and ambient temperature in, AC power out:

  1. solar position        NOAA, from solar_geometry.py (already validated
                           against this record's own observed daily peak)
  2. GHI -> DHI, DNI       Erbs diffuse-fraction correlation. Only GHI is
                           measured, and a tilted plane sees beam and
                           diffuse differently, so the split must be inferred
  3. -> plane of array     Hay-Davies transposition (isotropic sky + a
                           circumsolar term weighted by the anisotropy index)
  4. cell temperature      NOCT: T_cell = T_amb + (POA/800)(NOCT - 20).
                           No wind data, so Faiman/SAPM would need an assumed
                           wind speed and buy nothing over this
  5. DC power              P_dc = (POA/1000) P_dc0 (1 + gamma (T_cell - 25))
  6. -> AC                 system losses, inverter efficiency, and clipping
                           at the AC rating (DC/AC = 1.21 here, so clipping
                           is real and must be modelled)

AZIMUTH CONVENTION
------------------
solar_position returns azimuth as degrees clockwise from NORTH, verified
empirically: at winter-solstice solar noon it returns ~12 deg at elevation
31.6 deg, against a theoretical maximum of 32.6 deg for latitude -33.93.
In the southern hemisphere the array faces north, so SURFACE_AZIMUTH = 0.
Getting this backwards would put the array facing away from the sun and is
exactly the class of silent error that the timezone trap in this codebase
already documents.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SITE_LAT, SITE_LON, SITE_TZ_OFFSET  # noqa: E402
from solar_geometry import (CLEARSKY_TRANSMITTANCE, SOLAR_CONSTANT,  # noqa: E402
                            clear_sky_ghi, solar_position)

# Surface DNI cannot exceed the clear-sky direct beam - the atmosphere
# always attenuates. Erbs, fed a cloud-enhanced GHI, will happily return
# more than that, and a tilted plane then amplifies it further. The
# margin allows for the clear-sky envelope being a shape rather than a
# calibrated model (its observed/modelled median here is 0.90).
MAX_DNI_OVER_CLEARSKY = 1.05

# Ceiling on the observed/clear-sky ratio. The 0-1400 W/m^2 screen the
# forecasting pipeline applies is a GLOBAL cap: it cannot catch a reading
# that is only impossible FOR ITS SUN ANGLE. 1,351 W/m^2 at 36.6 deg
# elevation in May is over twice the clear-sky value there, and Erbs then
# reads it as almost pure beam, which a tilted plane amplifies further -
# producing a 1,585 W/m^2 plane-of-array irradiance that cannot occur.
# This record's own observed/clear-sky ratio has a p95 of 1.175 (see the
# smoke tests), so 1.3 leaves room for genuine cloud enhancement - which
# really can exceed 1, but not by much - while removing the impossible.
MAX_CLEARSKY_RATIO = 1.30

# ---- plant, as specified -------------------------------------------------
P_DC0_KW = 726.0                 # 1,320 x 550 Wp
P_AC0_KW = 600.0                 # 6 x 100 kW inverters
GAMMA_PMAX = -0.0035             # -0.35 %/K
TILT_DEG = 30.0
SURFACE_AZIMUTH_DEG = 0.0        # due north (southern hemisphere)
NOCT_C = 45.0                    # ground-mount open rack
SYSTEM_LOSSES = 0.14             # PVWatts default: soiling, wiring, mismatch,
                                 # connections, LID, nameplate, availability
INVERTER_EFF = 0.96
ALBEDO = 0.20


def erbs_diffuse_fraction(kt: np.ndarray) -> np.ndarray:
    """Erbs (1982): diffuse fraction of GHI from the clearness index."""
    f = np.where(
        kt <= 0.22,
        1.0 - 0.09 * kt,
        np.where(
            kt <= 0.80,
            (0.9511 - 0.1604 * kt + 4.388 * kt ** 2
             - 16.638 * kt ** 3 + 12.336 * kt ** 4),
            0.165))
    return np.clip(f, 0.0, 1.0)


def pv_ac_power(ghi: np.ndarray, temp_c: np.ndarray,
                index: pd.DatetimeIndex) -> pd.DataFrame:
    """Full chain. Returns every intermediate so the result is auditable."""
    pos = solar_position(index, SITE_LAT, SITE_LON, SITE_TZ_OFFSET)
    cosz = pos["cos_zenith"].to_numpy()
    zen_r = np.arccos(np.clip(cosz, -1.0, 1.0))
    sun_az_r = np.radians(pos["azimuth"].to_numpy())
    up = cosz > 0.0                              # sun above the horizon

    ghi = np.clip(np.nan_to_num(ghi, nan=0.0), 0.0, None)

    # Angle-aware screening, before anything downstream reads it.
    cs = clear_sky_ghi(index, SITE_LAT, SITE_LON, SITE_TZ_OFFSET).to_numpy()
    ceiling = MAX_CLEARSKY_RATIO * cs
    over = (cs > 0) & (ghi > ceiling)
    n_capped = int(over.sum())
    ghi = np.where(over, ceiling, ghi)
    pv_ac_power.last_capped = n_capped          # type: ignore[attr-defined]

    # -- 1. extraterrestrial, and the clearness index -------------------
    doy = index.dayofyear.to_numpy()
    e0 = 1.0 + 0.033 * np.cos(2 * np.pi * doy / 365.0)   # Earth-Sun distance
    dni_extra = SOLAR_CONSTANT * e0
    ghi_extra = np.where(up, dni_extra * cosz, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        kt = np.where(ghi_extra > 0, ghi / ghi_extra, 0.0)
    kt = np.clip(np.nan_to_num(kt), 0.0, 1.0)

    # -- 2. Erbs split ---------------------------------------------------
    dhi = ghi * erbs_diffuse_fraction(kt)
    with np.errstate(divide="ignore", invalid="ignore"):
        dni = np.where(up, (ghi - dhi) / np.where(cosz > 0, cosz, np.nan), 0.0)
    dni_clearsky = dni_extra * CLEARSKY_TRANSMITTANCE * MAX_DNI_OVER_CLEARSKY
    dni = np.clip(np.nan_to_num(dni), 0.0, dni_clearsky)

    # -- 3. Hay-Davies transposition ------------------------------------
    tilt_r = np.radians(TILT_DEG)
    surf_az_r = np.radians(SURFACE_AZIMUTH_DEG)
    cos_aoi = (np.cos(zen_r) * np.cos(tilt_r)
               + np.sin(zen_r) * np.sin(tilt_r) * np.cos(sun_az_r - surf_az_r))
    cos_aoi = np.where(up, cos_aoi, 0.0)
    cos_aoi = np.clip(cos_aoi, 0.0, 1.0)          # no light through the back

    poa_beam = dni * cos_aoi
    ai = np.clip(np.divide(dni, dni_extra, out=np.zeros_like(dni),
                           where=dni_extra > 0), 0.0, 1.0)   # anisotropy index
    with np.errstate(divide="ignore", invalid="ignore"):
        rb = np.where(cosz > 0.01, cos_aoi / np.where(cosz > 0.01, cosz, np.nan), 0.0)
    rb = np.nan_to_num(np.clip(rb, 0.0, 10.0))
    poa_sky = dhi * (ai * rb + (1.0 - ai) * (1.0 + np.cos(tilt_r)) / 2.0)
    poa_ground = ghi * ALBEDO * (1.0 - np.cos(tilt_r)) / 2.0
    poa = np.clip(poa_beam + poa_sky + poa_ground, 0.0, None)
    poa = np.where(up, poa, 0.0)

    # -- 4. cell temperature (NOCT) -------------------------------------
    t_amb = pd.Series(temp_c).ffill().bfill().to_numpy()
    t_cell = t_amb + (poa / 800.0) * (NOCT_C - 20.0)

    # -- 5. DC, then 6. AC ----------------------------------------------
    p_dc = (poa / 1000.0) * P_DC0_KW * (1.0 + GAMMA_PMAX * (t_cell - 25.0))
    p_dc = np.clip(p_dc, 0.0, None)
    p_ac_unclipped = p_dc * (1.0 - SYSTEM_LOSSES) * INVERTER_EFF
    p_ac = np.minimum(p_ac_unclipped, P_AC0_KW)

    return pd.DataFrame({
        "ghi_wm2": ghi, "dni_wm2": dni, "dhi_wm2": dhi, "poa_wm2": poa,
        "temp_amb_c": t_amb, "temp_cell_c": t_cell,
        "p_dc_kw": p_dc, "p_ac_kw": p_ac,
        "p_ac_norm": p_ac / P_AC0_KW,
        "clipped": (p_ac_unclipped > P_AC0_KW).astype(int),
    }, index=index)


def summarise(df: pd.DataFrame, label: str) -> str:
    hours = 5.0 / 60.0
    mwh = df["p_ac_kw"].sum() * hours / 1000.0
    days = df.index.normalize().nunique()
    cf = df["p_ac_kw"].mean() / P_AC0_KW
    clip_h = df["clipped"].sum() * hours
    return (f"{label}\n"
            f"  period          {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d} "
            f"({days} days, {len(df):,} steps)\n"
            f"  generation      {mwh:,.1f} MWh   ({mwh * 1000 / days:,.0f} kWh/day)\n"
            f"  specific yield  {mwh * 1000 / P_DC0_KW:,.0f} kWh/kWp\n"
            f"  peak AC         {df['p_ac_kw'].max():.1f} kW of {P_AC0_KW:.0f} kW\n"
            f"  capacity factor {cf:.1%}  (AC basis)\n"
            f"  peak POA        {df['poa_wm2'].max():.0f} W/m2\n"
            f"  cell temp       {df['temp_cell_c'].min():.1f} to "
            f"{df['temp_cell_c'].max():.1f} C\n"
            f"  GHI capped      {getattr(pv_ac_power, 'last_capped', 0):,} step(s) "
            f"above {MAX_CLEARSKY_RATIO}x clear sky\n"
            f"  inverter clipping {clip_h:,.1f} h "
            f"({df['clipped'].mean():.2%} of steps)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="re-dated measured record (stellies_mod.csv)")
    ap.add_argument("--forecast", default=None,
                    help="solar_forecast_real_units.csv, for the forecast-driven profile")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    src = pd.read_csv(a.source, parse_dates=["timestamp"]).set_index("timestamp")
    gcol = next(c for c in src.columns if "ghi" in str(c).lower())
    tcol = next(c for c in src.columns if "temp" in str(c).lower())

    # ---------------- full measured profile (the ChaTime source) --------
    full = pv_ac_power(src[gcol].to_numpy(), src[tcol].to_numpy(), src.index)
    print(summarise(full, "MEASURED RESOURCE  (full window)"))
    full_out = full.reset_index().rename(columns={"index": "timestamp"})
    full_out.to_csv(out / "pv_ac_profile_measured.csv", index=False)

    # PowerFactory ChaTime factor series: two columns, nothing else
    cha = pd.DataFrame({
        "timestamp": full.index,
        "factor": full["p_ac_norm"].round(6),
    })
    cha.to_csv(out / "pv_chatime_factor.csv", index=False)

    # ---------------- forecast-driven profile ---------------------------
    if a.forecast:
        fc = pd.read_csv(a.forecast, parse_dates=["timestamp"]).set_index("timestamp")
        temp = src[tcol].reindex(fc.index)
        fdf = pv_ac_power(fc["ghi_wm2_forecast"].to_numpy(), temp.to_numpy(), fc.index)
        adf = pv_ac_power(fc["ghi_wm2_actual"].to_numpy(), temp.to_numpy(), fc.index)
        print()
        print(summarise(fdf, "FORECAST-DRIVEN  (test window)"))
        merged = pd.DataFrame({
            "timestamp": fc.index,
            "p_ac_kw_forecast": fdf["p_ac_kw"].to_numpy(),
            "p_ac_kw_actual": adf["p_ac_kw"].to_numpy(),
            "p_ac_norm_forecast": fdf["p_ac_norm"].to_numpy(),
            "p_ac_norm_actual": adf["p_ac_norm"].to_numpy(),
            "poa_wm2_forecast": fdf["poa_wm2"].to_numpy(),
            "temp_cell_c_forecast": fdf["temp_cell_c"].to_numpy(),
        })
        # GHI quantiles -> PV quantiles. A quantile passes through a
        # monotone map unchanged, and at fixed sun position and temperature
        # the chain is monotone in GHI while the sun is in front of the
        # array. The exception is the sun behind the plane (cos AOI = 0, early
        # and late in the day near summer), where a clearer sky moves light
        # from diffuse into a beam the array cannot see; power there is
        # negligible. Levels are re-sorted so they can never cross.
        qcols = sorted((c for c in fc.columns if c.startswith("ghi_wm2_q")),
                       key=lambda c: float(c[len("ghi_wm2_q"):]))
        if qcols:
            pq = np.column_stack([
                pv_ac_power(fc[c].to_numpy(), temp.to_numpy(), fc.index)["p_ac_kw"].to_numpy()
                for c in qcols])
            pq = np.sort(pq, axis=1)
            for j, c in enumerate(qcols):
                merged[f"p_ac_kw_q{c[len('ghi_wm2_q'):]}"] = pq[:, j]
        merged.to_csv(out / "pv_ac_profile_forecast.csv", index=False)
        err = (merged["p_ac_kw_forecast"] - merged["p_ac_kw_actual"]).abs()
        day = merged["p_ac_kw_actual"] > 1.0
        print(f"\n  forecast error in POWER: MAE {err[day].mean():.1f} kW, "
              f"RMSE {np.sqrt((err[day] ** 2).mean()):.1f} kW (generating hours)")

    (out / "pv_plant_config.json").write_text(json.dumps({
        "modules": {"count": 1320, "pmpp_w": 550, "technology": "half-cell mono PERC",
                    "gamma_pmax_per_k": GAMMA_PMAX, "efficiency": 0.213,
                    "area_m2": 2.58},
        "array": {"series_per_string": 20, "strings_per_inverter": 11,
                  "inverters": 6, "p_dc_kwp": P_DC0_KW, "p_ac_kw": P_AC0_KW,
                  "dc_ac_ratio": round(P_DC0_KW / P_AC0_KW, 3)},
        "geometry": {"tilt_deg": TILT_DEG, "surface_azimuth_deg": SURFACE_AZIMUTH_DEG,
                     "convention": "azimuth clockwise from north; 0 = due north",
                     "mounting": "ground-mount fixed", "tracking": "none"},
        "site": {"lat": SITE_LAT, "lon": SITE_LON, "tz_offset": SITE_TZ_OFFSET},
        "model": {"chain": "Erbs -> Hay-Davies -> NOCT -> PVWatts",
                  "noct_c": NOCT_C, "system_losses": SYSTEM_LOSSES,
                  "inverter_efficiency": INVERTER_EFF, "albedo": ALBEDO},
    }, indent=2))
    print(f"\n-> {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
