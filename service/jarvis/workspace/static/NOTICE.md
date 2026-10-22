# Third-party notices: Living Workspace front-end

Everything in this folder was written for Jarvis WorkOS, except the files under `vendor/three/`.
The parts listed below are adapted from, or are, MIT-licensed work.

## Open Intelligent UI (CopilotKit)

Adapted, not copied verbatim:

- The design-token names in `style.css` and in the sandbox stylesheet inside `sandbox-runtime.js`:
  `--color-background-*`, `--color-text-*`, `--color-border-*`, `--font-*` and `--border-radius-*`.
  These come from `THEME_CSS` in `packages/design-system/src/index.ts`.
- The pre-styled form controls in the sandbox, which follow `FORM_STYLES_CSS`.
- The generated-UI frame pattern in `sandbox-host.js` and `sandbox-runtime.js`: a ResizeObserver
  height report, clamped by the host, with a short transition. This follows
  `apps/app/src/components/generative-ui/open-generative-ui/frame-content.ts` and `renderer.tsx`.

The colour values, the MessageChannel bridge, the confirmation chips and the navigation guard are
our own.

```
The MIT License

Copyright (c) Atai Barkai

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## AG-UI protocol

`agui.js` consumes the AG-UI 1.0 event format (https://docs.ag-ui.com). It is an independent
implementation; no AG-UI SDK code is included.

```
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## three.js r180 (`vendor/three/`)

- **Package:** npm `three@0.180.0` (r180), published 2025-09-03.
- **Tarball:** https://registry.npmjs.org/three/-/three-0.180.0.tgz
- **Integrity:** `sha512-o+qycAMZrh+TsE01GqWUxUIKR1AL0S8pq7zDkYOQw8GqfX8b8VoCKYUoHbhiX5j+7hr8XsuHDVU6+gkQJQKg9w==`.
  This is the registry's `dist.integrity`, and it was verified against the downloaded tarball before
  extraction. The tarball's sha1 `b930cabfb524f6d36bf63e874b4d866888b50487` also matches `dist.shasum`.
- **Files copied unmodified:**
  - `build/three.module.min.js`, which imports `./three.core.min.js`
  - `build/three.core.min.js`
  - `examples/jsm/controls/OrbitControls.js`, which imports the bare specifier `three`; the sandbox
    import map resolves it
  - `LICENSE`

The full licence text is in `vendor/three/LICENSE`:

```
The MIT License

Copyright © 2010-2025 three.js authors
```
