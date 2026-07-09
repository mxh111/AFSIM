# AFSim XIO Bridge Notes

## Current Status

`app/services/xio_bridge.py` provides a reserved `XioBridge` interface with:

- `start(run_id, host, port)`
- `stop(run_id)`
- `frames(run_id)`

The bridge currently returns `unsupported` and never emits `authoritative=true` frames. This is intentional: AFSim demo files such as `demos/aea_iads/xio_interface.txt` and `demos/distributed_operations/xio_setup.txt` show that XIO can be configured, but this project does not yet know the exact runtime wire format for those messages.

`GET /api/afsim/bridges` reports XIO as `unsupported` until a concrete message schema and transport behavior are confirmed.

## Data Format To Confirm

Before parsing XIO as a real-time authoritative source, confirm:

- Whether the configured XIO endpoint uses UDP, TCP, multicast, files, or an AFSim-specific plugin callback.
- Message framing: fixed length, delimiter-based, length-prefixed, binary records, or text records.
- Coordinate representation: geodetic latitude/longitude/altitude, ECEF, NED, local tangent plane, or scenario-specific coordinates.
- Entity identity fields and lifecycle semantics.
- Time fields and whether they represent wall-clock time, simulation time, or step index.
- Event categories for detections, tracks, weapons, communications, and platform state.
- Endianness and numeric units for binary payloads.

## Possible Integration Routes

1. Python direct parser

   Add a UDP/TCP listener in `XioBridge`, capture raw packets/messages under `runtime/xio_sessions/<run_id>/`, parse known records into `afsim-xio-frame.v1`, and only then mark frames authoritative.

2. AFSim C++ plugin to JSON

   Add an AFSim-side XIO/plugin adapter that converts internal simulation objects to a documented JSON stream. The Python backend can then tail or subscribe to that JSON stream with a much smaller parser surface.

3. Hybrid capture-first workflow

   Capture XIO bytes first, attach metadata from the AFSim input files, and build a replayable fixture corpus before implementing any parser. This keeps the UI honest while the format is still being characterized.

## Guardrails

- Do not label XIO output as authoritative until the parser maps entity state, time, and units correctly.
- Do not add large third-party dependencies until the message format has been verified against real AFSim XIO output.
- Do not write into the AFSim installation tree; store captures and diagnostics under this project runtime directory.
