"""Lighting and lookdev science with no Maya dependency (stdlib only).

This file lives in the plugin (handlers/_science.py) and is copied verbatim to
src/automaya_mcp/science.py so the server can answer sun position, Kelvin and
exposure questions without a running Maya. A test checks the two copies match;
edit the plugin copy and copy it over.

Conventions (read these before trusting any number):

* Solar position uses the NOAA solar calculator equations (Jean Meeus, as
  published in the NOAA spreadsheet). Azimuth is degrees clockwise from true
  north, elevation is degrees above the horizon with atmospheric refraction.
* Kelvin to RGB is the Tanner Helland approximation, which produces display
  (sRGB encoded) values. We decode them to linear so the result can go straight
  into an Arnold light colour. 6500 K comes out near white.
* Exposure value follows the ISO definition: EV = log2(N^2 / t) - log2(ISO/100).
* Photometric to Arnold: AutoMaya assumes 683 lm/W (the photopic peak, so a
  lumen count divided by 683 is the radiant watt count at 555 nm) and picks the
  scale "one Arnold irradiance unit is what a 100 000 lux clear-sky sun
  delivers". With that scale a distant light of intensity 1 renders a white
  diffuse surface at pixel value 1, a camera at aiExposure 0 is correct for
  EV 15 (the sunny 16 rule), and an interior at EV 7 wants aiExposure +8.
  Arnold area lights are normalised (the default), so their intensity is a
  radiant intensity like a point light and does not change with light size.
  Scene units are centimetres unless told otherwise, so the inverse square law
  is evaluated with distances in cm.
* Measured material values come from public tables, chiefly Physically Based
  (physicallybased.info) and the Sebastien Lagarde / Naty Hoffman F0 tables.
  They are approximations in linear sRGB, meant as a sane starting point.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence, Tuple

LUMENS_PER_WATT = 683.0
SUN_LUX = 100000.0
ARNOLD_REFERENCE_EV = 15.0
INCIDENT_METER_CONSTANT = 250.0  # lux seconds at ISO 100 (ISO 2720 C constant, flat receptor)
EXTRATERRESTRIAL_ILLUMINANCE = 133800.0  # lux, solar constant times 683 lm/W
CLEAR_SKY_EXTINCTION = 0.21  # per air mass, a clear rural atmosphere
SCENE_UNIT_CM = 100.0  # scene units per metre when Maya works in cm


# solar position ------------------------------------------------------------
def _julian_day(when: datetime) -> float:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = (when - epoch).total_seconds()
    return seconds / 86400.0 + 2440587.5


def _refraction_deg(elevation: float) -> float:
    """NOAA atmospheric refraction correction in degrees for a true elevation."""
    if elevation > 85.0:
        return 0.0
    te = math.tan(math.radians(elevation))
    if elevation > 5.0:
        corr = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif elevation > -0.575:
        corr = 1735.0 + elevation * (-518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711)))
    else:
        corr = -20.774 / te
    return corr / 3600.0


def solar_position(lat: float, lon: float, when_utc: datetime) -> Dict[str, float]:
    """Sun elevation and azimuth for a place and a UTC datetime (NOAA equations).

    ``lat`` is degrees north (negative south), ``lon`` degrees east (negative
    west). Returns declination, equation of time (minutes), hour angle, true and
    apparent (refraction corrected) elevation, azimuth clockwise from north and
    the solar noon as minutes past UTC midnight.
    """
    if not -90.0 <= lat <= 90.0:
        raise ValueError("lat must be between -90 and 90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("lon must be between -180 and 180")
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)
    jd = _julian_day(when_utc)
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * mr) * (0.019993 - 0.000101 * t) + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = math.radians(125.04 - 1934.136 * t)
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega)
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(omega)
    eps_r = math.radians(eps)
    decl = math.degrees(math.asin(math.sin(eps_r) * math.sin(math.radians(app_long))))
    y = math.tan(eps_r / 2.0) ** 2
    l0r = math.radians(l0)
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r) - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr)
    )
    minutes_utc = when_utc.hour * 60.0 + when_utc.minute + when_utc.second / 60.0
    tst = (minutes_utc + eot + 4.0 * lon) % 1440.0
    ha = tst / 4.0 - 180.0
    if ha < -180.0:
        ha += 360.0
    lat_r, decl_r, ha_r = math.radians(lat), math.radians(decl), math.radians(ha)
    cos_zenith = math.sin(lat_r) * math.sin(decl_r) + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation = 90.0 - zenith
    sin_zenith = math.sin(math.radians(zenith))
    if abs(sin_zenith) < 1e-9 or abs(math.cos(lat_r)) < 1e-9:
        azimuth = 180.0 if lat >= 0 else 0.0
    else:
        cos_az = (math.sin(lat_r) * cos_zenith - math.sin(decl_r)) / (math.cos(lat_r) * sin_zenith)
        cos_az = max(-1.0, min(1.0, cos_az))
        az = math.degrees(math.acos(cos_az))
        azimuth = (az + 180.0) % 360.0 if ha > 0 else (540.0 - az) % 360.0
    apparent = elevation + _refraction_deg(elevation)
    solar_noon = (720.0 - 4.0 * lon - eot) % 1440.0
    return {
        "declination": round(decl, 4),
        "equation_of_time_min": round(eot, 3),
        "hour_angle": round(ha, 4),
        "elevation": round(apparent, 4),
        "true_elevation": round(elevation, 4),
        "azimuth": round(azimuth, 4),
        "zenith": round(90.0 - apparent, 4),
        "solar_noon_utc_min": round(solar_noon, 2),
        "julian_day": round(jd, 5),
    }


def local_to_utc(date: str, time: str, utc_offset_hours: float) -> datetime:
    """Build a UTC datetime from 'YYYY-MM-DD', 'HH:MM' (or 'HH:MM:SS') and an offset in hours (AEDT is 11, PST is -8)."""
    parts = [int(p) for p in time.split(":")]
    while len(parts) < 3:
        parts.append(0)
    y, mo, d = (int(p) for p in date.split("-"))
    local = datetime(y, mo, d, parts[0], parts[1], parts[2], tzinfo=timezone(timedelta(hours=float(utc_offset_hours))))
    return local.astimezone(timezone.utc)


def sun_direction(elevation: float, azimuth: float) -> List[float]:
    """Unit vector from the ground toward the sun in a Y up scene with north at -Z and east at +X."""
    e, a = math.radians(elevation), math.radians(azimuth)
    return [round(math.sin(a) * math.cos(e), 6), round(math.sin(e), 6), round(-math.cos(a) * math.cos(e), 6)]


def sun_light_rotation(elevation: float, azimuth: float) -> List[float]:
    """Euler XYZ rotation (degrees) for a Maya light that shines along -Z so it points from the sun toward the ground."""
    return [round(-elevation, 4), round((180.0 - azimuth) % 360.0, 4), 0.0]


def aim_rotation(position: Sequence[float], target: Sequence[float]) -> List[float]:
    """Euler XYZ rotation (degrees) that points a -Z emitting light at ``target`` from ``position``."""
    d = [float(target[i]) - float(position[i]) for i in range(3)]
    length = math.sqrt(sum(v * v for v in d)) or 1.0
    dx, dy, dz = (v / length for v in d)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, dy))))
    yaw = math.degrees(math.atan2(-dx, -dz))
    return [round(pitch, 4), round(yaw, 4), 0.0]


# colour temperature --------------------------------------------------------
def srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def kelvin_to_rgb(kelvin: float, linear: bool = True) -> List[float]:
    """Tanner Helland black body approximation, normalised 0..1. Linear by default (sRGB decoded), valid 1000..40000 K."""
    k = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if k <= 66.0:
        r = 255.0
        g = 99.4708025861 * math.log(k) - 161.1195681661
        b = 0.0 if k <= 19.0 else 138.5177312231 * math.log(k - 10.0) - 305.0447927307
    else:
        r = 329.698727446 * ((k - 60.0) ** -0.1332047592)
        g = 288.1221695283 * ((k - 60.0) ** -0.0755148492)
        b = 255.0
    rgb = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    if linear:
        rgb = [srgb_to_linear(v) for v in rgb]
    return [round(v, 5) for v in rgb]


# exposure ------------------------------------------------------------------
def ev_from(iso: float, fstop: float, shutter: float) -> float:
    """EV100 from ISO, f-number and shutter time in seconds (1/125 s is 0.008). ISO 100 f/2.8 1/125 gives about 9.9."""
    if iso <= 0 or fstop <= 0 or shutter <= 0:
        raise ValueError("iso, fstop and shutter must be positive")
    return round(math.log2(fstop * fstop / shutter) - math.log2(iso / 100.0), 3)


def ev_to_settings(ev: float, iso: float = 100.0, fstop: float = 5.6) -> Dict[str, float]:
    """Shutter time (seconds) that exposes ``ev`` at the given ISO and f-number."""
    shutter = fstop * fstop / (2.0 ** (ev + math.log2(iso / 100.0)))
    return {"iso": iso, "fstop": fstop, "shutter": shutter, "shutter_fraction": "1/%d" % round(1.0 / shutter) if shutter < 1 else "%.1f s" % shutter}


def ev_from_illuminance(lux: float, iso: float = 100.0) -> float:
    """Incident light meter reading: EV = log2(E * S / C) with C = 250."""
    if lux <= 0:
        return -20.0
    return round(math.log2(lux * iso / INCIDENT_METER_CONSTANT), 3)


def illuminance_from_ev(ev: float, iso: float = 100.0) -> float:
    return round(INCIDENT_METER_CONSTANT * (2.0 ** ev) / iso, 3)


def exposure_value_to_arnold(ev: float, reference_ev: float = ARNOLD_REFERENCE_EV) -> float:
    """Camera aiExposure (stops) for a scene metered at ``ev`` when lights use the AutoMaya scale (EV 15 is aiExposure 0)."""
    return round(reference_ev - ev, 3)


def arnold_to_exposure_value(ai_exposure: float, reference_ev: float = ARNOLD_REFERENCE_EV) -> float:
    return round(reference_ev - ai_exposure, 3)


def split_intensity_exposure(value: float) -> Tuple[float, float]:
    """Split a raw multiplier into Arnold style (intensity in 1..2, integer-ish exposure stops)."""
    if value <= 0:
        return 0.0, 0.0
    exposure = math.floor(math.log2(value))
    return round(value / (2.0 ** exposure), 4), float(exposure)


# photometry to Arnold -------------------------------------------------------
def lux_to_arnold_irradiance(lux: float) -> float:
    """Arnold irradiance (a distant light's intensity) for an illuminance in lux under the 100 000 lux = 1 convention."""
    return lux / SUN_LUX


def lumens_to_watts(lumens: float, efficacy: float = LUMENS_PER_WATT) -> float:
    return lumens / efficacy


def watts_to_lumens(watts: float, efficacy: float) -> float:
    """Electrical watts to lumens using a luminous efficacy (incandescent about 15, halogen 20, fluorescent 60, LED 90 lm/W)."""
    return watts * efficacy


def lumens_to_arnold_intensity(lumens: float, solid_angle_sr: float = 4.0 * math.pi, scene_unit_cm: float = SCENE_UNIT_CM) -> Dict[str, float]:
    """Arnold intensity for a point or normalised area light emitting ``lumens``.

    Luminous intensity I = lumens / solid angle (candela), which equals the
    illuminance in lux at one metre. Under the 100 000 lux = 1 convention that is
    I / 100 000 Arnold irradiance at one metre; Arnold evaluates the inverse
    square law in scene units, so at 100 cm the light needs intensity
    I / 100 000 * 100^2. Returns the raw multiplier plus an intensity/exposure
    split and the numbers behind it.
    """
    if lumens < 0:
        raise ValueError("lumens must be >= 0")
    candela = lumens / solid_angle_sr
    raw = candela / SUN_LUX * (scene_unit_cm ** 2)
    intensity, exposure = split_intensity_exposure(raw)
    return {
        "lumens": lumens,
        "candela": round(candela, 4),
        "radiant_watts": round(lumens / LUMENS_PER_WATT, 5),
        "lux_at_1m": round(candela, 4),
        "arnold_intensity_raw": round(raw, 5),
        "intensity": intensity,
        "exposure": exposure,
        "solid_angle_sr": round(solid_angle_sr, 5),
        "scene_unit_cm": scene_unit_cm,
    }


def air_mass(elevation: float) -> float:
    """Kasten and Young relative optical air mass for a solar elevation in degrees."""
    e = max(elevation, -6.0)
    return 1.0 / (math.sin(math.radians(e)) + 0.50572 * ((e + 6.07995) ** -1.6364))


def sky_illuminance_estimate(elevation: float) -> Dict[str, float]:
    """Clear sky illuminance for a sun elevation in degrees: direct normal, diffuse and global horizontal (lux) plus EV100.

    Direct light uses a Beer-Lambert extinction of 0.21 per air mass on the
    133 800 lux extraterrestrial value; diffuse skylight is an empirical fit
    (about 15 000 lux at a high sun). Below the horizon a twilight curve drops
    roughly 2 orders of magnitude per 6 degrees (400 lux at sunset, 4 lux at
    civil dusk, 0.04 at nautical). Rough numbers meant for exposure planning.
    """
    if elevation <= 0.0:
        global_h = 400.0 * (10.0 ** (elevation / 3.0))
        return {"elevation": elevation, "direct_normal_lux": 0.0, "diffuse_horizontal_lux": round(global_h, 4), "global_horizontal_lux": round(global_h, 4), "ev100": ev_from_illuminance(global_h), "sun_above_horizon": False}
    m = air_mass(elevation)
    sin_e = math.sin(math.radians(elevation))
    direct_normal = EXTRATERRESTRIAL_ILLUMINANCE * math.exp(-CLEAR_SKY_EXTINCTION * m)
    diffuse = 15000.0 * (sin_e ** 0.5) + 400.0
    global_h = direct_normal * sin_e + diffuse
    return {
        "elevation": elevation,
        "air_mass": round(m, 3),
        "direct_normal_lux": round(direct_normal, 1),
        "diffuse_horizontal_lux": round(diffuse, 1),
        "global_horizontal_lux": round(global_h, 1),
        "ev100": ev_from_illuminance(global_h),
        "sun_above_horizon": True,
    }


def sun_kelvin_estimate(elevation: float) -> float:
    """Apparent sun colour temperature: about 5800 K high, sliding toward 2500 K at the horizon."""
    if elevation >= 40.0:
        return 5800.0
    if elevation <= 0.0:
        return 2500.0
    f = elevation / 40.0
    return round(2500.0 + (5800.0 - 2500.0) * (f ** 0.6), 0)


# colour helpers ------------------------------------------------------------
def luminance(rgb: Sequence[float]) -> float:
    return 0.2126 * float(rgb[0]) + 0.7152 * float(rgb[1]) + 0.0722 * float(rgb[2])


def rgb_to_hsv(rgb: Sequence[float]) -> List[float]:
    r, g, b = (float(v) for v in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d) % 6.0
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    h *= 60.0
    s = 0.0 if mx == 0 else d / mx
    return [h, s, mx]


def hsv_to_rgb(hsv: Sequence[float]) -> List[float]:
    h, s, v = float(hsv[0]) % 360.0, float(hsv[1]), float(hsv[2])
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    sector = int(h // 60.0) % 6
    r, g, b = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x), (0.0, x, c), (x, 0.0, c), (c, 0.0, x)][sector]
    return [round(r + m, 5), round(g + m, 5), round(b + m, 5)]


