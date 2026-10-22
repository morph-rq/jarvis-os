#version 440
// The Jarvis orb: a dark core with two eyes inside luminous strands of light that drift, fold and
// interweave. Original work for Jarvis WorkOS (MIT). Every input is a uniform driven by real jarvisd
// state in JarvisOrb.qml; this shader is a pure function of them and of `time`.
//
// Layout of a strand: a closed curve r = 1 + f(θ, t) around the core, where f is a few slow
// harmonics, two travelling folds (the "kinks" of the reference), a thinking weave, and the voice:
// the FFT bands mapped around the circle. Each strand is drawn as a crisp anti-aliased line plus an
// exponential glow, coloured along its length between cyan and violet, and summed additively with
// a soft tone curve so crossings bloom to white without clipping.
//
// Compile: /usr/lib/qt6/bin/qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o orb.frag.qsb orb.frag

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;        // phase, seconds scaled by the state's speed (advanced in QML)
    float level;       // smoothed voice level 0..1 (microphone or Jarvis's voice)
    float energy;      // overall brightness of the strands 0..1.5
    float deform;      // how far the strands bend 0..1.5
    float twist;       // thinking: strands weave around each other 0..1
    float orbit;       // working: a comet of light travelling the ring 0..1
    float contract;    // 0 awake .. 1 collapsed to a small blob (sleep / hidden)
    float pulse;       // success: brief expansion 0..1
    float attention;   // approval: steady, restrained throb 0..1
    float eyeOpen;     // 0 closed .. 1 open (blinks, sleepy)
    float eyeHappy;    // 0 round eyes .. 1 happy arcs
    float eyeVisible;  // 0..1
    float detail;      // 0 tiny (island pill) .. 1 large (bolder lines when small)
    float pxSize;      // item size in device pixels, for anti-aliasing
    float strands;     // strands to draw, 2..5
    vec2 look;         // where the eyes look, -1..1
    vec4 bandsA;       // 8 spectrum bands 0..1
    vec4 bandsB;
    vec4 colorA;       // strand colour one end (electric blue)
    vec4 colorB;       // strand colour other end (violet)
    vec4 highlight;    // the hottest parts of a strand (cyan)
    vec4 tint;         // state tint; tint.a = how much it takes over
};

const float PI = 3.14159265;
const float TAU = 6.28318531;

float angDist(float a, float b) {
    return mod(a - b + PI, TAU) - PI;
}

// Spectrum around the circle, mirrored so the curve stays closed and symmetric-ish.
float band(float u) {
    float x = abs(fract(u) * 2.0 - 1.0) * 7.0;
    int i = int(floor(x));
    float f = smoothstep(0.0, 1.0, fract(x));
    float b[8] = float[8](bandsA.x, bandsA.y, bandsA.z, bandsA.w, bandsB.x, bandsB.y, bandsB.z, bandsB.w);
    return mix(b[i], b[min(i + 1, 7)], f);
}

float strandRadius(float th, float k, float t) {
    float ph = k * 2.39996;                 // golden angle: strands never line up
    float d = deform * (0.65 + 0.35 * sin(t * 0.37 + ph));
    float r = 1.0;
    r += 0.035 * (k - 2.0) * (0.5 + 0.5 * d);                 // strands fan out a little
    r += d * 0.050 * sin(2.0 * th + t * 0.61 + ph);
    r += d * 0.034 * sin(3.0 * th - t * 0.83 + ph * 1.7);
    r += d * 0.018 * sin(5.0 * th + t * 1.13 + ph * 0.6);
    // Two folds travel around the ring in opposite directions: the inward pinch and the outward lobe.
    float g1 = angDist(th, t * 0.23 + ph * 1.3);
    r -= d * 0.17 * exp(-g1 * g1 * 16.0) * (0.55 + 0.45 * sin(t * 0.5 + ph));
    float g2 = angDist(th, -t * 0.17 + ph * 2.1 + 2.0);
    r += d * 0.08 * exp(-g2 * g2 * 9.0) * sin(t * 0.7 + ph * 0.5);
    // Thinking: neighbouring strands cross over each other.
    r += twist * 0.055 * sin(4.0 * th + t * 2.1 + ph * 3.0);
    // Voice: the spectrum pushes the strands out, with a ripple that runs around the ring.
    float b = band(th / TAU + k * 0.07 + t * 0.02);
    r += level * (0.045 + 0.13 * b) * (0.75 + 0.25 * sin(6.0 * th - t * 3.0 + ph));
    // Approval: all strands breathe together, slowly.
    r += attention * 0.025 * sin(t * 2.4);
    return r;
}

// Signed distance to one eye: a soft vertical capsule that morphs into a happy arc.
float eyeDist(vec2 q, vec2 c, float px) {
    vec2 h = vec2(0.062, max(0.012, 0.118 * eyeOpen));
    vec2 e = (q - c) / h;
    float round = (length(e) - 1.0) * min(h.x, h.y);
    // Happy: an upward arc (the top of a circle below the eye centre).
    vec2 a = q - c - vec2(0.0, 0.07);
    float arc = abs(length(a) - 0.085) - 0.022;
    arc = max(arc, a.y + 0.01);              // keep the top half only
    return mix(round, arc, eyeHappy);
}

