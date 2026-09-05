"""craft_light tools: sun and sky, HDRI domes, three point and studio rigs, portals, practicals, exposure and a light report.

The solar, Kelvin and photometric helpers also run server side (no Maya needed)
through ``automaya_mcp.science`` so the agent can reason before touching the scene.
"""
from __future__ import annotations

from typing import List

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .. import science as sci
from ._base import READ, WRITE, ToolContext, dumps


class SunSkyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(default=51.5, description="Latitude in degrees, north positive", ge=-90, le=90, examples=[-33.87])
    lon: float = Field(default=-0.12, description="Longitude in degrees, east positive", ge=-180, le=180, examples=[151.21])
    date: str = Field(default="2026-06-21", description="Local date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(default="12:00", description="Local time HH:MM (24 h)", pattern=r"^\d{1,2}:\d{2}(:\d{2})?$")
    utc_offset: float = Field(default=0.0, description="Local UTC offset in hours including daylight saving (AEDT 11, CET 1, PDT -7)", ge=-14, le=14)
    intensity: float = Field(default=1.0, description="Multiplier on the physical sun and sky", ge=0, le=100)
    turbidity: float = Field(default=3.0, description="Haze: 2 crisp mountain air, 3 clear, 6 hazy city, 10 smog", ge=1, le=10)
    name: str = Field(default="sun", description="Base name for the sun light and sky nodes", max_length=100)
    sun_size: float = Field(default=0.51, description="Sun disc angle in degrees (real sun 0.51); bigger softens shadows", ge=0, le=90)
    ground_albedo: float = Field(default=0.2, description="Physical sky ground albedo", ge=0, le=1)


class HdriDomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(..., description="Path to an .hdr or .exr environment", min_length=1, examples=["/hdri/studio_small_09_4k.exr"])
    rotation: float = Field(default=0.0, description="Rotation about Y in degrees to turn the sun/key around", ge=-360, le=360)
    intensity: float = Field(default=1.0, ge=0, le=100)
    exposure: float = Field(default=0.0, description="Stops", ge=-20, le=20)
    camera_visible: bool = Field(default=True, description="Show the HDRI as the background")
    ground_projection: bool = Field(default=False, description="Return a hint for making objects sit on the HDRI ground (Arnold has no dome projection)")
    name: str = Field(default="hdriDome", max_length=100)


class ThreePointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: List[str] | None = Field(default=None, description="Subject transforms; omit to use the selection")
    key_stops: float = Field(default=0.0, description="Key offset in stops from the computed correct exposure", ge=-10, le=10)
    fill_stops: float = Field(default=-2.0, description="Fill relative to key in stops (-2 is a 4:1 ratio)", ge=-10, le=5)
    rim_stops: float = Field(default=1.0, description="Rim relative to key in stops", ge=-10, le=5)
    key_angle: float = Field(default=45.0, description="Key azimuth from the camera axis in degrees, positive to camera left", ge=-180, le=180)
    key_elevation: float = Field(default=30.0, description="Key elevation in degrees", ge=-30, le=89)
    softness: float = Field(default=1.0, description="Light size relative to the subject radius; 1 is soft, 0.2 is hard", gt=0, le=10)
    kelvin: float = Field(default=5600.0, description="Key colour temperature", ge=1000, le=40000)
    fill_kelvin: float | None = Field(default=None, description="Fill colour temperature; default matches the key", ge=1000, le=40000)
    rim_kelvin: float | None = Field(default=None, description="Rim colour temperature", ge=1000, le=40000)
    distance_factor: float = Field(default=2.5, description="Light distance as a multiple of the subject radius", ge=1.1, le=20)
    key_exposure: float | None = Field(default=None, description="Override the computed key exposure in stops", ge=-10, le=40)
    prefix: str = Field(default="", description="Name prefix for the lights", max_length=50)


class StudioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: List[str] | None = Field(default=None, description="Subject transforms; omit to use the selection")
    style: str = Field(default="softbox", description="softbox | butterfly | rembrandt | rim_heavy", pattern="^(softbox|butterfly|rembrandt|rim_heavy)$")
    kelvin: float | None = Field(default=None, description="Override the style's colour temperature", ge=1000, le=40000)
    prefix: str | None = Field(default=None, description="Name prefix; default is the style name", max_length=50)


class PortalsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    windows: List[str] = Field(..., description="Window opening transforms (planes or frame meshes); one portal each", min_length=1)
    name: str = Field(default="portal", max_length=100)


class PracticalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="bulb", description="bulb | tube | neon | candle | screen", pattern="^(bulb|tube|neon|candle|screen)$")
    lumens: float | None = Field(default=None, description="Luminous flux; default is typical for the kind (bulb 800, tube 2800, candle 13)", ge=0)
    watts: float | None = Field(default=None, description="Electrical watts, converted with a typical efficacy when lumens is not given", ge=0)
    kelvin: float | None = Field(default=None, description="Colour temperature; default per kind (bulb 2700, tube 4000, candle 1850)", ge=1000, le=40000)
    position: List[float] | None = Field(default=None, min_length=3, max_length=3, description="World position in cm")
    rotate: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Rotation in degrees")
    name: str | None = Field(default=None, max_length=100)
    color: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Override colour [r, g, b] linear, e.g. for neon")


class ExposureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera transform or shape; default persp")
    ev: float | None = Field(default=None, description="Scene EV100 (sunny 15, overcast 12, bright interior 7, candle 2)", ge=-10, le=25)
    iso: float | None = Field(default=None, description="ISO, used with fstop and shutter when ev is omitted", gt=0)
    fstop: float | None = Field(default=None, gt=0)
    shutter: float | None = Field(default=None, description="Shutter time in seconds (1/125 is 0.008)", gt=0)
    reference_ev: float = Field(default=15.0, description="EV that maps to aiExposure 0 (AutoMaya light scale)", ge=0, le=25)


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera: str | None = Field(default=None, description="Camera to read aiExposure from; default persp")
    target: List[float] | None = Field(default=None, min_length=3, max_length=3, description="Point to evaluate illuminance at; default is the scene centre")


class SolarInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(..., ge=-90, le=90, description="Latitude, north positive")
    lon: float = Field(..., ge=-180, le=180, description="Longitude, east positive")
    date: str = Field(..., description="Local date YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(default="12:00", description="Local time HH:MM", pattern=r"^\d{1,2}:\d{2}(:\d{2})?$")
    utc_offset: float = Field(default=0.0, description="Local UTC offset in hours", ge=-14, le=14)


class KelvinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kelvin: float = Field(..., description="Colour temperature 1000..40000 (candle 1850, tungsten 3200, daylight 5600, overcast 6500, blue sky 10000)", ge=1000, le=40000)


class LuxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lux: float | None = Field(default=None, description="Illuminance in lux (sun 100000, overcast 1000, office 500, street at night 10)", ge=0)
    lumens: float | None = Field(default=None, description="Luminous flux of a point or area light", ge=0)
    solid_angle_sr: float = Field(default=12.566370614359172, description="Emission solid angle: 4 pi omnidirectional, 2 pi hemisphere, pi for a flat panel", gt=0)


def register(mcp: FastMCP, ctx: ToolContext) -> None:
    @mcp.tool(name="maya_light_sun_sky", annotations={"title": "Sun and sky from place and time", **WRITE})
    async def maya_light_sun_sky(params: SunSkyInput) -> str:
        """Build a physically placed sun and sky for a latitude, longitude, date and
        local time (NOAA solar equations). With Arnold: aiPhysicalSky driving an
        aiSkyDomeLight plus a directional sun with aiExposure at the real elevation
        and azimuth; without Arnold: the directional sun only (path 'maya'). Returns
        elevation, azimuth, illuminance, EV and the camera aiExposure to match."""
        return await ctx.run("light.sun_sky", params.model_dump())

    @mcp.tool(name="maya_light_hdri_dome", annotations={"title": "HDRI skydome", **WRITE})
    async def maya_light_hdri_dome(params: HdriDomeInput) -> str:
        """Create an aiSkyDomeLight lit by an HDRI (Raw colour space) with rotation,
        intensity, exposure and camera visibility. Arnold only. Use maya_light_sun_sky
        instead when you need a specific time of day and no HDRI is at hand."""
        return await ctx.run("light.hdri_dome", params.model_dump())

    @mcp.tool(name="maya_light_three_point", annotations={"title": "Three point rig", **WRITE})
    async def maya_light_three_point(params: ThreePointInput) -> str:
        """Place key, fill and rim area lights around a subject from its bounding
        box. Ratios are in stops, softness is light size relative to the subject,
        colour is in Kelvin. The key exposure is computed so a white surface renders
        near 0.8 at camera aiExposure 0. Falls back to Maya areaLights without Arnold."""
        return await ctx.run("light.three_point", params.model_dump())

    @mcp.tool(name="maya_light_studio", annotations={"title": "Studio lighting preset", **WRITE})
    async def maya_light_studio(params: StudioInput) -> str:
        """Preset studio setups on a subject: softbox (product), butterfly (beauty),
        rembrandt (dramatic portrait) or rim_heavy (moody silhouette). Built on the
        three point rig with tuned angles, ratios, softness and Kelvin."""
        return await ctx.run("light.studio", params.model_dump())

    @mcp.tool(name="maya_light_portals", annotations={"title": "Interior light portals", **WRITE})
    async def maya_light_portals(params: PortalsInput) -> str:
        """Add an aiLightPortal over each window opening so skydome light enters an
        interior cleanly with less noise. Needs Arnold and a skydome (HDRI or sun
        sky). Windows are the opening transforms; size and orientation come from
        their bounding boxes."""
        return await ctx.run("light.interior_portals", params.model_dump())

    @mcp.tool(name="maya_light_practical", annotations={"title": "Practical light", **WRITE})
    async def maya_light_practical(params: PracticalInput) -> str:
        """Create a practical (bulb, tube, neon, candle, screen) with intensity and
        exposure derived from lumens or watts and colour from Kelvin. Arnold builds
        an aiAreaLight with the right emitter shape; without Arnold a pointLight.
        Returns the photometry so you can sanity check the numbers."""
        return await ctx.run("light.practical", params.model_dump())

    @mcp.tool(name="maya_light_exposure", annotations={"title": "Camera exposure", **WRITE})
    async def maya_light_exposure(params: ExposureInput) -> str:
        """Set the camera aiExposure from a scene EV or ISO, f-stop and shutter.
        EV 15 maps to aiExposure 0 under the AutoMaya light scale, so an interior
        at EV 7 gets +8. Without Arnold the viewport exposure is set instead.
        Returns the EV, illuminance and matching real camera settings."""
        return await ctx.run("light.exposure", params.model_dump())

    @mcp.tool(name="maya_light_report", annotations={"title": "Light report", **READ})
    async def maya_light_report(params: ReportInput) -> str:
        """List every light with its effective intensity and an illuminance guess at
        a target point, the summed scene EV and whether the camera aiExposure is
        over or under. Use it before rendering to catch blown out or black frames."""
        return await ctx.run("light.light_report", params.model_dump())

    @mcp.tool(name="maya_light_solar", annotations={"title": "Solar position (offline)", **READ})
    async def maya_light_solar(params: SolarInput) -> str:
        """Compute sun elevation, azimuth, illuminance, EV and colour temperature for
        a place, date and local time without touching Maya (NOAA equations). Use it
        to plan a shot before calling maya_light_sun_sky."""
        try:
            when = sci.local_to_utc(params.date, params.time, params.utc_offset)
            sun = sci.solar_position(params.lat, params.lon, when)
        except ValueError as exc:
            return "Error: %s" % exc
        return dumps({
            "utc": when.strftime("%Y-%m-%d %H:%M"), "solar": sun, "illuminance": sci.sky_illuminance_estimate(sun["elevation"]),
            "sun_kelvin": sci.sun_kelvin_estimate(sun["elevation"]), "sun_direction": sci.sun_direction(sun["elevation"], sun["azimuth"]),
            "maya_light_rotation": sci.sun_light_rotation(sun["elevation"], sun["azimuth"]),
        })

    @mcp.tool(name="maya_light_kelvin_to_rgb", annotations={"title": "Kelvin to RGB (offline)", **READ})
    async def maya_light_kelvin_to_rgb(params: KelvinInput) -> str:
        """Convert a colour temperature to linear RGB (and sRGB) without Maya, using
        the Tanner Helland black body approximation. 6500 K is near white."""
        return dumps({"kelvin": params.kelvin, "rgb_linear": sci.kelvin_to_rgb(params.kelvin), "rgb_srgb": sci.kelvin_to_rgb(params.kelvin, linear=False)})

    @mcp.tool(name="maya_light_lux_to_arnold", annotations={"title": "Lux and lumens to Arnold (offline)", **READ})
    async def maya_light_lux_to_arnold(params: LuxInput) -> str:
        """Convert lux to an Arnold distant light intensity and EV, or lumens to a
        point/area light intensity and exposure, under the AutoMaya convention
        (100000 lux = 1, EV 15 = aiExposure 0, 683 lm/W, cm units). No Maya needed."""
        if params.lux is None and params.lumens is None:
            return "Error: pass lux and/or lumens"
        out = {"convention": "100000 lux = 1 Arnold irradiance unit; EV 15 = aiExposure 0; 683 lm/W; scene units cm"}
        if params.lux is not None:
            ev = sci.ev_from_illuminance(params.lux)
            out.update({"lux": params.lux, "distant_intensity": round(sci.lux_to_arnold_irradiance(params.lux), 6), "ev100": ev, "camera_ai_exposure": sci.exposure_value_to_arnold(ev)})
        if params.lumens is not None:
            out["point_light"] = sci.lumens_to_arnold_intensity(params.lumens, solid_angle_sr=params.solid_angle_sr)
        return dumps(out)