# measured materials --------------------------------------------------------
# baseColor is linear sRGB albedo for dielectrics and F0 reflectance for metals
# (Arnold's aiStandardSurface reads baseColor as F0 when metalness is 1).
# Sources: Physically Based (physicallybased.info), Lagarde "Feeding a physically
# based shading model", Hoffman SIGGRAPH physically based shading course notes,
# the Arnold aiStandardSurface docs for skin and glass starting values.
MEASURED_MATERIALS: Dict[str, Dict[str, Any]] = {
    "concrete": {"baseColor": [0.51, 0.51, 0.51], "roughness": 0.85, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                 "notes": "Cast concrete, albedo about 0.5 linear (physicallybased.info Concrete). Roughness high; add breakup."},
    "asphalt": {"baseColor": [0.07, 0.07, 0.07], "roughness": 0.9, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                "notes": "Fresh to mid aged asphalt, albedo 0.04 to 0.12 (physicallybased.info Asphalt). Wet asphalt: coat 1, coat roughness 0.1."},
    "brick": {"baseColor": [0.262, 0.095, 0.061], "roughness": 0.9, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
              "notes": "Red clay brick (physicallybased.info Brick). Mortar is close to concrete."},
    "plaster": {"baseColor": [0.76, 0.74, 0.70], "roughness": 0.8, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                "notes": "White interior plaster or drywall, albedo 0.7 to 0.8. Pure white walls above 0.85 are rare in reality."},
    "wood_oak": {"baseColor": [0.47, 0.32, 0.18], "roughness": 0.55, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                 "notes": "Bare oak (physicallybased.info Wood Oak range). Varnished: coat 1, coat roughness 0.15."},
    "wood_walnut": {"baseColor": [0.22, 0.13, 0.08], "roughness": 0.55, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                    "notes": "Dark walnut, albedo about 0.15 luminance."},
    "wood_pine": {"baseColor": [0.68, 0.50, 0.29], "roughness": 0.6, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                  "notes": "Pale pine, albedo about 0.5 luminance."},
    "painted_metal": {"baseColor": [0.6, 0.6, 0.6], "roughness": 0.35, "metalness": 0.0, "ior": 1.5, "coat": 1.0, "sss": 0.0, "transmission": 0.0,
                      "notes": "Gloss painted steel: a dielectric paint layer over metal, so metalness 0 with a coat. Change baseColor to the paint colour."},
    "steel": {"baseColor": [0.56, 0.57, 0.58], "roughness": 0.4, "metalness": 1.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
              "notes": "Iron/steel F0 (Lagarde table 0.56, 0.57, 0.58). Brushed: roughness 0.35 anisotropy 0.6; polished 0.15."},
    "aluminium": {"baseColor": [0.912, 0.914, 0.920], "roughness": 0.3, "metalness": 1.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
                  "notes": "Aluminium F0 (Lagarde/Hoffman). Anodised or cast aluminium is rougher, 0.5 to 0.7."},
    "copper": {"baseColor": [0.955, 0.638, 0.538], "roughness": 0.25, "metalness": 1.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
               "notes": "Copper F0 (Lagarde table). Oxidised copper turns into a dielectric green patina, metalness 0."},
    "gold": {"baseColor": [1.0, 0.782, 0.344], "roughness": 0.2, "metalness": 1.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
             "notes": "Gold F0 (Lagarde table 1.0, 0.782, 0.344)."},
    "glass": {"baseColor": [1.0, 1.0, 1.0], "roughness": 0.0, "metalness": 0.0, "ior": 1.52, "coat": 0.0, "sss": 0.0, "transmission": 1.0,
              "notes": "Clear window glass, IOR 1.52. Set base weight 0 for a clean transmission (the handler does this). Frosted: transmission roughness 0.3."},
    "water": {"baseColor": [1.0, 1.0, 1.0], "roughness": 0.0, "metalness": 0.0, "ior": 1.333, "coat": 0.0, "sss": 0.0, "transmission": 1.0,
              "notes": "Water IOR 1.333 at 20 C. Deep water needs transmission colour and depth; give the surface some wave displacement."},
    "rubber": {"baseColor": [0.023, 0.023, 0.023], "roughness": 0.7, "metalness": 0.0, "ior": 1.52, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
               "notes": "Black rubber, albedo about 0.02 (physicallybased.info Rubber)."},
    "leather": {"baseColor": [0.16, 0.09, 0.06], "roughness": 0.6, "metalness": 0.0, "ior": 1.45, "coat": 0.3, "sss": 0.0, "transmission": 0.0,
                "notes": "Brown leather, light coat for the finish. Matte leathers drop the coat."},
    "fabric": {"baseColor": [0.45, 0.42, 0.38], "roughness": 0.85, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
               "notes": "Cotton/linen, albedo 0.4 to 0.5. Sheen 0.5 with sheen roughness 0.4 sells the fibres; velvet sheen 1."},
    "skin": {"baseColor": [0.85, 0.57, 0.46], "roughness": 0.5, "metalness": 0.0, "ior": 1.4, "coat": 0.0, "sss": 1.0, "transmission": 0.0,
             "notes": "Light skin from the Arnold aiStandardSurface skin preset; sss radius about (1.0, 0.35, 0.2) cm scale 0.5. Darker skin tones scale the albedo down, not the SSS."},
    "snow": {"baseColor": [0.9, 0.9, 0.92], "roughness": 0.7, "metalness": 0.0, "ior": 1.31, "coat": 0.0, "sss": 0.6, "transmission": 0.0,
             "notes": "Fresh snow albedo 0.8 to 0.9 (one of the few materials legitimately above 0.85). Ice IOR 1.31."},
    "sand": {"baseColor": [0.52, 0.43, 0.30], "roughness": 0.9, "metalness": 0.0, "ior": 1.5, "coat": 0.0, "sss": 0.0, "transmission": 0.0,
             "notes": "Dry beach sand, albedo about 0.4 luminance; wet sand is about half as bright with a coat."},
    "grass": {"baseColor": [0.08, 0.20, 0.04], "roughness": 0.75, "metalness": 0.0, "ior": 1.4, "coat": 0.0, "sss": 0.2, "transmission": 0.0,
              "notes": "Grass lawn proxy, albedo about 0.15 luminance (physicallybased.info Grass). A real lawn is geometry; this is for ground planes."},
}

