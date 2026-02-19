"""
Gradio web interface for the Solar Pipeline.

Drag-and-drop an IFC file → get a full solar analysis report with LEED score.

Run:
    python app.py
"""

import sys
import os
import tempfile
import logging
from pathlib import Path

# Ensure repo root is in sys.path so final_pipeline is importable
_APP_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _APP_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import gradio as gr

from final_pipeline.analyze import analyze_ifc
from final_pipeline.config import (
    DEFAULT_CONSUMPTION_KWH_PER_M2,
    PANEL_EFFICIENCY,
    SYSTEM_LOSSES,
    DEFAULT_ANGLE_TOLERANCE_DEG,
    __version__,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")


# ── Core analysis callback ────────────────────────────────────────────────────

def run_analysis(
    ifc_file,
    lat: str,
    lon: str,
    consumption_benchmark: float,
    panel_eff: float,
    call_api: bool,
):
    """Called by Gradio when the user clicks Analyze."""

    # ── Validate file ─────────────────────────────────────────────────────
    if ifc_file is None:
        return (
            "❌ **No file uploaded.** Please drag-and-drop an .ifc file.",
            None,
            None,
        )

    ifc_path = ifc_file  # Gradio gives us the temp file path as a string

    if not os.path.isfile(ifc_path):
        return "❌ **File not found.** Upload failed.", None, None

    # ── Parse optional lat/lon ────────────────────────────────────────────
    lat_val = _parse_float(lat)
    lon_val = _parse_float(lon)

    # ── Monkey-patch panel efficiency if user changed it ──────────────────
    import final_pipeline.config as cfg
    original_eff = cfg.PANEL_EFFICIENCY
    cfg.PANEL_EFFICIENCY = panel_eff

    # ── Run the pipeline ──────────────────────────────────────────────────
    try:
        result = analyze_ifc(
            ifc_path,
            lat=lat_val,
            lon=lon_val,
            consumption_kwh_per_m2=consumption_benchmark,
            call_api=call_api,
        )
    except Exception as exc:
        cfg.PANEL_EFFICIENCY = original_eff
        return f"❌ **Error:** {exc}", None, None
    finally:
        cfg.PANEL_EFFICIENCY = original_eff

    # ── Format outputs ────────────────────────────────────────────────────
    if not result.get("ok"):
        return f"❌ **{result.get('error', 'Unknown error')}**", None, None

    report_md = _format_report(result)
    segment_table = _format_segment_table(result)
    score_display = _format_score_badge(result)

    return score_display, report_md, segment_table


def _parse_float(s: str) -> float | None:
    if not s or not s.strip():
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_score_badge(result: dict) -> str:
    score = result["leed_score"]
    prod = result["total_production"]

    if score >= 100:
        emoji = "⭐"
        label = "Net-Zero Energy!"
        color = "green"
    elif score >= 50:
        emoji = "🟢"
        label = "Strong Renewable Coverage"
        color = "green"
    elif score >= 10:
        emoji = "🟡"
        label = "Moderate Coverage"
        color = "orange"
    else:
        emoji = "🔴"
        label = "Low Coverage"
        color = "red"

    return (
        f"## {emoji} LEED Score: **{score:.1f}%**\n\n"
        f"**{label}**\n\n"
        f"Annual solar production: **{prod:,.0f} kWh/yr**"
    )


def _format_report(result: dict) -> str:
    def _val(key, unit="m²"):
        v = result.get(key)
        if v is None:
            return "N/A"
        if unit == "°":
            return f"{v:,.1f}°"
        return f"{v:,.1f} {unit}"

    md = f"""### 📋 Building Report — {result['project_name']}

| Property | Value |
|----------|-------|
| **IFC File** | `{result['ifc_file']}` |
| **Location** | {result['latitude']}, {result['longitude']} |
| **True North** | {_val('true_north_deg', '°')} |
| **Window Area** | {_val('window_area_m2')} |
| **Floor Area** | {_val('floor_area_m2')} |
| **Roof Area (property-set)** | {_val('roof_area_m2')} |
| **Roof Area (3D geometry)** | {_val('total_roof_area_m2')} |

---

### ⚡ Solar Production Summary

| Metric | Value |
|--------|-------|
| **Total Roof Area** | {result['total_roof_area_m2']:,.1f} m² |
| **System Capacity** | {result['total_capacity_kw']:,.1f} kW |
| **Annual Production** | {result['total_production']:,.0f} kWh/yr |
| **Est. Consumption** | {result['consumption']:,.0f} kWh/yr |
| **LEED Score** | {result['leed_score']:.1f}% |
| **Roof Segments** | {len(result['segments'])} |
"""
    return md


def _format_segment_table(result: dict) -> str:
    if not result.get("segments"):
        return "No segments found."

    rows = "| Segment | Area (m²) | Tilt (°) | Azimuth (°) | Capacity (kW) | Production (kWh/yr) |\n"
    rows += "|---------|-----------|----------|-------------|---------------|---------------------|\n"
    for s in result["segments"]:
        rows += (
            f"| {s['id']} | {s['area']:,.1f} | {s['tilt']:.1f} | "
            f"{s['azimuth']:.1f} | {s['capacity_kw']:,.1f} | "
            f"{s['annual_kwh']:,.0f} |\n"
        )

    total_area = sum(s["area"] for s in result["segments"])
    total_cap = sum(s["capacity_kw"] for s in result["segments"])
    total_kwh = sum(s["annual_kwh"] for s in result["segments"])
    rows += (
        f"| **TOTAL** | **{total_area:,.1f}** | — | — | "
        f"**{total_cap:,.1f}** | **{total_kwh:,.0f}** |\n"
    )
    return rows


# ── Gradio UI ─────────────────────────────────────────────────────────────────

def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="☀️ Lux.Ai Solar Analyser",
        theme=gr.themes.Soft(),
    ) as app:

        gr.Markdown(
            """
            # ☀️ Lux.Ai — Solar Production Analyser

            **Upload an IFC building file → get a solar energy score instantly.**

            The tool reads your building's 3D roof geometry, queries the
            NREL PVWatts solar database, and tells you what percentage of the
            building's energy consumption the roof solar panels could cover.
            """
        )

        with gr.Row():
            # ── Left column: Inputs ───────────────────────────────────────
            with gr.Column(scale=1):
                ifc_input = gr.File(
                    label="📁 Upload IFC File",
                    file_types=[".ifc"],
                    type="filepath",
                )

                gr.Markdown("#### 📍 Location Override *(optional)*")
                gr.Markdown(
                    "*Leave blank to auto-detect from the IFC file. "
                    "Fill in only if the file has no coordinates.*"
                )
                with gr.Row():
                    lat_input = gr.Textbox(
                        label="Latitude",
                        placeholder="e.g. 48.14",
                        scale=1,
                    )
                    lon_input = gr.Textbox(
                        label="Longitude",
                        placeholder="e.g. 11.58",
                        scale=1,
                    )

                gr.Markdown("#### ⚙️ Settings")
                consumption_input = gr.Slider(
                    minimum=50,
                    maximum=500,
                    value=DEFAULT_CONSUMPTION_KWH_PER_M2,
                    step=10,
                    label="Energy benchmark (kWh/m²/yr)",
                    info="ASHRAE: Office=150, Residential=100, Hospital=300",
                )
                panel_eff_input = gr.Slider(
                    minimum=0.10,
                    maximum=0.30,
                    value=PANEL_EFFICIENCY,
                    step=0.01,
                    label="Panel efficiency",
                    info="0.15 = budget, 0.20 = premium, 0.25 = cutting-edge",
                )
                api_toggle = gr.Checkbox(
                    value=True,
                    label="Query PVWatts API (requires internet)",
                    info="Uncheck for offline mode — geometry only, no kWh values",
                )

                analyze_btn = gr.Button(
                    "🔍 Analyse",
                    variant="primary",
                    size="lg",
                )

            # ── Right column: Outputs ─────────────────────────────────────
            with gr.Column(scale=2):
                score_output = gr.Markdown(
                    label="Score",
                    value="*Upload a file and click Analyse to see results.*",
                )
                report_output = gr.Markdown(label="Report")
                segments_output = gr.Markdown(label="Roof Segments")

        # ── Wire button ──────────────────────────────────────────────────
        analyze_btn.click(
            fn=run_analysis,
            inputs=[
                ifc_input,
                lat_input,
                lon_input,
                consumption_input,
                panel_eff_input,
                api_toggle,
            ],
            outputs=[score_output, report_output, segments_output],
        )

        gr.Markdown(
            f"""
            ---
            *Solar Pipeline v{__version__} · Powered by
            [NREL PVWatts v8](https://developer.nrel.gov/docs/solar/pvwatts/v8/)
            + [ifcopenshell](https://ifcopenshell.org/) ·
            [Lux.Ai](https://github.com/) for Architecture & Urbanism*
            """
        )

    return app


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = build_app()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        inbrowser=True,
    )
