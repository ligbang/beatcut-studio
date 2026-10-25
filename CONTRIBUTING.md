# Contributing

Thanks for helping make beat-driven editing more accessible.

1. Use Python 3.11 or newer, FFmpeg, and an isolated virtual environment.
2. Install `requirements-dev.txt` and run `python -m pytest -q`.
3. Keep fixes scoped. Add regression tests for timing, source-range, beat-analysis, or export bugs.
4. Test FCPXML with Apple's DTD and with Final Cut Pro when available. DTD validity alone does not prove a successful import.
5. Include the exact frame rate, media type, reproduction steps and expected result in bug reports. Use synthetic or redistributable samples.

Do not submit `.data`, private media, credentials, local export paths, or proprietary commercial-plugin code. Contributions are licensed under the repository's MIT license.
