# STEPforge — STEP/IGES Importer for Blender

Assembly-aware STEP and IGES import for Blender: full assembly trees as
nested collections, one shared mesh per unique part (CAD instancing done
right), unit conversion, CAD colors, rule-based PBR materials, geometry
healing, re-import from source, and CAD surface normals for shading that
matches the CAD model.

## Features

- **Assembly tree import** — STEP (.stp/.step) and IGES (.igs/.iges);
  nested collections per assembly level (or flat), optional A–Z sorting.
- **Shared instance meshes** — exploded exports that duplicate every
  product definition are deduplicated by tessellated content; instance
  transforms become object matrices.
- **CAD surface normals** (smooth shading) — exact per-vertex surface
  normals imported as custom split normals: curved surfaces shade like
  the CAD model, hard edges stay crisp. Geometry winding is recalculated
  consistent-outward, so imports never render black. Zero-area
  degenerate triangles from tessellation welding are dropped
  automatically.
- **Cosmetic thread cleanup** — PTC Creo cosmetic-thread shells
  (floating all-cylinder face groups) are detected and removed before
  tessellation, revealing the real thread geometry underneath
  (toggleable, on by default).
- **Automatic unit conversion** (mm/cm/µm/inch → metres).
- **CAD colors** to viewport color and optionally shared materials.
- **Rule-based PBR materials** — ordered rules match part name (regex) or
  CAD color; ships with default and GMI-product policies, custom JSON
  rulesets supported.
- **Quality presets** (Draft → Extra fine), batch import.
- **Geometry healing** (ShapeFix) for dirty CAD.
- **Re-import from Source** — rebuild an import after the CAD file
  changed, in place.
- **Viewport drag-and-drop** of STEP files.

## Requirements

- Blender 4.2 or newer (developed on 5.4).
- `cadquery-ocp` 7.8.x in Blender's bundled Python
  (**pin `7.8.1.1.post1`** — 8.0.1.1.0 ships broken auto-generated
  submodules). Install once, e.g. for a system `blender`:

  ```bash
  "$(dirname "$(readlink -f "$(which blender)")")"/../share/blender/*/python/bin/python* -m pip install "cadquery-ocp==7.8.1.1.post1"
  ```

  or find Blender's python under its install dir (`<blender>/python/bin/python3.*`).

## Install

1. Download `stepforge-<version>.zip` from [Releases](../../releases).
2. Blender → Edit → Preferences → Add-ons → Install… → pick the zip.
3. Enable **Import-Export: STEPforge**.

## Usage

- **File > Import > STEP (.stp/.step) / IGES (.igs/.iges)**
- **Object > STEP: Re-import from Source** on an imported root after the
  CAD file changed.
- Smooth shading with CAD normals: enable **Smooth shading** in the
  import options. Per-mesh stats (CAD-normal faces, degenerate drops) are
  stored as mesh custom properties (`stepforge/*`).

## License

GPL-3.0 — Blender addons derive from Blender's GPL API.
