"""
Solar position and a clear-sky envelope, from date and time alone.

Why this belongs in a forecasting codebase: the sun's position at any
future instant is *deterministic*. It is known exactly at forecast time,
for every step of the horizon, which makes it a completely legal future
covariate - unlike the weather, which is not.

For irradiance forecasting this is the single most valuable input there
is. Almost all of the variance in GHI is the diurnal and seasonal cycle;
a model given solar elevation only has to learn the cloud attenuation on
top of it, which is a far easier problem than learning the whole shape
from scratch.

It also gives the honest denominator for evaluation. Night-time GHI is
exactly zero and trivially predictable, so scoring it alongside daylight
inflates every metric. The clear-sky curve is what decides which steps
count as daytime.

Implemented from the NOAA solar position equations, to a few tenths of a
degree - far better than needed here, and with no extra dependency (no
pvlib).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Extraterrestrial irradiance on a plane normal to the beam.
SOLAR_CONSTANT = 1361.0
# Bulk clear-sky transmittance. A crude Haurwitz-style model is plenty:
# what matters is the SHAPE of the envelope, not its absolute calibration,
# because the model learns a scale factor anyway.
CLEARSKY_TRANSMITTANCE = 0.75


def _fractional_year(index: pd.DatetimeIndex) -> np.ndarray:
    day = index.dayofyear.to_numpy(dtype=float)
    hour = index.hour.to_numpy(dtype=float)
    return 2 * np.pi / 365.0 * (day - 1 + (hour - 12) / 24.0)


def solar_position(index: pd.DatetimeIndex, lat: float, lon: float,
                   tz_offset: float) -> pd.DataFrame:
    """
    Solar elevation and azimuth for every timestamp, in degrees.

    Timestamps are treated as LOCAL STANDARD TIME, which is how the
    loggers in this project record them. `tz_offset` is the standard
    UTC offset in hours and must be given explicitly - inferring it from
    longitude is wrong wherever a country's zone meridian is far from the
    site, which includes South Africa: round(18.86/15) = 1, but SAST is
    UTC+2, and the resulting hour of error puts modelled solar noon a
    full hour before the record's own daily peak.
    """
    g = _fractional_year(index)

    # Equation of time (minutes) and declination (radians), NOAA form.
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g)
            - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
            - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))

    minutes = (index.hour.to_numpy(dtype=float) * 60
               + index.minute.to_numpy(dtype=float)
               + index.second.to_numpy(dtype=float) / 60.0)
    time_offset = eqtime + 4.0 * lon - 60.0 * tz_offset
    true_solar_time = minutes + time_offset
    hour_angle = np.radians(true_solar_time / 4.0 - 180.0)

    lat_r = np.radians(lat)
    cos_zenith = (np.sin(lat_r) * np.sin(decl)
                  + np.cos(lat_r) * np.cos(decl) * np.cos(hour_angle))
    cos_zenith = np.clip(cos_zenith, -1.0, 1.0)
    zenith = np.arccos(cos_zenith)
    elevation = np.degrees(np.pi / 2 - zenith)

    azimuth = np.degrees(np.arctan2(
        -np.sin(hour_angle),
        np.tan(decl) * np.cos(lat_r) - np.sin(lat_r) * np.cos(hour_angle))) % 360.0

    return pd.DataFrame({"elevation": elevation, "azimuth": azimuth,
                         "cos_zenith": cos_zenith, "declination": np.degrees(decl)},
                        index=index)


def clear_sky_ghi(index: pd.DatetimeIndex, lat: float, lon: float,
                  tz_offset: float) -> pd.Series:
    """
    A clear-sky GHI envelope in W/m^2. Zero whenever the sun is down.

    Deliberately simple: a transmittance times the extraterrestrial
    normal irradiance times cos(zenith), with the Earth-Sun distance
    correction. Good enough to carry the diurnal and seasonal shape,
    which is all that is asked of it.
    """
    pos = solar_position(index, lat, lon, tz_offset)
    doy = index.dayofyear.to_numpy(dtype=float)
    distance_factor = 1 + 0.033 * np.cos(2 * np.pi * doy / 365.0)
    ghi = SOLAR_CONSTANT * distance_factor * CLEARSKY_TRANSMITTANCE * pos["cos_zenith"]
    return pd.Series(np.where(pos["elevation"].to_numpy() > 0, ghi, 0.0),
                     index=index, name="clear_sky").clip(lower=0.0)


def clear_sky_index(ghi: pd.Series, lat: float, lon: float, tz_offset: float,
                    floor: float = 20.0) -> pd.Series:
    """
    k = GHI / clear-sky GHI, the standard way to strip out the geometry.

    Undefined near sunrise and sunset where the denominator goes to zero,
    so it is only computed above `floor` and set to NaN elsewhere rather
    than allowed to explode.
    """
    cs = clear_sky_ghi(ghi.index, lat, lon, tz_offset)
    k = pd.Series(np.nan, index=ghi.index, name="clear_sky_index")
    usable = cs > floor
    k[usable] = (ghi[usable] / cs[usable]).clip(0.0, 1.5)
    return k


def daytime_mask(index: pd.DatetimeIndex, lat: float, lon: float, tz_offset: float,
                 threshold: float = 20.0) -> np.ndarray:
    """
    True where clear-sky irradiance exceeds `threshold`.

    This is the mask solar metrics are computed over. Scoring a solar
    model on night-time - where the answer is zero and every model gets
    it right - would inflate the numbers to the point of meaninglessness.
    """
    return (clear_sky_ghi(index, lat, lon, tz_offset) > threshold).to_numpy()