void main() {
    vec2 p = (qt_TexCoord0 - 0.5) * 2.0;    // -1..1 across the item
    float R = mix(0.62, 0.07, contract) * (1.0 + 0.07 * pulse);
    vec2 q = p / R;                          // ring base radius = 1
    float r = length(q);
    float th = atan(q.y, q.x);
    float px = 2.0 / max(pxSize * R, 1.0);   // one device pixel in q units

    // ------------------------------------------------------------ strands
    vec3 rings = vec3(0.0);
    float lineW = mix(0.05, 0.011, detail);  // half width: bolder when the orb is tiny
    float glowW = mix(0.16, 0.07, detail) * (0.8 + 0.4 * energy);
    for (int i = 0; i < 5; i++) {
        float k = float(i);
        float present = clamp(strands - k, 0.0, 1.0);
        if (present <= 0.0) break;
        // Each strand sees the plane through its own slow domain warp (three rotated sine layers), so
        // the strands twist and cross like ribbons instead of staying concentric.
        vec2 wq = q;
        float f = 1.7;
        float amp = (0.10 + 0.05 * twist) * deform;
        mat2 rot = mat2(0.8, -0.6, 0.6, 0.8);
        for (int j = 0; j < 3; j++) {
            float fj = float(j);
            vec2 s = sin(f * (rot * wq) + vec2(time * (0.55 + 0.21 * fj) + k * 1.9, time * (0.43 - 0.17 * fj) + k * 2.7));
            wq += (amp / f) * vec2(s.y, s.x);
            rot = rot * mat2(0.6, -0.8, 0.8, 0.6);
            f *= 1.55;
        }
        float wr = length(wq);
        float wth = atan(wq.y, wq.x);
        float rr = strandRadius(wth, k, time);
        float d = abs(wr - rr);
        float dir = mod(k, 2.0) * 2.0 - 1.0;
        float s = 0.5 + 0.5 * sin(th * (1.0 + mod(k, 2.0)) + dir * time * (0.35 + 0.12 * k) + k * 1.7);
        float bright = mix(0.22, 1.0, s * s);
        float w = lineW * (0.6 + 0.6 * s) + px * 0.6;
        float core = 1.0 - smoothstep(w * 0.35, w + px, d);
        float glow = exp(-d / glowW) * 0.34 + 0.0055 / (d + 0.02);
        float hue = 0.5 + 0.5 * sin(th + k * 1.3 + time * 0.21);
        vec3 c = mix(colorA.rgb, colorB.rgb, hue);
        c = mix(c, highlight.rgb, s * s * s * s * 0.65);
        c = mix(c, tint.rgb, tint.a);
        rings += c * (core * 1.25 + glow) * bright * present;
    }

    // Working: a comet of light runs around the ring with a fading tail.
    if (orbit > 0.0) {
        float head = time * 1.9;
        float g = mod(head - th, TAU);                       // distance behind the head
        float tail = exp(-g * 2.2) * smoothstep(0.0, 0.05, g + 0.05);
        float rr = strandRadius(head, 0.0, time) * 1.08;
        float d = abs(r - rr);
        rings += mix(vec3(0.9, 0.97, 1.0), tint.rgb, tint.a * 0.5) * tail * exp(-d / (glowW * 0.6)) * orbit * 0.9;
    }

    rings *= energy;
    rings = vec3(1.0) - exp(-rings * 1.05);                 // soft tone curve: overlaps bloom to white
    rings *= smoothstep(1.0, 0.82, length(p));              // never clip at the item's edge

    // ------------------------------------------------------------ core
    float coreR = 0.7;
    float coreMask = 1.0 - smoothstep(coreR - px, coreR + px, r);
    vec3 core = mix(vec3(0.006, 0.007, 0.022), vec3(0.025, 0.03, 0.085), smoothstep(0.0, coreR, r));
    vec3 rim = mix(colorA.rgb, colorB.rgb, 0.5 + 0.5 * sin(th + time * 0.3));
    rim = mix(rim, tint.rgb, tint.a);
    core += rim * pow(smoothstep(coreR * 0.5, coreR, r), 3.0) * 0.38 * energy;

    // ------------------------------------------------------------ eyes
    vec3 eyes = vec3(0.0);
    if (eyeVisible > 0.0) {
        vec2 off = look * vec2(0.075, 0.06);
        float de = min(eyeDist(q, vec2(-0.2, 0.0) + off, px), eyeDist(q, vec2(0.2, 0.0) + off, px));
        float body = 1.0 - smoothstep(-px, px, de);
        float halo = exp(-max(de, 0.0) / 0.05) * 0.45;
        vec3 ec = mix(vec3(0.86, 0.93, 1.0), tint.rgb, tint.a * 0.35);
        eyes = ec * (body + halo) * eyeVisible * coreMask;
    }

    vec3 rgb = core * coreMask + eyes + rings;
    float a = clamp(max(coreMask, max(rings.r, max(rings.g, rings.b))), 0.0, 1.0);
    rgb = min(rgb, vec3(a));                                  // premultiplied
    fragColor = vec4(rgb, a) * qt_Opacity;
}