MATERIAL_ALIASES = {
    "oak": "wood_oak", "walnut": "wood_walnut", "pine": "wood_pine", "wood": "wood_oak", "aluminum": "aluminium", "iron": "steel",
    "metal": "steel", "cloth": "fabric", "cotton": "fabric", "drywall": "plaster", "tarmac": "asphalt", "road": "asphalt", "cement": "concrete",
}


def measured_material(name: str) -> Dict[str, Any]:
    """Look up a measured material by name or alias; raises KeyError with the valid names."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    key = MATERIAL_ALIASES.get(key, key)
    if key not in MEASURED_MATERIALS:
        raise KeyError("unknown material %r; choose one of: %s" % (name, ", ".join(sorted(MEASURED_MATERIALS))))
    out = dict(MEASURED_MATERIALS[key])
    out["name"] = key
    out["baseColor"] = list(out["baseColor"])
    return out


def material_names() -> List[str]:
    return sorted(MEASURED_MATERIALS)


# plausibility checks (shared by the material report) ------------------------
def material_issues(kind: str, base_color: Sequence[float] | None, roughness: float | None, metalness: float | None, ior: float | None, transmission: float | None = None) -> List[str]:
    """Return plain language problems with a shader's values; empty when it looks plausible."""
    issues: List[str] = []
    lum = luminance(base_color) if base_color is not None else None
    metal = (metalness or 0.0) >= 0.5
    glassy = bool(transmission and transmission > 0.5)
    if lum is not None:
        if not metal and not glassy and lum > 0.9:
            issues.append("albedo %.2f is above 0.9; only fresh snow gets there, most whites sit at 0.7 to 0.85" % lum)
        if not metal and lum < 0.02 and not glassy:
            issues.append("albedo %.3f is nearly black; coal and black rubber still bounce 2 to 4 percent" % lum)
        if metal and lum < 0.3:
            issues.append("metal with a dark baseColor (%.2f); aiStandardSurface uses baseColor as F0, so use the measured metal colour (steel 0.56, aluminium 0.91)" % lum)
    if roughness is not None and roughness <= 0.0 and not metal and not glassy:
        issues.append("roughness 0 on a dielectric renders as a perfect mirror; even glossy plastic is 0.1 to 0.2")
    if ior is not None and (ior < 1.0 or ior > 2.5) and kind != "aiFlat":
        issues.append("specular IOR %.2f is outside 1.0 to 2.5 (plastics 1.45 to 1.55, glass 1.5, water 1.33, diamond 2.42)" % ior)
    if metal and transmission and transmission > 0.0:
        issues.append("metalness and transmission are both on; metals do not transmit light")
    return issues
